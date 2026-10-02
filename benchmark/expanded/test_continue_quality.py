import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import continue_quality as continuation


def fixture(root, n=6, attempts=2, invalid_indices=(0,), unknown_indices=(0,)):
    cases = [{"id": f"c{i}", "experiment": "policy", "target": i % 2, "target_kind": "label",
              "state": {"number": i}, "question": "Yes?", "criteria": {"true": "yes", "false": "no"}} for i in range(n)]
    case_path = root / "cases.jsonl"
    case_path.write_text("".join(json.dumps(c) + "\n" for c in cases))
    plan = [{"pair_index": i, "case_id": c["id"], "models": ["Jev", "Luna"] if i % 2 == 0 else ["Luna", "Jev"]} for i, c in enumerate(cases)]
    out = root / "run"
    out.mkdir()
    (out / "plan.json").write_text(json.dumps(plan))
    rows = []
    for sequence in range(attempts):
        pi, order = divmod(sequence, 2)
        case, model = cases[pi], plan[pi]["models"][order]
        invalid = sequence in invalid_indices
        rows.append({"case_id": case["id"], "model_label": model, "phase": "quality", "sequence": sequence,
                     "pair_index": pi, "target": case["target"], "target_kind": "label", "experiment": "policy",
                     "valid": not invalid, "probability": None if invalid else float(case["target"]),
                     "cost_usd": None if sequence in unknown_indices else .000001,
                     "http_status": 520 if invalid else 200, "error": "HTTP 520" if invalid else None})
    raw = "".join(json.dumps(r) + "\n" for r in rows).encode()
    (out / "responses.jsonl").write_bytes(raw)
    meta = {"status": "stopped", "cases": n, "planned_calls": 2*n, "seed": 20261002,
            "cases_sha256": hashlib.sha256(case_path.read_bytes()).hexdigest(), "runner_sha256": "initial-runner-hash",
            "adapter_sha256": hashlib.sha256(Path(continuation.runner.__file__).read_bytes()).hexdigest()}
    (out / "metadata.json").write_text(json.dumps(meta))
    return case_path, out, raw


class FakeClient:
    called = []
    invalid = False
    def __init__(self, key):
        pass
    def close(self):
        pass
    def run(self, case, model, phase, sequence):
        self.called.append((case["id"], model))
        return {"case_id": case["id"], "model_label": model, "phase": phase, "sequence": sequence,
                "target": case["target"], "target_kind": "label", "experiment": "policy", "valid": not self.invalid,
                "probability": None if self.invalid else float(case["target"]), "cost_usd": .000001,
                "error": "bad" if self.invalid else None, "http_status": 200, "usage": {}, "latency_s": .01}


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        FakeClient.called = []
        FakeClient.invalid = False

    def test_failed_attempt_retained_no_retry_prefix_and_metadata_hash_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(continuation.runner, "Client", FakeClient):
            cases, out, initial = fixture(Path(tmp))
            original_meta = (out / "metadata.json").read_bytes()
            result = continuation.run_quality("sk-or-fake", cases, out, workers=2)
            self.assertEqual(result["metadata"]["status"], "complete")
            self.assertEqual(result["metadata"]["calls_completed"], 12)
            self.assertEqual(result["metadata"]["invalid"], 1)
            self.assertNotIn(("c0", "Jev"), FakeClient.called)
            self.assertEqual(len(FakeClient.called), 10)
            self.assertEqual((out / "responses.jsonl").read_bytes()[:len(initial)], initial)
            amendment = result["metadata"]["protocol_amendments"][0]
            self.assertEqual(amendment["initial_metadata_sha256"], hashlib.sha256(original_meta).hexdigest())
            self.assertEqual((out / amendment["initial_metadata_snapshot"]).read_bytes(), original_meta)
            self.assertIsNone(result["metadata"]["charged_usd"])
            self.assertEqual(result["metadata"]["unknown_billing"], 1)
            self.assertAlmostEqual(result["metadata"]["budget_accounted_usd"], .01 + 11*.000001)
            rows = [json.loads(x) for x in (out / "responses.jsonl").read_text().splitlines()]
            self.assertIsNone(rows[0]["cost_usd"])
            self.assertEqual(len({(r["case_id"],r["model_label"]) for r in rows}), 12)

    def test_all_9570_attempts_counted_complete_with_original_invalid_retained(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(continuation.runner, "Client", FakeClient):
            cases, out, _ = fixture(Path(tmp), n=4785, attempts=9568)
            result = continuation.run_quality("sk-or-fake", cases, out, workers=16)
            self.assertEqual(result["metadata"]["status"], "complete")
            self.assertEqual(result["metadata"]["calls_completed"], 9570)
            self.assertEqual(result["metadata"]["invalid"], 1)
            self.assertEqual(len(FakeClient.called), 2)

    def test_pending_and_unknown_allocations_both_constrain_reservations(self):
        guard = continuation.AllowanceBudget(.03, [{"cost_usd": None}])
        first = guard.reserve(.001)
        self.assertEqual(first, .01)
        second = guard.reserve(.001)
        with self.assertRaises(RuntimeError):
            guard.reserve(.001)
        guard.settle(first, {"cost_usd": None})
        guard.settle(second, {"cost_usd": .001})
        self.assertEqual(guard.snapshot()["unknown_billing"], 2)
        self.assertAlmostEqual(guard.snapshot()["budget_accounted_usd"], .021)
        self.assertEqual(guard.snapshot()["budget_pending_usd"], 0)
        self.assertIsNone(guard.snapshot()["charged_usd"])

    def test_five_consecutive_invalid_stops_before_sixth_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(continuation.runner, "Client", FakeClient):
            cases, out, _ = fixture(Path(tmp), n=20, attempts=0, invalid_indices=(), unknown_indices=())
            FakeClient.invalid = True
            result = continuation.run_quality("sk-or-fake", cases, out, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 5)
            self.assertIn("consecutive", result["metadata"]["error"])

    def test_global_failure_rate_reuses_initial_records(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(continuation.runner, "Client", FakeClient):
            cases, out, _ = fixture(Path(tmp), n=60, attempts=99, invalid_indices=(1,20,40,60), unknown_indices=())
            FakeClient.invalid = True
            result = continuation.run_quality("sk-or-fake", cases, out, workers=1)
            self.assertEqual(result["metadata"]["status"], "stopped")
            self.assertEqual(result["metadata"]["calls_completed"], 100)
            self.assertEqual(result["metadata"]["invalid"], 5)
            self.assertEqual(result["metadata"]["consecutive_invalid"], 1)
            self.assertIn("5%", result["metadata"]["error"])


if __name__ == "__main__":
    unittest.main()
