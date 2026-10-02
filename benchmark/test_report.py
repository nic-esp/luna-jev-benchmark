"""Numerical and failure-accounting checks for the benchmark report."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import report


def record(case="c1", model="Jev", target=1, probability=0.8, **changes):
    value = {
        "case_id": case, "experiment": "policy", "model_label": model,
        "target": target, "target_kind": "label", "probability": probability,
        "valid": True, "error": None, "latency_s": 2.0, "cost_usd": 0.01,
        "phase": "measured", "response_model": "resolved/model", "provider": "test", "usage": {},
    }
    value.update(changes)
    return value


class ReportTests(unittest.TestCase):
    def test_labeled_metrics_and_invalid_denominator(self):
        result = report.summarize_group([
            record(target=1, probability=0.8),
            record("c2", target=0, probability=0.3, latency_s=4),
            record("c3", target=0, probability=None, valid=False, error="timeout", latency_s=60),
        ])
        self.assertEqual(result["valid_count"], 2)
        self.assertAlmostEqual(result["success_rate"], 2 / 3)
        self.assertEqual(result["quality"]["accuracy"], 1)
        self.assertAlmostEqual(result["quality"]["accuracy_all_requests"], 2 / 3)
        self.assertAlmostEqual(result["quality"]["brier"], (0.04 + 0.09) / 2)
        self.assertAlmostEqual(result["quality"]["log_loss"], (-math.log(.8) - math.log(.7)) / 2)
        self.assertEqual(result["valid_response_latency_s"]["p50"], 3)
        self.assertEqual(result["all_request_latency_s"]["count"], 3)
        self.assertAlmostEqual(result["cost"]["per_correct_answer_usd"], .015)

    def test_exact_probability_scores_use_expected_outcomes(self):
        result = report.summarize_group([
            record(target=.2, probability=.4, target_kind="probability"),
            record("c2", target=.5, probability=.5, target_kind="probability"),
        ])
        quality = result["quality"]
        self.assertAlmostEqual(quality["mae"], .1)
        self.assertAlmostEqual(quality["excess_brier"], .02)
        self.assertAlmostEqual(quality["rmse"], math.sqrt(.02))
        self.assertAlmostEqual(quality["expected_brier"], (.04 + .16 + .25) / 2)
        self.assertNotIn("accuracy", quality)
        self.assertIsNone(result["cost"]["per_correct_answer_usd"])

    def test_missing_cost_not_zero_and_failed_cost_included(self):
        result = report.summarize_group([
            record(cost_usd=.02),
            record("c2", cost_usd=None, valid=False, probability=None),
            record("c3", cost_usd=.03, valid=False, probability=None),
        ])
        costs = result["cost"]
        self.assertAlmostEqual(costs["known_total_usd"], .05)
        self.assertEqual(costs["missing_count"], 1)
        self.assertIsNone(costs["total_usd"])
        self.assertIsNone(costs["per_1000_requests_usd"])
        self.assertIsNone(costs["per_correct_answer_usd"])

    def test_warmups_excluded_from_quality_but_not_total_billing(self):
        summary = report.summarize_records([
            record(probability=1, cost_usd=.1),
            record("warm", probability=0, cost_usd=.2, phase="warmup"),
        ], bootstrap_samples=20)
        self.assertEqual(summary["experiments"]["policy"]["models"]["Jev"]["quality"]["accuracy"], 1)
        self.assertEqual(summary["totals"]["measured_requests"], 1)
        self.assertAlmostEqual(summary["totals"]["all_cost"]["total_usd"], .3)
        self.assertAlmostEqual(summary["totals"]["warmup_cost"]["total_usd"], .2)

    def test_paired_bootstrap_direction_and_repeat_clustering(self):
        records = [record("a", probability=1, latency_s=2), record("a", "Luna", probability=0, latency_s=1)]
        # Repeating a case must not give it more influence than case b.
        records += [record("a", probability=1, latency_s=2), record("a", "Luna", probability=0, latency_s=1)] * 3
        records += [record("b", probability=0, latency_s=4), record("b", "Luna", probability=1, latency_s=2)]
        comparison = report.paired_comparison(records, samples=200, seed=44)
        self.assertEqual(comparison["paired_cases"], 2)
        self.assertEqual(comparison["quality_differences"]["accuracy"]["estimate"], 0)
        self.assertEqual(comparison["latency_median_ratio"]["estimate"], .5)
        self.assertEqual(comparison["latency_median_ratio"]["ci95"], [.5, .5])
        self.assertEqual(comparison, report.paired_comparison(records, samples=200, seed=44))

    def test_paired_excludes_failures_and_conflicting_targets(self):
        records = [
            record("a"), record("a", "Luna"),
            record("b"), record("b", "Luna", probability=None, valid=False),
            record("c"), record("c", "Luna", target=0),
        ]
        paired = report.paired_comparison(records, samples=100, seed=5)
        self.assertEqual(paired["total_cases"], 3)
        self.assertEqual(paired["paired_cases"], 1)
        self.assertEqual(paired["excluded_cases"], 2)
        self.assertEqual(paired["mismatched_target_case_ids"], ["c"])
        self.assertIsNone(paired["quality_differences"]["accuracy"]["ci95"])

    def test_nonfinite_and_out_of_range_predictions_invalid(self):
        for prediction in (float("nan"), float("inf"), -.1, 1.1, True, "0.5"):
            with self.subTest(prediction=prediction):
                self.assertFalse(report.valid_prediction(record(probability=prediction)))
        self.assertFalse(report.valid_prediction(record(target=.2)))
        self.assertEqual(report.loss_values(record(probability=.5))["accuracy"], 1)
        self.assertTrue(math.isfinite(report.loss_values(record(probability=0))["log_loss"]))

    def test_empty_and_all_failed_groups_are_reportable(self):
        empty = report.summarize_records([], bootstrap_samples=0)
        self.assertEqual(empty["experiments"], {})
        self.assertIn("No measured results", report.html_report(empty, []))
        failed = report.summarize_group([record(valid=False, probability=None)])
        self.assertIsNone(failed["quality"]["accuracy"])
        self.assertEqual(failed["quality"]["accuracy_all_requests"], 0)
        self.assertIsNone(failed["valid_response_latency_s"]["p95"])
        self.assertIsNone(failed["cost"]["per_correct_answer_usd"])

    def test_mixed_target_kinds_rejected(self):
        with self.assertRaisesRegex(ValueError, "mixed target kinds"):
            report.summarize_records([record(), record("b", target_kind="probability", target=.4)])

    def test_generate_files_escape_untrusted_text_and_accept_string_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            records_path = base / "raw.jsonl"
            records_path.write_text(json.dumps(record(error="<img src=x onerror=alert(1)>")) + "\n", encoding="utf-8")
            summary = report.generate_report(records_path, base / "out", metadata={"protocol": "Sequential randomized", "note": "</script><script>alert(1)</script>"}, bootstrap_samples=10)
            result_html = (base / "out" / "report.html").read_text()
            self.assertIn("&lt;img", result_html)
            self.assertNotIn("<img src=x", result_html)
            self.assertNotIn("</script><script>alert(1)</script>", result_html)
            self.assertEqual(json.loads((base / "out" / "summary.json").read_text()), summary)
            self.assertIn("Accuracy (all)", (base / "out" / "report.md").read_text())
            self.assertIn("No", "No external calls occur in these tests")

    def test_broken_jsonl_reports_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text("\n{broken}\n")
            with self.assertRaisesRegex(ValueError, "line 2"):
                report.read_records(path)


if __name__ == "__main__":
    unittest.main()
