"""Offline cache-verifier mutation tests. No credential or network use."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import adapter
import cache_protocol as protocol
import verify_cache as audit


SPECS = [
    {"label": label, "id": model, "provider": provider, "provider_name": provider,
     "prices": {"prompt": "0.0000001", "completion": "0.0000004"},
     "reasoning": None if label == "Jev" else {"effort": "none"},
     "cache_control": "explicit" if label == "Luna" else "unsupported"}
    for label, model, provider in (("Jev", "typesafe/jev-1.13", "TypeSafe"),
                                  ("Luna", "openai/gpt-6-luna", "OpenAI"),
                                  ("Qwen3.8 Flash", "qwen/qwen3.8-flash", "Alibaba"))]


class FakeAdapter(adapter.Adapter):
    def __init__(self):
        # No credential, TLS connection, or HTTP client is initialized.
        pass

    def run(self, *, case, spec, arm_label, phase, sequence, endpoint, payload):
        answer = {"type": "noul", "noul": case["target"]}
        usage = {"cost": .0001, "prompt_tokens": 3000, "completion_tokens": 18,
                 "completion_tokens_details": {"reasoning_tokens": 0}}
        if spec["label"] == "Luna":
            usage["prompt_tokens_details"] = {"cached_tokens": 2600 if phase == "measured" and arm_label.endswith(" cached") else 0,
                                               "cache_write_tokens": 2600 if phase == "cache_prime" else 0}
        elif spec["label"] == "Qwen3.8 Flash":
            usage["prompt_tokens_details"] = {"cached_tokens": 2048, "cache_write_tokens": 0}
        response = {"model": spec["id"], "provider": spec["provider_name"], "usage": usage}
        if spec["label"] == "Jev":
            response["answers"] = {"answer": answer}
        else:
            response["choices"] = [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]
        return {"valid": True, "probability": case["target"], "error": None, "latency_s": .3,
                "response": response, "response_model": spec["id"], "provider": spec["provider_name"],
                "usage": usage, "cost_usd": .0001, "http_status": 200, "answer": answer,
                "finish_reason": "stop", "reported_reasoning_tokens": 0, "visible_reasoning": False,
                "reasoning_verified_disabled": True, "started_at": "2026-10-02T00:00:00Z"}


class CacheAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_temp = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_temp.name) / "complete"
        result = protocol.run_cache_protocol(FakeAdapter(), cls.fixture, SPECS, 2)
        assert result["metadata"]["status"] == "complete"

    @classmethod
    def tearDownClass(cls):
        cls.fixture_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name) / "run"
        shutil.copytree(self.fixture, self.run)

    def rows(self):
        return audit.read_rows(self.run / "responses.jsonl")

    def save_rows(self, rows):
        (self.run / "responses.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def test_complete_plan_and_response_verification_uses_raw_counters(self):
        result = audit.verify_cache(self.run)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["attempts"], 388)
        self.assertEqual(result["measured_attempts"], 384)
        self.assertEqual(result["prime_attempts"], 4)
        self.assertEqual(result["cache_treatment"]["Luna"]["status"], "verified")
        self.assertEqual(result["cache_treatment"]["Qwen3.8 Flash"]["status"], "unsupported")
        self.assertEqual(result["cache_treatment"]["Qwen3.8 Flash"]["positive_read_calls"], 96)
        self.assertTrue((self.run / "verification.json").exists())

    def test_one_missing_bill_remains_unknown_and_can_pass(self):
        rows = self.rows()
        rows[0]["cost_usd"] = None
        rows[0]["usage"]["cost"] = None
        rows[0]["response"]["usage"]["cost"] = None
        self.save_rows(rows)
        path = self.run / "metadata.json"
        meta = json.loads(path.read_text())
        known = sum(r["cost_usd"] for r in rows if r["cost_usd"] is not None)
        meta.update(charged_usd=None, known_charge_subtotal_usd=known, cost_missing_n=1,
                    unknown_bill_allowance_total_usd=.01, budget_accounted_usd=known + .01)
        path.write_text(json.dumps(meta))
        result = audit.verify_cache(self.run)
        self.assertTrue(result["passed"], result)
        self.assertIsNone(result["billing"]["actual_total_usd"])
        self.assertEqual(result["billing"]["missing_bills"], 1)

    def test_source_or_dispatch_mutation_is_detected(self):
        rows = self.rows()
        rows[0]["request"]["messages"][1]["content"][1]["text"] += " changed input"
        self.save_rows(rows)
        journal = audit.read_rows(self.run / "dispatches.jsonl")
        (self.run / "dispatches.jsonl").write_text("".join(json.dumps(r) + "\n" for r in journal[:-1]))
        result = audit.verify_cache(self.run)
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["dispatch_count_and_order"])
        self.assertTrue(any("actual_request_hash" in r["issues"] for r in result["issues"]))

    def test_unobserved_off_counter_fails_control_even_if_self_summary_claims_success(self):
        rows = self.rows()
        row = next(r for r in rows if r["model_label"] == "Luna uncached")
        for usage in (row["usage"], row["response"]["usage"]):
            usage["prompt_tokens_details"].pop("cached_tokens", None)
        row["cached_tokens"] = None
        self.save_rows(rows)
        result = audit.verify_cache(self.run)
        self.assertFalse(result["passed"])
        self.assertEqual(result["cache_treatment"]["Luna"]["status"], "failed")
        self.assertFalse(result["checks"]["every_controlled_cache_treatment_verified"])

    def test_reasoning_or_completion_cap_violation_is_detected(self):
        rows = self.rows()
        row = next(r for r in rows if r["model_label"] == "Qwen3.8 Flash")
        for usage in (row["usage"], row["response"]["usage"]):
            usage["completion_tokens_details"]["reasoning_tokens"] = 1
            usage["completion_tokens"] = 129
        self.save_rows(rows)
        result = audit.verify_cache(self.run)
        self.assertFalse(result["passed"])
        issues = next(r["issues"] for r in result["issues"] if r["model"] == "Qwen3.8 Flash")
        self.assertIn("observed_reasoning_tokens_nonzero_or_invalid", issues)
        self.assertIn("reported_completion_tokens_outside_cap", issues)

    def test_duplicate_json_answer_fields_are_not_silently_accepted(self):
        rows = self.rows()
        row = next(r for r in rows if r["model_label"] == "Qwen3.8 Flash" and r["target"] == 1)
        row["response"]["choices"][0]["message"]["content"] = '{"type":"noul","noul":0,"noul":1}'
        self.save_rows(rows)
        result = audit.verify_cache(self.run)
        self.assertFalse(result["passed"])
        self.assertTrue(any("answer_contains_duplicate_fields_or_unparseable_json" in r["issues"] for r in result["issues"]))


if __name__ == "__main__":
    unittest.main()
