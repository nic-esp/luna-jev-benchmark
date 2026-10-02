"""Offline tests of concurrent budgets, paired scheduling, logs, and failures."""
import concurrent.futures
import json
import math
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import run_study as study


def fixtures(path, n=40):
    rows = [{"id": f"case-{i}", "experiment": ["boolq", "policy", "probability"][i % 3],
             "state": {"number": i}, "question": "Is the number even?",
             "criteria": {"true": "Even", "false": "Odd"},
             "target": int(i % 2 == 0), "target_kind": "label"} for i in range(n)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rows


class FakeClient:
    instances = []
    def __init__(self, key):
        self.key = key
        self.owner = threading.get_ident()
        self.closed = False
        self.calls = []
        type(self).instances.append(self)
    def close(self):
        self.closed = True
    def run(self, case, model, phase, sequence):
        if threading.get_ident() != self.owner:
            raise AssertionError("Client shared across worker threads")
        self.calls.append((case["id"], model, phase, sequence))
        time.sleep(.001 * (sequence % 3 + 1))
        return {"case_id": case["id"], "experiment": case["experiment"], "target": case["target"],
                "target_kind": case["target_kind"], "model_label": model, "phase": phase, "sequence": sequence,
                "valid": True, "probability": float(case["target"]), "error": None, "cost_usd": .0001,
                "latency_s": .01, "usage": {}, "http_status": 200}


class RunStudyTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances = []

    def test_budget_rejects_invalid_limit_and_tracks_pending_reservations(self):
        for value in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                study.Budget(value)
        guard = study.Budget(1)
        guard.reserve(.6)
        with self.assertRaises(RuntimeError):
            guard.reserve(.5)
        guard.settle(.6, {"cost_usd": .2})
        self.assertAlmostEqual(guard.snapshot()["known_charge_subtotal_usd"], .2)
        self.assertEqual(guard.snapshot()["budget_pending_usd"], 0)

    def test_missing_bill_is_unknown_even_for_rejected_http_status(self):
        for status in (200, 400, 401, 403, 404, 429, 500, None):
            guard = study.Budget(1)
            guard.reserve(.1)
            guard.settle(.1, {"cost_usd": None, "http_status": status})
            self.assertIsNone(guard.snapshot()["charged_usd"], status)
            self.assertEqual(guard.snapshot()["unknown_billing"], 1)
            with self.assertRaises(RuntimeError):
                guard.reserve(.1)

    def test_threaded_reservations_cannot_overallocate(self):
        guard = study.Budget(1)
        barrier = threading.Barrier(5)
        def task(_):
            guard.reserve(.2)
            barrier.wait(timeout=5)
            guard.settle(.2, {"cost_usd": .2})
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(task, range(5)))
        self.assertAlmostEqual(guard.snapshot()["charged_usd"], 1)
        self.assertAlmostEqual(guard.snapshot()["budget_pending_usd"], 0)
        with self.assertRaises(RuntimeError):
            guard.reserve(.01)

    def test_quality_plan_pair_order_and_jsonl_thread_integrity(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", FakeClient):
            root = Path(tmp)
            cases = fixtures(root / "cases.jsonl")
            result = study.run_quality("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, workers=8)
            metadata = result["metadata"]
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["calls_completed"], 80)
            self.assertAlmostEqual(metadata["charged_usd"], .008)
            self.assertEqual(metadata["unknown_billing"], 0)
            rows = [json.loads(line) for line in (root / "run" / "responses.jsonl").read_text().splitlines()]
            plan = json.loads((root / "run" / "plan.json").read_text())
            self.assertEqual(len(rows), 80)
            self.assertEqual({r["sequence"] for r in rows}, set(range(80)))
            self.assertEqual(len({(r["case_id"], r["model_label"]) for r in rows}), 80)
            for pair in plan:
                pair_rows = [r for r in rows if r["pair_index"] == pair["pair_index"]]
                self.assertEqual([r["model_label"] for r in pair_rows], pair["models"])
                self.assertEqual({r["case_id"] for r in pair_rows}, {pair["case_id"]})
                self.assertTrue(all(r["measurement_role"] == "bulk_quality" for r in pair_rows))
            self.assertEqual({r["case_id"] for r in rows}, {c["id"] for c in cases})
            self.assertTrue(all(c.closed for c in FakeClient.instances))
            self.assertNotIn("sk-or-fake", (root / "run" / "responses.jsonl").read_text())

    def test_quality_records_unknown_billing_before_stopping(self):
        class MissingBill(FakeClient):
            def run(self, *args):
                row = super().run(*args)
                row.update(valid=False, probability=None, cost_usd=None, http_status=429, error="Rejected")
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", MissingBill):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 6)
            result = study.run_quality("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 1)
            self.assertIsNone(result["metadata"]["charged_usd"])
            self.assertEqual(result["metadata"]["unknown_billing"], 1)
            self.assertEqual(len((root / "run" / "responses.jsonl").read_text().splitlines()), 1)

    def test_final_call_overspend_is_logged_and_stopped(self):
        class ExcessCharge(FakeClient):
            def run(self, *args):
                row = super().run(*args)
                row["cost_usd"] = 2
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", ExcessCharge):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 1)
            result = study.run_quality("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 1)
            self.assertEqual(result["metadata"]["charged_usd"], 2)
            self.assertIn("exceeded budget", result["metadata"]["error"])
            self.assertEqual(len((root / "run" / "responses.jsonl").read_text().splitlines()), 1)

    def test_five_invalid_circuit_breaker_stops_before_sixth_serial_call(self):
        class Invalid(FakeClient):
            def run(self, *args):
                row = super().run(*args)
                row.update(valid=False, probability=None, error="Wrong output schema")
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", Invalid):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 20)
            result = study.run_quality("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 5)
            self.assertEqual(result["metadata"]["invalid"], 5)

    def test_unexpected_client_exception_is_retained_and_secret_redacted(self):
        class Broken(FakeClient):
            def run(self, *args):
                raise RuntimeError("Adapter broke with key " + self.key)
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", Broken):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 2)
            result = study.run_quality("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            rows = (root / "run" / "responses.jsonl").read_text()
            self.assertNotIn("sk-or-fake", rows)
            self.assertTrue(json.loads(rows)["request_exception"])
            self.assertIsNone(result["metadata"]["charged_usd"])

    def test_timing_blocks_are_serial_and_warmups_distinct_from_measurements(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", FakeClient):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 3)
            result = study.run_timing("sk-or-fake", root / "cases.jsonl", root / "run", budget=1, repeats=3)
            self.assertEqual(result["metadata"]["status"], "complete")
            rows = [json.loads(line) for line in (root / "run" / "responses.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 30)
            self.assertEqual([r["sequence"] for r in rows], list(range(30)))
            for repeat in range(3):
                block = [r for r in rows if r["repeat"] == repeat]
                self.assertEqual([r["phase"] for r in block[:4]], ["timing_warmup"] * 4)
                self.assertEqual([r["phase"] for r in block[4:]], ["timing"] * 6)
                self.assertEqual(len({(r["case_id"], r["model_label"]) for r in block[4:]}), 6)
            self.assertEqual(len(FakeClient.instances), 1)
            self.assertIn("3 randomized blocks, same 3 cases", result["metadata"]["protocol"])

    def test_timing_unknown_bill_is_retained_and_stops_immediately(self):
        class MissingBill(FakeClient):
            def run(self, *args):
                row = super().run(*args)
                row["cost_usd"] = None
                return row
        with tempfile.TemporaryDirectory() as tmp, patch.object(study.runner, "Client", MissingBill):
            root = Path(tmp)
            fixtures(root / "cases.jsonl", 3)
            result = study.run_timing("sk-or-fake", root / "cases.jsonl", root / "run", budget=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 1)
            self.assertIsNone(result["metadata"]["charged_usd"])


if __name__ == "__main__":
    unittest.main()
