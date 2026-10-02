"""Independent checks of frozen labels, exact probabilities, provenance and isolation."""
from collections import Counter
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path
import random
import tempfile
import unittest

import datasets

HERE = Path(__file__).resolve().parent


def policy_oracle(family, record):
    """Decision-tree implementation independent of generator boolean expressions."""
    r = record
    if family == 'refund':
        if r['recalled']:
            return True
        if not r['receipt'] or r['final_sale']:
            return False
        if r['defective']:
            if r['days_since_purchase'] <= 90:
                return True
        if r['unused']:
            if r['days_since_purchase'] <= 30:
                return True
        return False
    if family == 'delivery':
        if not r['mainland'] or r['hazardous']:
            return False
        if r['weight_kg'] > 20:
            return False
        if r['active_promotion']:
            return True
        threshold = 50 if r['member'] else 100
        return r['subtotal_credits'] >= threshold
    if family == 'access':
        if not r['account_active'] or r['suspended']:
            return False
        if r['emergency_permit']:
            return True
        if not r['training_complete']:
            return False
        minimum = {'staff': 2, 'contractor': 3}.get(r['role'])
        if minimum is None or r['clearance'] < minimum:
            return False
        if r['role'] == 'contractor' and not r['sponsor']:
            return False
        return True
    raise ValueError(family)


def enumerate_probability(case):
    """Count explicit equally likely outcomes; no generator closed-form formulas."""
    state = case['state']
    family = case['source']['probability_family']
    if family == 'conditional_table':
        row = state['counts'][state['selected_group']]
        population = [True] * row['marked'] + [False] * row['unmarked']
        return Fraction(sum(population), len(population))
    if family == 'without_replacement':
        row = state['counts']
        population = [True] * row['marked'] + [False] * row['unmarked']
        pairs = list(itertools.combinations(population, 2))
        event = state['event']
        if event == 'both_marked':
            successes = sum(all(pair) for pair in pairs)
        elif event == 'exactly_one_marked':
            successes = sum(sum(pair) == 1 for pair in pairs)
        elif event == 'at_least_one_marked':
            successes = sum(any(pair) for pair in pairs)
        else:
            raise ValueError(event)
        return Fraction(successes, len(pairs))
    if family == 'weighted_mixture':
        # Expand tickets; integrate every item outcome separately for each ticket.
        probabilities = []
        for box_name, ticket_count in state['box_selection_tickets'].items():
            row = state['boxes'][box_name]
            population = [True] * row['marked'] + [False] * row['unmarked']
            for _ in range(ticket_count):
                probabilities.append(sum(Fraction(int(marked), len(population)) for marked in population))
        return sum(probabilities) / len(probabilities)
    raise ValueError(family)


class FrozenDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (HERE / 'data' / 'cases.jsonl').read_bytes()
        cls.cases = [json.loads(line) for line in cls.raw.decode('utf-8').splitlines()]
        cls.manifest = json.loads((HERE / 'data' / 'manifest.json').read_text())

    def test_manifest_hash_and_counts(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.manifest['cases_sha256'])
        self.assertEqual(self.manifest['seed'], 20261002)
        self.assertEqual(len(self.cases), 180)
        self.assertEqual(Counter(c['experiment'] for c in self.cases), {'policy': 60, 'boolq': 60, 'probability': 60})
        self.assertEqual(len({c['id'] for c in self.cases}), 180)
        datasets.validate_cases(self.cases)

    def test_model_inputs_have_no_evaluator_metadata(self):
        forbidden = {'target', 'target_kind', 'source', 'truth_numerator', 'truth_denominator', 'answer', 'label', 'expected'}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        visible = []
        for case in self.cases:
            model_input = {key: case[key] for key in ('state', 'question', 'criteria')}
            check(model_input)
            visible.append(json.dumps(model_input, sort_keys=True))
        self.assertEqual(len(visible), len(set(visible)), 'Duplicate model-visible cases weaken this pilot')

    def test_policy_truth_independently(self):
        family_labels = Counter()
        for case in self.cases:
            if case['experiment'] != 'policy':
                continue
            family = case['source']['rule_family']
            expected = policy_oracle(family, case['state']['record'])
            self.assertEqual(case['target'], int(expected), case['id'])
            self.assertEqual(case['target_kind'], 'label')
            family_labels[(family, int(expected))] += 1
        self.assertEqual(family_labels, {(family, label): 10 for family in ('refund', 'delivery', 'access') for label in (0, 1)})

    def test_policy_distractors_cannot_change_truth(self):
        for case in self.cases:
            if case['experiment'] != 'policy':
                continue
            family = case['source']['rule_family']
            record = dict(case['state']['record'])
            if family == 'refund':
                record.update(price_credits=999999, customer_member=not record['customer_member'])
            elif family == 'delivery':
                record.update(gift_wrapping=not record['gift_wrapping'], account_age_days=999999)
            else:
                record.update(experience_years=999999, badge_color='purple')
            self.assertEqual(case['target'], int(policy_oracle(family, record)))

    def test_probability_truth_by_outcome_enumeration(self):
        families = Counter()
        for case in self.cases:
            if case['experiment'] != 'probability':
                continue
            exact = enumerate_probability(case)
            stated = Fraction(case['source']['truth_numerator'], case['source']['truth_denominator'])
            self.assertEqual(exact, stated, case['id'])
            self.assertEqual(case['target'], float(exact), case['id'])
            self.assertEqual(case['target_kind'], 'probability')
            families[case['source']['probability_family']] += 1
        self.assertEqual(families, {'conditional_table': 20, 'without_replacement': 20, 'weighted_mixture': 20})

    def test_boolq_sampling_and_attribution(self):
        cases = [c for c in self.cases if c['experiment'] == 'boolq']
        expected_indices = random.Random(20261002).sample(range(3270), 60)
        self.assertEqual([c['source']['row_index_zero_based'] for c in cases], expected_indices)
        for case in cases:
            self.assertEqual(case['source']['dataset'], 'BoolQ')
            self.assertEqual(case['source']['license'], 'CC BY-SA 3.0')
            self.assertEqual(case['source']['raw_jsonl_sha256'], self.manifest['boolq']['raw_jsonl_sha256'])
            self.assertIn('authors', case['source'])
            self.assertIn('changes', case['source'])
            self.assertGreater(len(case['state']['passage']), 10)

    def test_synthetic_reproducibility(self):
        policies = [c for c in self.cases if c['experiment'] == 'policy']
        probabilities = [c for c in self.cases if c['experiment'] == 'probability']
        self.assertEqual(policies, datasets.build_policy_cases(20261002))
        self.assertEqual(probabilities, datasets.build_probability_cases(20261002))
        self.assertNotEqual(policies, datasets.build_policy_cases(20261003))

    def test_boolq_labels_and_rebuild_against_full_source_when_available(self):
        raw_path = HERE.parents[1] / 'work' / 'boolq-dev.jsonl'
        if not raw_path.exists():
            self.skipTest('Full BoolQ source stays outside the deliverable; provide work/boolq-dev.jsonl to rerun source reconciliation')
        raw_rows = [json.loads(line) for line in raw_path.read_text().splitlines()]
        self.assertEqual(len(raw_rows), 3270)
        self.assertEqual(hashlib.sha256(raw_path.read_bytes()).hexdigest(), self.manifest['boolq']['raw_jsonl_sha256'])
        for case in self.cases:
            if case['experiment'] != 'boolq':
                continue
            original = raw_rows[case['source']['row_index_zero_based']]
            self.assertEqual(case['target'], int(original['answer']))
            self.assertEqual(case['state']['passage'], original['passage'])
            self.assertEqual(case['question'].rstrip('?'), original['question'].rstrip('?'))
        with tempfile.TemporaryDirectory() as temporary:
            rebuilt = datasets.build_dataset(raw_path, Path(temporary))
            self.assertEqual(rebuilt['cases_sha256'], self.manifest['cases_sha256'])

    def test_invalid_schema_is_rejected(self):
        for replacement in (-0.1, 1.1):
            case = dict(self.cases[0], target=replacement)
            with self.assertRaises(ValueError):
                datasets.validate_cases([case])
        with self.assertRaises(ValueError):
            datasets.validate_cases([self.cases[0], self.cases[0]])


if __name__ == '__main__':
    unittest.main()
