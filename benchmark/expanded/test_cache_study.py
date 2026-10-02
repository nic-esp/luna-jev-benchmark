"""Offline protocol and accounting tests; never call an API."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cache_study as study
from runner import shared_input


class FakeClient:
    def __init__(self, key):
        pass
    def close(self):
        pass
    def run(self, case, arm, phase, sequence, **kwargs):
        return {"case_id": case["id"], "experiment": case["experiment"], "target": case["target"],
                "target_kind": "label", "model_label": arm, "phase": phase, "sequence": sequence,
                "valid": True, "probability": float(case["target"]), "error": None,
                "provider": "TypeSafe" if arm == "Jev" else "OpenAI", "response_model": study.MODELS["Jev" if arm == "Jev" else "Luna"],
                "cost_usd": .0001, "latency_s": 1.0,
                "usage": {"prompt_tokens_details": {"cached_tokens": 2500 if arm == "Luna cached" and phase == "measured" else 0,
                                                   "cache_write_tokens": 2500 if phase == "cache_prime" else 0}}}


class CacheStudyTests(unittest.TestCase):
    def test_exact_labels_counterfactual_pairs_and_block_balance(self):
        data = study.dataset()
        self.assertEqual(len(data), 96)
        self.assertEqual(len({json.dumps(r["state"]["facts"], sort_keys=True) for r in data}), 96)
        for block in range(4):
            rows = [r for r in data if r["block"] == block]
            self.assertEqual(len(rows), 24)
            self.assertEqual(sum(r["target"] for r in rows), 12)
        for i in range(0, len(data), 2):
            yes, no = data[i:i + 2]
            self.assertEqual((yes["target"], no["target"]), (1, 0))
            self.assertEqual(yes["block"], no["block"])
            differing = [k for k in yes["state"]["facts"] if yes["state"]["facts"][k] != no["state"]["facts"][k]]
            self.assertEqual(differing, [no["source"]["changed_fact"]])
            self.assertEqual(study.failed_rules(yes["state"]["facts"]), [])
            self.assertEqual(study.failed_rules(no["state"]["facts"]), no["source"]["expected_failed_rules"])
            self.assertEqual(study.policy_truth(no["state"]["facts"]), 0)

    def test_cache_and_control_same_content_and_unique_prefixes(self):
        data = study.dataset()
        all_prefixes = []
        for block in range(4):
            prefix_set = set()
            for c in [x for x in data if x["block"] == block]:
                case = study.with_prefix(c, "distinct-prefix-" + str(block))
                _, on = study.cache_payload(case, "Luna cached", "key")
                _, off = study.cache_payload(case, "Luna uncached", "key")
                self.assertEqual(on["messages"][1]["content"][0].pop("prompt_cache_breakpoint"), {"mode": "explicit"})
                self.assertEqual(on, off)
                content = off["messages"][1]["content"]
                prefix_set.add(content[0]["text"])
                self.assertEqual(json.loads("".join(b["text"] for b in content)), shared_input(case))
                _, jev = study.cache_payload(case, "Jev", "key")
                self.assertEqual({k: v for k, v in jev.items() if k != "model"}, shared_input(case))
            self.assertEqual(len(prefix_set), 1)
            all_prefixes.extend(prefix_set)
        self.assertEqual(len(set(all_prefixes)), 4)

    def test_complete_run_has_four_cold_primes_and_288_measured(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(study, "Client", FakeClient):
            result = study.run_cache_study("sk-or-fake", Path(tmp) / "run", 1, progress=lambda _: None)
            self.assertEqual(result["metadata"]["status"], "complete")
            self.assertEqual(result["metadata"]["calls_completed"], 292)
            self.assertEqual(result["metadata"]["completed_blocks"], [0, 1, 2, 3])
            self.assertTrue(result["summary"]["cache_verification"]["fully_verified"])
            self.assertAlmostEqual(result["summary"]["cache_verification"]["setup_inclusive_cached_cost_usd"], .0100)
            self.assertEqual(result["summary"]["arms"]["Luna cached"]["n"], 96)
            self.assertNotIn("sk-or-fake", (Path(tmp) / "run" / "responses.jsonl").read_text())

    def test_missing_bill_stops_and_never_becomes_zero_total(self):
        class MissingBill(FakeClient):
            def run(self, *a, **kw):
                row = super().run(*a, **kw)
                row["cost_usd"] = None
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study, "Client", MissingBill):
            result = study.run_cache_study("sk-or-fake", Path(tmp) / "run", 1, progress=lambda _: None)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 1)
            self.assertIsNone(result["metadata"]["charged_usd"])
            self.assertEqual(result["metadata"]["cost_missing_n"], 1)
            self.assertIsNone(result["summary"]["total_cost"]["total_usd"])
            self.assertIsNone(result["summary"]["cache_verification"]["setup_inclusive_cached_cost_usd"])

    def test_budget_stop_and_partial_block_resume_refusal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(study, "Client", FakeClient):
            directory = Path(tmp) / "run"
            result = study.run_cache_study("sk-or-fake", directory, .0001, progress=lambda _: None)
            self.assertEqual(result["metadata"]["calls_completed"], 0)
            self.assertEqual(result["metadata"]["status"], "stopped")
            resumed = study.run_cache_study("sk-or-fake", directory, 1, progress=lambda _: None, resume=True)
            self.assertEqual(resumed["metadata"]["status"], "complete")
            raw = directory / "responses.jsonl"
            raw.write_text(raw.read_text().splitlines()[0] + "\n")
            with self.assertRaisesRegex(ValueError, "complete 73-request"):
                study.run_cache_study("sk-or-fake", directory, 1, progress=lambda _: None, resume=True)

    def test_warm_prime_and_missing_cache_counters_not_verified(self):
        class Unverified(FakeClient):
            def run(self, case, arm, phase, sequence, **kwargs):
                row = super().run(case, arm, phase, sequence, **kwargs)
                if phase == "cache_prime":
                    row["usage"]["prompt_tokens_details"]["cached_tokens"] = 100
                if arm == "Luna uncached":
                    row["usage"]["prompt_tokens_details"] = {}
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study, "Client", Unverified):
            result = study.run_cache_study("sk-or-fake", Path(tmp) / "run", 1, progress=lambda _: None)
            self.assertFalse(result["summary"]["cache_verification"]["fully_verified"])
            self.assertEqual(result["summary"]["cache_verification"]["cold_primed_blocks"], 0)
            self.assertIsNone(result["summary"]["arms"]["Luna uncached"]["cache_read_tokens"])


if __name__ == "__main__":
    unittest.main()
