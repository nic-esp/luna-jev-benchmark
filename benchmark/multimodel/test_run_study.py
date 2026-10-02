"""Offline protocol tests. The fake adapter cannot make HTTP requests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("study_under_test", ROOT / "run_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


SPECS = [{"label": name, "id": "test/" + name, "provider": "offline", "prices": {"prompt": 0},
          "reasoning": None if name == "Jev" else {"effort": "none"}, "cache_control": "unsupported"}
         for name in ("Jev", "A", "B")]


def cases(n=8):
    return [{"id": "case-" + str(i), "experiment": "policy", "state": {"n": i},
             "question": "Is the integer even?", "criteria": {"true": "It is even.", "false": "It is odd."},
             "target": int(i % 2 == 0), "target_kind": "label"} for i in range(n)]


class FakeAdapter:
    calls = []
    active = maximum = constructions = 0
    lock = threading.Lock()
    out = None
    behavior = None

    def __init__(self, key):
        self.key = key
        type(self).constructions += 1

    def close(self):
        pass

    def build_request(self, case, spec, cache_mode=None, cache_key=None):
        return {"endpoint": "/offline", "payload": {"model": spec["id"], **study.base.shared_input(case)}}

    def reserve_usd(self, request, spec):
        return .001

    def run(self, case, spec, arm_label, phase, sequence, endpoint, payload):
        cls = type(self)
        if cls.out:
            assert (cls.out / "plan.json").exists()
            assert (cls.out / "requests.jsonl").exists()
            dispatches = study.read_rows(cls.out / "attempts.jsonl")
            assert sequence in {r["sequence"] for r in dispatches}
        with cls.lock:
            cls.active += 1
            cls.maximum = max(cls.maximum, cls.active)
            cls.calls.append((sequence, case["id"], arm_label, phase))
        try:
            time.sleep(.001)
            if cls.behavior:
                return cls.behavior(case, spec, sequence)
            return {"valid": True, "probability": case["target"], "cost_usd": .001,
                    "usage": {}, "latency_s": .001, "provider": "offline", "request": payload,
                    "endpoint": endpoint, "response_model": spec["id"]}
        finally:
            with cls.lock:
                cls.active -= 1


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "run"
        FakeAdapter.calls = []
        FakeAdapter.active = FakeAdapter.maximum = FakeAdapter.constructions = 0
        FakeAdapter.out, FakeAdapter.behavior = self.out, None
        self.fixture = cases()
        self.patches = [patch.object(study, "adapter_class", return_value=FakeAdapter),
                        patch.object(study, "PHASE_LOCK", self.root / "phase.lock"),
                        patch.object(study, "load_frozen_sources", return_value=(self.fixture, self.fixture[:3],
                            {"cases_sha256": "full", "timing_cases_sha256": "timing"}))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_quality_complete_cross_product_and_frozen_before_dispatch(self):
        result = study.run_quality("offline-key", SPECS, self.out, budget=2, workers=3)
        self.assertEqual(result["metadata"]["status"], "complete")
        rows = study.read_rows(self.out / "responses.jsonl")
        self.assertEqual(len(rows), 24)
        self.assertEqual({(r["case_id"], r["model_label"]) for r in rows},
                         {(c["id"], s["label"]) for c in self.fixture for s in SPECS})
        plan = json.loads((self.out / "plan.json").read_text())
        self.assertEqual(len(study.read_rows(self.out / "requests.jsonl")), len(plan))
        for r in rows:
            request = {"endpoint": r["endpoint"], "payload": r["request"]}
            self.assertEqual(r["request_sha256"], study.digest(study.canonical(request).encode()))
        self.assertGreater(FakeAdapter.maximum, 1)
        self.assertLessEqual(FakeAdapter.maximum, 3)

    def test_source_corruption_stops_before_adapter_construction(self):
        with patch.object(study, "load_frozen_sources", side_effect=ValueError("Frozen source hash mismatch")):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                study.run_quality("offline-key", SPECS, self.out)
        self.assertEqual(FakeAdapter.constructions, 0)
        self.assertEqual(FakeAdapter.calls, [])

    def test_plan_reproduces_and_changes_if_request_changes(self):
        client = FakeAdapter("x")
        a, _ = study.create_plan(client, self.fixture, SPECS, "quality", 42)
        b, _ = study.create_plan(client, self.fixture, SPECS, "quality", 42)
        c, _ = study.create_plan(client, self.fixture, SPECS, "quality", 43)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        with patch.object(client, "build_request", return_value={"endpoint": "/offline", "payload": {"model": "test/Jev", "state": {"target": 1}}}):
            with self.assertRaises(ValueError):
                study.create_plan(client, self.fixture, SPECS[:1], "quality", 42)

    def test_budget_reservations_atomic_and_unknown_is_not_free(self):
        guard = study.Budget(.03)
        with ThreadPoolExecutor(max_workers=10) as pool:
            accepted = list(pool.map(lambda _: guard.reserve(.01), range(10)))
        self.assertEqual(sum(accepted), 3)
        for _ in range(3):
            guard.settle(.01, {"cost_usd": None})
        result = guard.snapshot()
        self.assertIsNone(result["charged_usd"])
        self.assertEqual(result["unknown_billing"], 3)
        self.assertAlmostEqual(result["budget_accounted_usd"], .03)
        self.assertEqual(result["budget_pending_usd"], 0)

    def test_per_model_and_fraction_circuits(self):
        circuit = study.Circuit()
        reason = None
        for _ in range(5):
            circuit.add({"model_label": "B", "valid": True})
            reason = circuit.add({"model_label": "A", "valid": False})
        self.assertIn("Five consecutive", reason)
        circuit = study.Circuit()
        for i in range(99):
            self.assertIsNone(circuit.add({"model_label": "A", "valid": i % 20 != 0}))
        self.assertIn("5%", circuit.add({"model_label": "A", "valid": True}))

    def test_adapter_exception_retained_once_and_breaker_stops(self):
        def raises(case, spec, seq):
            raise RuntimeError("secret offline-key")
        FakeAdapter.behavior = staticmethod(raises)
        result = study.run_quality("offline-key", SPECS[:1], self.out, budget=1, workers=1)
        self.assertEqual(result["metadata"]["status"], "stopped")
        self.assertEqual(len(FakeAdapter.calls), 5)
        rows = study.read_rows(self.out / "responses.jsonl")
        self.assertEqual(len({r["sequence"] for r in rows}), 5)
        self.assertTrue(all(r["cost_usd"] is None and r["probability"] is None for r in rows))
        self.assertNotIn("offline-key", (self.out / "responses.jsonl").read_text())
        self.assertAlmostEqual(result["metadata"]["unknown_billing_guard_usd"], .05)

    def test_malformed_adapter_raw_value_still_retains_dispatch_and_known_bill(self):
        FakeAdapter.behavior = staticmethod(lambda c, s, n: {
            "valid": True, "probability": .5, "cost_usd": .001,
            "usage": {"unexpected_value": float("nan")}})
        result = study.run_quality("offline-key", SPECS[:1], self.out, budget=1, workers=1)
        self.assertEqual(result["metadata"]["calls_completed"], 5)
        rows = study.read_rows(self.out / "responses.jsonl")
        self.assertTrue(all(r["valid"] is False and r["probability"] is None for r in rows))
        self.assertTrue(all(r["cost_usd"] == .001 and "raw_adapter_record_json" in r for r in rows))
        self.assertEqual(len(study.read_rows(self.out / "attempts.jsonl")), len(rows))

    def test_quality_resume_only_unattempted_and_rejects_tampered_plan(self):
        first = study.run_quality("offline-key", SPECS[:1], self.out, budget=.011, workers=1)
        self.assertEqual(first["metadata"]["status"], "stopped")
        before = list(FakeAdapter.calls)
        result = study.run_quality("offline-key", SPECS[:1], self.out, budget=1, workers=1, resume=True)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertEqual(len(FakeAdapter.calls), 8)
        self.assertEqual(len({c[0] for c in FakeAdapter.calls}), 8)
        self.assertEqual(FakeAdapter.calls[:len(before)], before)
        plan_path = self.out / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan[0]["request_sha256"] = "changed"
        study.save(plan_path, plan)
        with self.assertRaisesRegex(ValueError, "frozen plan"):
            study.run_quality("offline-key", SPECS[:1], self.out, budget=1, workers=1, resume=True)
        self.assertEqual(len(FakeAdapter.calls), 8)

    def test_dispatch_without_outcome_never_replayed(self):
        study.run_quality("offline-key", SPECS[:1], self.out, budget=.011, workers=1)
        rows = study.read_rows(self.out / "responses.jsonl")
        attempted = {r["sequence"] for r in rows}
        plan = json.loads((self.out / "plan.json").read_text())
        lost = next(e for e in plan if e["sequence"] not in attempted)
        study.append(self.out / "attempts.jsonl", {"sequence": lost["sequence"], "request_sha256": lost["request_sha256"], "dispatched_at": study.now()})
        meta = json.loads((self.out / "metadata.json").read_text())
        meta["status"] = "running"  # Simulate interruption after journal fsync.
        study.save(self.out / "metadata.json", meta)
        result = study.run_quality("offline-key", SPECS[:1], self.out, budget=1, workers=1, resume=True)
        self.assertEqual(result["metadata"]["status"], "complete")
        self.assertNotIn(lost["sequence"], [c[0] for c in FakeAdapter.calls])
        all_rows = study.read_rows(self.out / "responses.jsonl")
        missing = next(r for r in all_rows if r["sequence"] == lost["sequence"])
        self.assertTrue(missing["lost_outcome"])
        self.assertIsNone(missing["cost_usd"])

    def test_timing_three_blocks_two_warmups_per_model_and_serial(self):
        result = study.run_timing("offline-key", SPECS, self.out, budget=3)
        self.assertEqual(result["metadata"]["status"], "complete")
        rows = study.read_rows(self.out / "responses.jsonl")
        self.assertEqual(len(rows), 3 * (3 + 2) * 3)
        self.assertEqual(FakeAdapter.maximum, 1)
        for repeat in range(3):
            for model in SPECS:
                selected = [r for r in rows if r["repeat"] == repeat and r["model_label"] == model["label"]]
                self.assertEqual(sum(r["phase"] == "timing_warmup" for r in selected), 2)
                self.assertEqual(sum(r["phase"] == "timing" for r in selected), 3)
        with study.isolated_phase("quality"):
            with self.assertRaisesRegex(RuntimeError, "active"):
                study.run_timing("offline-key", SPECS, self.root / "other")


class FrozenSourceTests(unittest.TestCase):
    def test_actual_frozen_split_counts_hashes_and_membership(self):
        full, timing, hashes = study.load_frozen_sources()
        self.assertEqual((len(full), len(timing)), (4785, 60))
        self.assertEqual(hashes["cases_sha256"], study.CASES_SHA256)
        self.assertEqual(hashes["timing_cases_sha256"], study.TIMING_SHA256)


if __name__ == "__main__":
    unittest.main()
