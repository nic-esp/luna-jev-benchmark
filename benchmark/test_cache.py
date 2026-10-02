"""Offline checks for cache-control comparability, policy truth, and honest reporting."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cache_experiment as cache
from runner import shared_input


class CacheTests(unittest.TestCase):
    def test_cases_balanced_unique_and_frozen_targets(self):
        rows = cache.cases()
        self.assertEqual(len(rows), 24)
        self.assertEqual(sum(c["target"] for c in rows), 12)
        self.assertEqual(len({c["id"] for c in rows}), 24)
        self.assertNotIn(cache.prime_case()["id"], {c["id"] for c in rows})
        for case in rows:
            self.assertEqual(cache.policy_truth(case["state"]["facts"]), case["target"])
        self.assertGreater(len(cache.RULEBOOK.split()), 1800)

    def test_luna_arms_only_differ_in_breakpoint(self):
        for case in cache.cases():
            _, on = cache.cache_payload(case, "Luna cached", "same-key")
            _, off = cache.cache_payload(case, "Luna uncached", "same-key")
            self.assertEqual(on["messages"][1]["content"][0].pop("prompt_cache_breakpoint"), {"mode": "explicit"})
            self.assertEqual(on, off)
            self.assertEqual(off["prompt_cache_options"]["mode"], "explicit")
            text = "".join(b["text"] for b in off["messages"][1]["content"])
            self.assertEqual(json.loads(text), shared_input(case))
            endpoint, jev = cache.cache_payload(case, "Jev", "same-key")
            self.assertEqual(endpoint, "/api/alpha/decisions")
            self.assertEqual({k: v for k, v in jev.items() if k != "model"}, shared_input(case))

    def test_prefix_fixed_suffix_varies(self):
        payloads = [cache.cache_payload(c, "Luna cached", "key")[1] for c in cache.cases() + [cache.prime_case()]]
        self.assertEqual(len({p["messages"][1]["content"][0]["text"] for p in payloads}), 1)
        self.assertEqual(len({p["messages"][1]["content"][1]["text"] for p in payloads}), 25)

    def test_policy_boundaries_and_exceptions(self):
        for category, (duration, battery, distance, low, high) in cache.CATALOG.items():
            good = dict(cache.BASE, category=category, duration_hours=duration, battery_percent=battery, transport_distance_m=distance, environment_temperature_c=low)
            self.assertEqual(cache.policy_truth(good), 1, category)
            for field, value in [("duration_hours", duration + .01), ("transport_distance_m", distance + .01), ("environment_temperature_c", low - .01), ("environment_temperature_c", high + .01), ("battery_percent", 101)]:
                self.assertEqual(cache.policy_truth(dict(good, **{field: value})), 0, (category, field))
        for field in ("membership_active", "badge_active", "destination_approved"):
            self.assertEqual(cache.policy_truth(dict(cache.BASE, **{field: False})), 0)
        self.assertEqual(cache.policy_truth(dict(cache.BASE, requester_role="visitor", supervisor_active=True, supervisor_present=True, supervisor_category_endorsed=True)), 0)
        self.assertEqual(cache.policy_truth(dict(cache.BASE, release_tag="amber", emergency=True, duty_manager_signed=True, known_damage=True)), 0)
        self.assertEqual(cache.policy_truth(dict(cache.BASE, notice_hours=0, duty_manager_signed=True)), 0)
        self.assertEqual(cache.policy_truth(dict(cache.BASE, notice_hours=0, duty_manager_signed=True, emergency=True)), 1)

    def rows(self, cached=2000, control=0, missing=False):
        rows = []
        for sequence, arm in enumerate(("Luna cached",) + cache.ARMS):
            hit = 0 if sequence == 0 else cached if arm == "Luna cached" else control
            rows.append({"case_id": "prime" if sequence == 0 else "one", "experiment": "cache_policy", "target": 1, "target_kind": "label",
                         "model_label": arm, "phase": "cache_prime" if sequence == 0 else "measured", "sequence": sequence,
                         "valid": True, "probability": 1.0, "error": None, "cost_usd": .01 if sequence == 0 else .002,
                         "latency_s": 1.0, "usage": {"prompt_tokens": 3000, "prompt_tokens_details": {} if missing else {"cached_tokens": hit, "cache_write_tokens": 2500 if sequence == 0 else 0}}})
        return rows

    def test_cache_claims_require_hits_and_clean_control(self):
        meta = {"status": "complete"}
        verified = cache.summarise(self.rows(), meta)
        self.assertTrue(verified["cache_verification"]["controlled_cache_comparison_verified"])
        self.assertAlmostEqual(verified["cache_verification"]["setup_inclusive_cached_cost_usd"], .012)
        self.assertEqual(verified["arms"]["Luna cached"]["n"], 1)
        for rows in (self.rows(cached=0), self.rows(control=20), self.rows(missing=True)):
            report = cache.summarise(rows, meta)
            self.assertFalse(report["cache_verification"]["controlled_cache_comparison_verified"])
        empty = cache.summarise([], {"status": "stopped"})
        self.assertFalse(empty["cache_verification"]["controlled_cache_comparison_verified"])

    def test_missing_cost_and_cache_counters_are_unknown(self):
        rows = self.rows()
        rows[0]["cost_usd"] = None
        summary = cache.summarise(rows, {"status": "stopped"})
        self.assertIsNone(summary["cache_verification"]["setup_inclusive_cached_cost_usd"])
        rows = self.rows()
        rows[1]["cost_usd"] = None
        summary = cache.summarise(rows, {"status": "stopped"})
        self.assertIsNone(summary["arms"]["Luna cached"]["total_cost_usd"])
        self.assertIsNone(summary["arms"]["Luna cached"]["mean_cost_usd"])
        self.assertEqual(summary["arms"]["Luna cached"]["cost_missing_n"], 1)
        self.assertIsNone(summary["cache_verification"]["setup_inclusive_cached_cost_usd"])
        summary = cache.summarise(self.rows(missing=True), {"status": "stopped"})
        for arm in cache.ARMS:
            self.assertIsNone(summary["arms"][arm]["cache_read_tokens"])
            self.assertIsNone(summary["arms"][arm]["cache_write_tokens"])

    def test_mock_run_separates_prime_and_respects_missing_cost(self):
        class FakeClient:
            def __init__(self, key):
                self.key = key
            def close(self):
                pass
            def run(self, case, arm, phase, sequence, **kwargs):
                return {"case_id": case["id"], "experiment": case["experiment"], "target": case["target"], "target_kind": "label", "model_label": arm,
                        "phase": phase, "sequence": sequence, "valid": True, "probability": float(case["target"]), "error": None,
                        "cost_usd": .0001, "latency_s": 1, "usage": {"prompt_tokens": 3500, "prompt_tokens_details": {"cached_tokens": 3000 if arm == "Luna cached" and phase == "measured" else 0, "cache_write_tokens": 3000 if phase == "cache_prime" else 0}}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(cache, "Client", FakeClient):
            result = cache.run_cache("sk-or-fake", Path(tmp) / "run", budget=1, progress=lambda update: None)
            directory, meta = Path(result["run_dir"]), result["metadata"]
            self.assertEqual(meta["status"], "complete")
            self.assertEqual(meta["calls_completed"], 73)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertEqual(summary["arms"]["Luna cached"]["n"], 24)
            self.assertTrue(summary["cache_verification"]["controlled_cache_comparison_verified"])
            self.assertTrue((directory / "report.html").exists())
            self.assertNotIn("sk-or-fake", (directory / "responses.jsonl").read_text())
        with tempfile.TemporaryDirectory() as tmp, patch.object(cache, "Client", FakeClient):
            meta = cache.run_cache("sk-or-fake", Path(tmp) / "run", budget=.00001, progress=lambda update: None)["metadata"]
            self.assertEqual(meta["status"], "stopped")
            self.assertEqual(meta["calls_completed"], 0)
        class UnbilledClient(FakeClient):
            def run(self, *args, **kwargs):
                row = super().run(*args, **kwargs)
                row["cost_usd"] = None
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(cache, "Client", UnbilledClient):
            result = cache.run_cache("sk-or-fake", Path(tmp) / "run", budget=1, progress=lambda update: None)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 1)
            self.assertIsNone(result["summary"]["cache_verification"]["setup_inclusive_cached_cost_usd"])


if __name__ == "__main__":
    unittest.main()
