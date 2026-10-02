"""Synthetic completed fixtures only; never read live benchmark outputs."""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location('test_summary_' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = load('verify_summary')
# The production analysis is used only to create test input, never by the verifier.
A = load('analyze')
MODELS = ['Jev', 'Luna', 'Qwen3.8 Flash', 'DeepSeek V4.1 Flash', 'MiMo V2.6 Flash', 'Hy4 Preview']


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def case(index, target, kind='label', **extra):
    return {'id': 'c' + str(index), 'experiment': 'binary' if kind == 'label' else 'probability',
            'target': target, 'target_kind': kind, **extra}


def row(c, model, probability, **extra):
    usage = {'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}}
    return {'case_id': c['id'], 'experiment': c['experiment'], 'target': c['target'], 'target_kind': c['target_kind'],
            'model_label': model, 'valid': probability is not None, 'probability': probability,
            'phase': 'quality', 'repeat': 0, 'cost_usd': .01, 'latency_s': 1.,
            'usage': usage, 'response': {'usage': copy.deepcopy(usage)}, **extra}


class SummaryVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cases = [case(0, 0), case(1, 1), case(2, 0), case(3, 1), case(4, .25, 'probability'), case(5, .75, 'probability')]
        self.cp = self.root / 'cases.jsonl'
        write_rows(self.cp, self.cases)
        self.tp = self.root / 'timing-cases.jsonl'
        write_rows(self.tp, self.cases[:2])
        self.quality = self.root / 'quality'
        self.timing = self.root / 'timing'
        self.cache = self.root / 'cache'
        for path in (self.quality, self.timing, self.cache):
            path.mkdir()
            (path / 'metadata.json').write_text(json.dumps({'status': 'complete'}))
        self.quality_rows = [row(c, model, c['target']) for c in self.cases for model in MODELS]
        # One failed label is retained as wrong and unbilled; one valid wrong label exercises tie handling.
        self.quality_rows[0].update(valid=False, probability=None, cost_usd=None)
        self.quality_rows[12]['probability'] = .5
        # Nonzero probability errors make accidental zero score implementations fail.
        self.quality_rows[24]['probability'] = .75
        self.quality_rows[30]['probability'] = .25
        write_rows(self.quality / 'responses.jsonl', self.quality_rows)
        self.timing_rows = [row(c, model, c['target'], phase='timing', repeat=repeat,
                               latency_s=(i + 1) * (1 + repeat / 10) * (1 + int(c['id'][1:])))
                            for c in self.cases[:2] for i, model in enumerate(MODELS) for repeat in range(3)]
        self.timing_rows[-1].update(valid=False, probability=None)
        self.timing_rows.append(row(self.cases[0], 'Jev', 0, phase='timing_warmup'))
        write_rows(self.timing / 'responses.jsonl', self.timing_rows)
        self.cache_cases = [case(i, i % 2, block=i // 2, pair_id='p' + str(i // 2)) for i in range(4)]
        write_rows(self.cache / 'cases.jsonl', self.cache_cases)
        self.cache_rows = []
        for c in self.cache_cases:
            for arm in ('Jev', 'Luna cached', 'Luna uncached'):
                r = row(c, arm, c['target'], phase='measured', block=c['block'], pair_id=c['pair_id'])
                counters = {'cached_tokens': 2048 if arm == 'Luna cached' else 0, 'cache_write_tokens': 0}
                r['usage']['prompt_tokens_details'] = counters
                r['response']['usage'] = copy.deepcopy(r['usage'])
                self.cache_rows.append(r)
        for block in range(2):
            c = case('prime' + str(block), 1)
            r = row(c, 'Luna cached', 1, phase='cache_prime', block=block, prime_for_arm='Luna cached', cost_usd=None if block == 0 else .03)
            r['usage']['prompt_tokens_details']['cache_write_tokens'] = 2048
            r['response']['usage'] = copy.deepcopy(r['usage'])
            self.cache_rows.append(r)
        write_rows(self.cache / 'responses.jsonl', self.cache_rows)
        self.summary = A.analyze([self.quality], self.timing, [self.cache], cases_path=self.cp,
            timing_cases_path=self.tp, models=MODELS, output_dir=self.root / 'analysis', bootstrap_samples=0)
        self.sp = self.root / 'analysis/summary.json'

    def tearDown(self):
        self.temp.cleanup()

    def verify(self):
        self.sp.write_text(json.dumps(self.summary))
        return V.verify_summary(self.sp, cases_path=self.cp, timing_cases_path=self.tp)

    def test_baseline_and_independently_known_values(self):
        result = self.verify()
        self.assertTrue(result['passed'], result)
        self.assertGreater(result['checked_values'], 1500)
        binary = self.summary['tasks']['binary']['models']['Jev']
        self.assertEqual(binary['quality']['accuracy_all_attempts'], .5)
        self.assertEqual(binary['quality']['proper_score_denominator'], 3)
        self.assertAlmostEqual(binary['quality']['brier_valid_only'], .25 / 3)
        self.assertAlmostEqual(binary['quality']['log_loss_valid_only'], math.log(2) / 3)
        probability = self.summary['tasks']['probability']['models']['Jev']['quality']
        self.assertEqual(probability['mae_valid_only'], .5)
        self.assertEqual(probability['rmse_valid_only'], .5)
        self.assertEqual(probability['expected_brier_valid_only'], .4375)
        self.assertEqual(self.summary['timing']['tasks']['binary']['common_complete_cases'], 1)
        self.assertEqual(self.summary['timing']['tasks']['binary']['models']['Luna']['primary_case_median_latency_s'], 2.2)
        cached = self.summary['cache_sessions'][0]['arms']['Luna cached']
        self.assertEqual(cached['reported_cache_usage']['read_tokens']['total'], 8192)
        self.assertIsNone(cached['setup_inclusive_usd_per_1000_decisions'])

    def test_denominator_and_proper_score_mutations(self):
        metric = self.summary['tasks']['binary']['models']['Jev']
        metric['quality']['accuracy_all_attempts'] = 2 / 3
        metric['quality']['proper_score_denominator'] = 4
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('accuracy_all_attempts' in m['path'] for m in result['mismatches']))
        self.assertTrue(any('proper_score_denominator' in m['path'] for m in result['mismatches']))

    def test_missing_bill_cannot_be_zero_or_projected(self):
        cost = self.summary['quality']['cost_quality_phase']
        cost.update(total_billed_usd=cost['known_billed_usd'], usd_per_1000_attempts=1, missing_bills=0)
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('total_billed_usd' in m['path'] for m in result['mismatches']))

    def test_timing_must_use_all_six_model_common_support(self):
        task = self.summary['timing']['tasks']['binary']
        task.update(common_complete_cases=2, common_case_ids=['c0', 'c1'])
        task['models']['Jev']['primary_case_median_latency_s'] = 1.65
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('common_case_ids' in m['path'] for m in result['mismatches']))
        self.assertTrue(any('primary_case_median_latency_s' in m['path'] for m in result['mismatches']))

    def test_unknown_prime_prevents_complete_allocation(self):
        item = self.summary['cache_sessions'][0]['arms']['Luna cached']
        item['setup_inclusive_cost']['total_billed_usd'] = .07
        item['setup_inclusive_usd_per_1000_decisions'] = 17.5
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('setup_inclusive' in m['path'] for m in result['mismatches']))

    def test_raw_response_cache_counter_readback(self):
        target = next(r for r in self.cache_rows if r['model_label'] == 'Luna cached')
        target['response']['usage']['prompt_tokens_details']['cached_tokens'] = 1
        write_rows(self.cache / 'responses.jsonl', self.cache_rows)
        # Refresh source hash to isolate raw-counter mismatch from hash mismatch.
        self.summary['cache_sessions'][0]['source']['responses_sha256'] = hashlib.sha256((self.cache / 'responses.jsonl').read_bytes()).hexdigest()
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('raw_usage' in m['path'] for m in result['mismatches']))
        self.assertTrue(any('read_tokens.total' in m['path'] for m in result['mismatches']))

    def test_active_source_refused(self):
        (self.quality / 'metadata.json').write_text(json.dumps({'status': 'running'}))
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertIn('Refusing unfinished source', result['errors'][0]['message'])
        self.assertNotIn(str(self.quality / 'responses.jsonl'), result['input_sha256'])

    def test_missing_attempt_rejected_even_if_summary_were_consistent(self):
        self.quality_rows.pop()
        write_rows(self.quality / 'responses.jsonl', self.quality_rows)
        self.summary = A.analyze([self.quality], self.timing, [self.cache], cases_path=self.cp,
            timing_cases_path=self.tp, models=MODELS, output_dir=self.root / 'analysis', bootstrap_samples=0)
        result = self.verify()
        self.assertFalse(result['passed'])
        self.assertTrue(any('cross_product' in m['path'] for m in result['mismatches']))


if __name__ == '__main__':
    unittest.main()
