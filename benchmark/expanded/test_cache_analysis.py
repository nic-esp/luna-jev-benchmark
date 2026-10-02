import unittest
import cache_analysis as analysis


def records():
    result = []
    for block in range(4):
        result.append({"phase": "cache_prime", "block": block, "valid": True, "cost_usd": .05, "latency_s": 5,
                       "usage": {"prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 2500}}})
        for pair in range(12):
            for target in (0, 1):
                for arm, latency, cost in (("Jev", 1, .01), ("Luna uncached", 4, .04), ("Luna cached", 2, .02)):
                    result.append({"case_id": f"{block}-{pair}-{target}", "pair_id": f"{block}-{pair}", "phase": "measured",
                                   "block": block, "model_label": arm, "target": target, "probability": 1-target if arm == "Luna cached" else target,
                                   "valid": True, "cost_usd": cost, "latency_s": latency,
                                   "usage": {"prompt_tokens_details": {"cached_tokens": 2500 if arm == "Luna cached" else 0, "cache_write_tokens": 0}}})
    return result


class CacheAnalysisTests(unittest.TestCase):
    def test_clustered_statistics_preserve_fixed_counterfactual_comparisons(self):
        result = analysis.analyse(records(), {"status": "complete"}, draws=100)
        compared = result["comparisons"]["Luna cached / Luna uncached"]["metrics"]
        self.assertEqual(compared["accuracy_difference"]["estimate"], -1)
        self.assertEqual(compared["accuracy_difference"]["ci95"], [-1, -1])
        self.assertEqual(compared["median_paired_latency_ratio"]["ci95"], [.5, .5])
        self.assertAlmostEqual(compared["cost_ratio"]["estimate"], .5)
        self.assertEqual(result["counterfactual_pair_accuracy"]["Luna cached"]["both_cases_correct"], 0)
        self.assertEqual(result["counterfactual_pair_accuracy"]["Jev"]["both_cases_correct"], 48)
        block = result["blocks"][0]["arms"]["Luna cached"]
        self.assertAlmostEqual(block["cost_per_1000_with_prime_usd"], (.02 * 24 + .05) / 24 * 1000)

    def test_incomplete_duplicate_or_mismatched_pairing_rejected(self):
        with self.assertRaisesRegex(ValueError, "complete"):
            analysis.analyse(records()[:-1], {}, draws=10)
        duplicate = records()
        duplicate[2] = dict(duplicate[1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            analysis.analyse(duplicate, {}, draws=10)
        wrong = records()
        wrong[2]["target"] = 1 - wrong[2]["target"]
        with self.assertRaisesRegex(ValueError, "disagree"):
            analysis.analyse(wrong, {}, draws=10)

    def test_missing_cost_rejected(self):
        missing = records()
        missing[1]["cost_usd"] = None
        with self.assertRaisesRegex(ValueError, "complete recorded billing"):
            analysis.analyse(missing, {}, draws=10)


if __name__ == "__main__":
    unittest.main()
