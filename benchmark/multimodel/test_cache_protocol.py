"""Offline protocol invariants; no model or HTTP calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import cache_protocol as cp


def specs():
    return [
        {"label": label, "id": "test/" + label, "provider": "TestProvider", "prices": {},
         "cache_control": "explicit" if label == "Luna" else "unsupported"}
        for label in ("Jev", "Luna", "Gemini 3.8 Flash", "GLM 5.3 Flash", "Qwen3.8 Flash")]


class Adapter:
    def __init__(self, missing=None, invalid=None, reserve=.001):
        self.calls = []
        self.missing = set(missing or [])
        self.invalid = set(invalid or [])
        self.reserve = reserve

    def build_request(self, case, spec, cache_mode, cache_key):
        if spec["label"] == "Jev":
            return {"endpoint": "/alpha", "payload": {"model": spec["id"], **cp.shared_input(case)}}
        payload = {"model": spec["id"], "messages": [
            {"role": "system", "content": "Use the single answer schema."},
            {"role": "user", "content": cp.text_blocks(case)}], "max_tokens": 128,
            "response_format": {"type": "json_schema", "schema": "same"}}
        if cache_mode != "baseline":
            payload.update(prompt_cache_options={"mode": "explicit", "ttl": "30m"}, prompt_cache_key=cache_key)
        if cache_mode == "cached":
            payload["messages"][1]["content"][0]["prompt_cache_breakpoint"] = {"mode": "explicit"}
        return {"endpoint": "/chat", "payload": payload}

    def reserve_usd(self, request, spec):
        return self.reserve

    def run(self, *, case, spec, arm_label, phase, sequence, endpoint, payload):
        self.calls.append((case["id"], arm_label, sequence))
        counts = {}
        if spec["cache_control"] == "explicit":
            is_on = arm_label.endswith(" cached")
            counts = {"cached_tokens": 0 if phase == "cache_prime" or not is_on else 2800,
                      "cache_write_tokens": 2800 if phase == "cache_prime" else 0}
        return {"valid": sequence not in self.invalid, "probability": case["target"],
                "cost_usd": None if sequence in self.missing else .0001,
                "latency_s": .3, "provider": spec["provider"],
                "usage": {"prompt_tokens_details": counts}}


class CacheProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.primes, cls.rulebook, cls.source_hashes = cp.load_source()

    def run_protocol(self, adapter, budget=2, model_specs=None):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        out = Path(self.temp.name) / "run"
        result = cp.run_cache_protocol(adapter, out, model_specs or specs(), budget)
        return result, out

    def test_source_integrity_pair_structure_and_plan_fairness(self):
        before = deepcopy(self.cases)
        plan, prepared = cp.create_plan(Adapter(), specs(), self.cases, self.primes,
                                       self.source_hashes, 17, ["a", "b", "c", "d"])
        self.assertEqual(self.cases, before)
        self.assertEqual(len(plan["attempts"]), 580)
        self.assertEqual(sum(p["phase"] == "cache_prime" for p in plan["attempts"]), 4)
        for block in range(4):
            entries = [p for p in plan["attempts"] if p["block"] == block]
            self.assertEqual(len(entries), 145)
            self.assertEqual(len({p["prefix_sha256"] for p in entries}), 1)
            self.assertEqual(entries[0]["phase"], "cache_prime")
        self.assertTrue(all(p["reserve_usd"] == .01 for p in plan["attempts"]))
        for entry in plan["attempts"]:
            case, spec, request = prepared[entry["sequence"]]
            if entry["phase"] == "measured":
                original = next(c for c in self.cases if c["id"] == case["id"])
                self.assertEqual(case["state"]["facts"], original["state"]["facts"])
                self.assertEqual(case["target"], original["target"])
            if spec["cache_control"] == "unsupported":
                self.assertEqual(entry["model_label"], spec["label"])
                self.assertEqual(entry["cache_mode"], "baseline")

    def test_unfair_text_and_unknown_cache_capability_rejected_before_calls(self):
        class Unfair(Adapter):
            def build_request(self, case, spec, cache_mode, cache_key):
                r = super().build_request(case, spec, cache_mode, cache_key)
                if cache_mode == "cached":
                    r["payload"]["messages"][1]["content"][1]["text"] += " extra hint"
                return r
        adapter = Unfair()
        with self.assertRaisesRegex(ValueError, "same two text blocks"):
            cp.create_plan(adapter, specs(), self.cases, self.primes, self.source_hashes, 17)
        self.assertEqual(adapter.calls, [])
        bad = specs()
        bad[0]["cache_control"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown is not runnable"):
            cp.validate_specs(bad)

    def test_unsupported_baseline_cannot_claim_explicit_disabled_cache(self):
        class FalseControl(Adapter):
            def build_request(self, case, spec, cache_mode, cache_key):
                r = super().build_request(case, spec, cache_mode, cache_key)
                if spec["label"] == "Gemini 3.8 Flash":
                    r["payload"]["prompt_cache_options"] = {"mode": "explicit"}
                return r
        with self.assertRaisesRegex(ValueError, "Unsupported baseline"):
            cp.create_plan(FalseControl(), specs(), self.cases, self.primes, self.source_hashes, 17)

    def test_complete_run_verifies_counters_and_accounts_for_primes(self):
        adapter = Adapter()
        result, out = self.run_protocol(adapter)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(len(adapter.calls), 580)
        self.assertEqual(len(set(adapter.calls)), 580)
        self.assertEqual(cp.digest((out / "cases.jsonl").read_bytes()), cp.SOURCE_CASES_SHA256)
        summary = result["summary"]
        verify = summary["cache_verification"]["Luna"]
        self.assertEqual(verify["status"], "verified")
        self.assertEqual(verify["prime_count"], 4)
        self.assertAlmostEqual(verify["setup_inclusive_cached_cost_usd"], .01)
        self.assertEqual(summary["cache_verification"]["Gemini 3.8 Flash"]["status"], "unsupported")
        self.assertIsNone(summary["arms"]["Gemini 3.8 Flash"]["cache_read_tokens"])
        self.assertAlmostEqual(result["metadata"]["charged_usd"], .058)

    def test_missing_bill_preserved_without_retry_or_imputed_price(self):
        adapter = Adapter(missing=[5], invalid=[5])
        result, out = self.run_protocol(adapter)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(len(adapter.calls), 580)
        rows = [json.loads(s) for s in (out / "responses.jsonl").read_text().splitlines()]
        self.assertFalse(rows[5]["valid"])
        self.assertIsNone(rows[5]["probability"])
        self.assertIsNone(rows[5]["cost_usd"])
        self.assertIsNone(result["metadata"]["charged_usd"])
        self.assertEqual(result["metadata"]["cost_missing_n"], 1)
        self.assertAlmostEqual(result["metadata"]["known_charge_subtotal_usd"], .0579)
        self.assertAlmostEqual(result["metadata"]["budget_accounted_usd"], .0679)
        self.assertEqual(sum(s["invalid_n"] for s in result["summary"]["arms"].values()), 1)

    def test_budget_reserves_unknown_allowance_before_each_attempt(self):
        adapter = Adapter(missing=[0])
        result, _ = self.run_protocol(adapter, budget=.015)
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(result["metadata"]["status"], "stopped")
        self.assertIn("Budget guard", result["metadata"]["error"])
        self.assertEqual(result["metadata"]["budget_accounted_usd"], .01)
        self.assertIsNone(result["metadata"]["charged_usd"])

    def test_failure_circuit_keeps_attempts_and_does_not_retry(self):
        adapter = Adapter(invalid=range(5))
        result, _ = self.run_protocol(adapter)
        self.assertEqual(len(adapter.calls), 5)
        self.assertEqual(result["metadata"]["status"], "stopped")
        self.assertIn("Five consecutive", result["metadata"]["error"])
        rows = [{"valid": True, "probability": 1} for _ in range(100)]
        for i in (1, 3, 8, 13, 41):
            rows[i]["valid"] = False
        self.assertIn("5%", cp.circuit_reason(rows))

    def test_resume_rejects_partial_block_without_new_attempts(self):
        adapter = Adapter(missing=[0])
        result, out = self.run_protocol(adapter, budget=.015)
        original = (out / "responses.jsonl").read_bytes()
        with self.assertRaisesRegex(ValueError, "complete-block boundaries"):
            cp.run_cache_protocol(Adapter(), out, specs(), 2, resume=True)
        self.assertEqual((out / "responses.jsonl").read_bytes(), original)

    def test_dispatch_journal_prevents_replay_of_unknown_outcome(self):
        class Interrupted(Adapter):
            def run(self, **kwargs):
                raise KeyboardInterrupt("Simulated process interruption after dispatch")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name) / "run"
        with self.assertRaises(KeyboardInterrupt):
            cp.run_cache_protocol(Interrupted(), out, specs(), 2)
        self.assertEqual(len((out / "dispatches.jsonl").read_text().splitlines()), 1)
        self.assertEqual((out / "responses.jsonl").read_text(), "")
        metadata = json.loads((out / "metadata.json").read_text())
        self.assertEqual(metadata["unresolved_dispatch_n"], 1)
        self.assertIsNone(metadata["charged_usd"])
        self.assertEqual(metadata["budget_accounted_usd"], .01)
        self.assertEqual(metadata["status"], "stopped")
        adapter = Adapter()
        with self.assertRaisesRegex(ValueError, "unknown outcome must not be replayed"):
            cp.run_cache_protocol(adapter, out, specs(), 2, resume=True)
        self.assertEqual(adapter.calls, [])

    def test_provider_display_name_is_distinct_from_route(self):
        class DisplayAdapter(Adapter):
            def run(self, **kwargs):
                row = super().run(**kwargs)
                row["provider"] = kwargs["spec"]["provider_name"]
                return row
        model_specs = specs()[:1]
        model_specs[0].update(provider="provider/route", provider_name="Provider display")
        result, _ = self.run_protocol(DisplayAdapter(), model_specs=model_specs)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(result["summary"]["arms"]["Jev"]["valid_n"], 96)


if __name__ == "__main__":
    unittest.main()
