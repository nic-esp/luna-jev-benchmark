"""Build the frozen 180-case pilot. Uses only the Python standard library.

Only state, question, and criteria are model inputs. The target and source fields
are evaluator metadata and must never be sent to either model.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path
import random

SEED = 20261002
GENERATOR_VERSION = 1
HERE = Path(__file__).resolve().parent
BOOLQ_SOURCE = 'https://github.com/google-research-datasets/boolean-questions'
BOOLQ_DOWNLOAD = 'https://storage.googleapis.com/boolq/dev.jsonl'
BOOLQ_MIRROR = 'https://huggingface.co/datasets/google/boolq'
BOOLQ_ROWS_API = 'https://datasets-server.huggingface.co/rows?dataset=google%2Fboolq&config=default&split=validation'
BOOLQ_LICENSE = 'https://creativecommons.org/licenses/by-sa/3.0/'

POLICIES = {
    'refund': (
        'This is a fictional shop policy. A recalled item is eligible for a refund regardless of all other facts. '
        'For an item that is not recalled, a receipt is required and final-sale items are never eligible. '
        'With those requirements met, an item is eligible if it is defective and purchased at most 90 days ago, '
        'or if it is unused and purchased at most 30 days ago. Boundaries are inclusive. '
        'No other exceptions apply. The item price and customer membership do not affect eligibility.'
    ),
    'delivery': (
        'This is a fictional delivery policy. Free delivery is available only for mainland destinations, '
        'non-hazardous orders, and total weight at most 20 kilograms. These requirements cannot be waived. '
        'An order meeting them qualifies if its subtotal is at least 100 credits, or if the customer is '
        'a member and the subtotal is at least 50 credits, or if the order has an active free-delivery promotion. '
        'An active promotion waives only the subtotal and membership requirements. Boundaries are inclusive. '
        'Gift wrapping and customer account age do not affect the decision.'
    ),
    'access': (
        'This is a fictional laboratory access policy. The account must be active and must not be suspended. '
        'These two requirements always apply. An emergency permit then grants access regardless of role, '
        'training, clearance, or sponsor. Without an emergency permit, training must be complete, and the '
        'person must either be staff with clearance at least 2 or be a contractor with clearance at least 3 '
        'and a sponsor. Visitors never qualify without an emergency permit. Boundaries are inclusive. '
        'Years of experience and the badge color do not affect access.'
    ),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(identifier, experiment, state, question, target, target_kind, source):
    return {
        'id': identifier, 'experiment': experiment, 'state': state,
        'question': question,
        'criteria': {
            'true': 'The answer to the stated question is yes.',
            'false': 'The answer to the stated question is no.',
        },
        'target': float(target), 'target_kind': target_kind, 'source': source,
    }


def _balanced_sample(candidates, rng, count=20):
    groups = {True: [], False: []}
    for record, truth in candidates:
        groups[bool(truth)].append((record, bool(truth)))
    selected = rng.sample(groups[True], count // 2) + rng.sample(groups[False], count // 2)
    rng.shuffle(selected)
    return selected


def build_policy_cases(seed=SEED):
    """Sample 10 true and 10 false records for each of three exact rules."""
    rng = random.Random(seed)
    families = []
    refunds = []
    for recalled, receipt, final_sale, defective, unused, days in itertools.product(
        [False, True], [False, True], [False, True], [False, True], [False, True],
        [0, 29, 30, 31, 89, 90, 91, 120],
    ):
        record = dict(recalled=recalled, receipt=receipt, final_sale=final_sale,
                      defective=defective, unused=unused, days_since_purchase=days)
        truth = recalled or (receipt and not final_sale and
                             ((defective and days <= 90) or (unused and days <= 30)))
        refunds.append((record, truth))
    families.append(('refund', refunds, 'Is this item eligible for a refund under the supplied policy?'))

    deliveries = []
    for mainland, hazardous, weight, subtotal, member, promotion in itertools.product(
        [False, True], [False, True], [19, 20, 21], [0, 49, 50, 51, 99, 100, 101],
        [False, True], [False, True],
    ):
        record = dict(mainland=mainland, hazardous=hazardous, weight_kg=weight,
                      subtotal_credits=subtotal, member=member, active_promotion=promotion)
        truth = mainland and not hazardous and weight <= 20 and (
            subtotal >= 100 or (member and subtotal >= 50) or promotion)
        deliveries.append((record, truth))
    families.append(('delivery', deliveries, 'Does this order qualify for free delivery under the supplied policy?'))

    access = []
    for active, suspended, emergency, training, role, clearance, sponsor in itertools.product(
        [False, True], [False, True], [False, True], [False, True],
        ['staff', 'contractor', 'visitor'], [1, 2, 3, 4], [False, True],
    ):
        record = dict(account_active=active, suspended=suspended, emergency_permit=emergency,
                      training_complete=training, role=role, clearance=clearance, sponsor=sponsor)
        truth = active and not suspended and (emergency or (training and (
            (role == 'staff' and clearance >= 2) or
            (role == 'contractor' and clearance >= 3 and sponsor))))
        access.append((record, truth))
    families.append(('access', access, 'Is this person allowed laboratory access under the supplied policy?'))

    result = []
    for family, candidates, question in families:
        for record, truth in _balanced_sample(candidates, rng):
            record = dict(record)
            if family == 'refund':
                record.update(price_credits=rng.randint(5, 400), customer_member=rng.choice([False, True]))
            elif family == 'delivery':
                record.update(gift_wrapping=rng.choice([False, True]), account_age_days=rng.randint(0, 3000))
            else:
                record.update(experience_years=rng.randint(0, 30), badge_color=rng.choice(['blue', 'green', 'orange']))
            result.append(_case('', 'policy', {'policy': POLICIES[family], 'record': record}, question,
                                int(truth), 'label', {
                                    'kind': 'synthetic_programmatic', 'rule_family': family,
                                    'generator': 'datasets.py', 'generator_version': GENERATOR_VERSION,
                                    'seed': seed, 'validation': 'Exact boolean rule over supplied record fields',
                                }))
    rng.shuffle(result)
    for index, case in enumerate(result, 1):
        case['id'] = f'policy-{index:03d}'
    return result


def load_boolq_cases(raw_path: Path, seed=SEED, count=60):
    """Uniform sample without replacement; no label balancing or output selection."""
    raw_bytes = raw_path.read_bytes()
    rows = [json.loads(line) for line in raw_bytes.decode('utf-8').splitlines() if line.strip()]
    if len(rows) != 3270:
        raise ValueError(f'Expected complete BoolQ development split (3270), found {len(rows)}')
    for row in rows:
        if not isinstance(row.get('answer'), bool):
            raise ValueError('Every BoolQ answer must be a boolean')
        if not isinstance(row.get('passage'), str) or not isinstance(row.get('question'), str):
            raise ValueError('BoolQ passage and question must be strings')
    indices = random.Random(seed).sample(range(len(rows)), count)
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    cases = []
    for index in indices:
        row = rows[index]
        state = {'passage': row['passage']}
        if row.get('title'):
            state['title'] = row['title']
        question = row['question'].rstrip('?') + '?'
        case = _case(f'boolq-dev-{index:04d}', 'boolq', state, question, int(row['answer']), 'label', {
            'kind': 'published_human_label', 'dataset': 'BoolQ', 'split': 'development',
            'row_index_zero_based': index, 'publisher': 'Google Research',
            'repository': BOOLQ_SOURCE, 'original_download': BOOLQ_DOWNLOAD,
            'downloaded_mirror': BOOLQ_MIRROR, 'rows_api': BOOLQ_ROWS_API,
            'raw_jsonl_sha256': raw_sha, 'license': 'CC BY-SA 3.0', 'license_url': BOOLQ_LICENSE,
            'authors': 'Christopher Clark, Kenton Lee, Ming-Wei Chang, Tom Kwiatkowski, Michael Collins, Kristina Toutanova',
            'paper': 'https://arxiv.org/abs/1905.10044',
            'changes': 'Selected subset; added trailing question mark; converted boolean label to 0/1; wrapped passage in state.',
        })
        case['criteria'] = {
            'true': 'The answer to the question is yes, using the supplied passage as evidence.',
            'false': 'The answer to the question is no, using the supplied passage as evidence.',
        }
        cases.append(case)
    return cases


def _probability_case(family, state, question, probability, seed):
    probability = Fraction(probability)
    source = {
        'kind': 'synthetic_exact_probability', 'probability_family': family,
        'generator': 'datasets.py', 'generator_version': GENERATOR_VERSION, 'seed': seed,
        'truth_numerator': probability.numerator, 'truth_denominator': probability.denominator,
        'validation': 'Exact rational probability from the complete population and stated random selection mechanism',
    }
    case = _case('', 'probability', state, question, probability, 'probability', source)
    case['criteria'] = {
        'true': 'The stated event occurs in the random selection described in the state.',
        'false': 'The stated event does not occur in that random selection.',
    }
    return case


def build_probability_cases(seed=SEED):
    rng = random.Random(seed + 1)
    cases = []
    for i in range(20):
        # Varied known base rates, including endpoints and uncertainty near 0.5.
        totals = [20, 30, 40, 50, 60]
        selected_total = rng.choice(totals)
        selected_marked = rng.choice([0, 1, selected_total // 5, selected_total // 2,
                                     selected_total // 2 + 1, selected_total - 1, selected_total])
        other_total = rng.randint(10, 100)
        other_marked = rng.randint(0, other_total)
        selected_group = rng.choice(['A', 'B'])
        selected = {'marked': selected_marked, 'unmarked': selected_total - selected_marked}
        other = {'marked': other_marked, 'unmarked': other_total - other_marked}
        table = {'A': selected if selected_group == 'A' else other,
                 'B': selected if selected_group == 'B' else other}
        state = {
            'setting': 'Fictional inventory. The following table describes the entire population, not a sample.',
            'counts': table,
            'selection': f'Choose one item uniformly at random from group {selected_group} only.',
            'selected_group': selected_group,
            'interpretation': 'Estimate the probability of the event for this one random draw, using the supplied counts.',
        }
        cases.append(_probability_case('conditional_table', state, 'Is the selected item marked?',
                                       Fraction(selected_marked, selected_total), seed))

    for i in range(20):
        total = rng.randint(5, 45)
        marked = rng.randint(0, total)
        event = ['both_marked', 'exactly_one_marked', 'at_least_one_marked'][i % 3]
        denominator = total * (total - 1)
        if event == 'both_marked':
            numerator = marked * (marked - 1)
            question = 'Are both selected items marked?'
        elif event == 'exactly_one_marked':
            numerator = 2 * marked * (total - marked)
            question = 'Is exactly one of the two selected items marked?'
        else:
            numerator = denominator - (total - marked) * (total - marked - 1)
            question = 'Is at least one of the two selected items marked?'
        state = {
            'setting': 'A fictional box contains the entire population described by these counts.',
            'counts': {'marked': marked, 'unmarked': total - marked},
            'selection': 'Select two different items uniformly at random without replacement. Every unordered pair is equally likely.',
            'event': event,
            'interpretation': 'Estimate the probability of the event for these two random draws, using the supplied counts.',
        }
        cases.append(_probability_case('without_replacement', state, question,
                                       Fraction(numerator, denominator), seed))

    for i in range(20):
        tickets_a = rng.randint(1, 9)
        tickets_b = rng.randint(1, 9)
        total_a, total_b = rng.randint(5, 50), rng.randint(5, 50)
        marked_a, marked_b = rng.randint(0, total_a), rng.randint(0, total_b)
        state = {
            'setting': 'Two fictional boxes contain complete populations. Their contents are listed below.',
            'boxes': {'A': {'marked': marked_a, 'unmarked': total_a - marked_a},
                      'B': {'marked': marked_b, 'unmarked': total_b - marked_b}},
            'box_selection_tickets': {'A': tickets_a, 'B': tickets_b},
            'selection': 'First choose one ticket uniformly at random from all tickets. The ticket names box A or box B. Then choose one item uniformly at random from that box. There is no further information about the chosen box.',
            'interpretation': 'Estimate the probability of the event for this random item, accounting for both stages of selection.',
        }
        probability = (Fraction(tickets_a, tickets_a + tickets_b) * Fraction(marked_a, total_a)
                       + Fraction(tickets_b, tickets_a + tickets_b) * Fraction(marked_b, total_b))
        cases.append(_probability_case('weighted_mixture', state, 'Is the selected item marked?', probability, seed))
    rng.shuffle(cases)
    for index, case in enumerate(cases, 1):
        case['id'] = f'probability-{index:03d}'
    return cases


def validate_cases(cases):
    required = {'id', 'experiment', 'state', 'question', 'criteria', 'target', 'target_kind', 'source'}
    identifiers = set()
    for case in cases:
        if set(case) != required:
            raise ValueError(f'Unexpected case fields: {case.get("id")}')
        if case['id'] in identifiers:
            raise ValueError(f'Duplicate case ID: {case["id"]}')
        identifiers.add(case['id'])
        if not (0 <= case['target'] <= 1):
            raise ValueError('Target must be in [0,1]')
        if case['target_kind'] == 'label' and case['target'] not in (0, 1):
            raise ValueError('Label target must be binary')
        if case['target_kind'] not in ('label', 'probability'):
            raise ValueError('Invalid target kind')
        if set(case['criteria']) != {'true', 'false'}:
            raise ValueError('Both criteria are required')
        if not case['question'].endswith('?'):
            raise ValueError('Question must use question form')


def build_dataset(raw_path: Path, output_dir: Path = HERE / 'data', seed=SEED):
    cases = build_policy_cases(seed) + load_boolq_cases(raw_path, seed) + build_probability_cases(seed)
    validate_cases(cases)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / 'cases.jsonl'
    path.write_text(''.join(json.dumps(case, ensure_ascii=False, sort_keys=True) + '\n' for case in cases), encoding='utf-8')
    distribution = {}
    for experiment in ('policy', 'boolq', 'probability'):
        subset = [case for case in cases if case['experiment'] == experiment]
        distribution[experiment] = {
            'count': len(subset), 'target_mean': sum(case['target'] for case in subset) / len(subset),
            'target_min': min(case['target'] for case in subset), 'target_max': max(case['target'] for case in subset),
        }
        if experiment != 'probability':
            distribution[experiment]['labels'] = dict(Counter('yes' if case['target'] else 'no' for case in subset))
    manifest = {
        'name': 'Luna versus Jev matched binary decisions pilot', 'generator_version': GENERATOR_VERSION,
        'seed': seed, 'count': len(cases), 'cases_file': 'cases.jsonl', 'cases_sha256': _sha(path),
        'model_input_fields': ['state', 'question', 'criteria'],
        'never_send_to_models': ['id', 'experiment', 'target', 'target_kind', 'source'],
        'distribution': distribution,
        'policy_families': dict(Counter(case['source']['rule_family'] for case in cases if case['experiment'] == 'policy')),
        'probability_families': dict(Counter(case['source']['probability_family'] for case in cases if case['experiment'] == 'probability')),
        'boolq': {
            'source': BOOLQ_SOURCE, 'official_download': BOOLQ_DOWNLOAD,
            'mirror': BOOLQ_MIRROR, 'mirror_rows_api': BOOLQ_ROWS_API,
            'retrieval_note': 'Original Google Cloud Storage URL returned HTTP 403. Downloaded complete Google-owned Hugging Face validation mirror via rows API, then serialized to JSONL. This JSONL hash is of the local normalized mirror, not the original GCS file.',
            'source_rows': 3270, 'raw_jsonl_sha256': _sha(raw_path),
            'sample_method': 'random.Random(seed).sample(range(3270), 60); uniform without replacement; no filtering by label or model output',
            'selected_row_indices_zero_based': [case['source']['row_index_zero_based'] for case in cases if case['experiment'] == 'boolq'],
            'license': 'CC BY-SA 3.0', 'license_url': BOOLQ_LICENSE,
            'attribution': 'BoolQ: Exploring the Surprising Difficulty of Natural Yes/No Questions. Christopher Clark, Kenton Lee, Ming-Wei Chang, Tom Kwiatkowski, Michael Collins, Kristina Toutanova. NAACL 2019.',
            'paper': 'https://arxiv.org/abs/1905.10044',
        },
        'limitations': [
            'This is a small, exploratory pilot, not a broad ranking of either model.',
            'BoolQ development labels are human annotations rather than a mathematical oracle; the public set may be present in model training data.',
            'Synthetic policy and probability cases measure the specified rules and probability problems, not general operational or forecasting performance.',
            'Probability cases use exact known event probabilities, not observed binary outcomes. Use MAE/RMSE and expected proper scores for these cases.',
            'Thresholds and prompts must be frozen before reading scored model outputs.',
        ],
    }
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boolq-path', type=Path, default=HERE.parents[1] / 'work' / 'boolq-dev.jsonl')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'data')
    parser.add_argument('--seed', type=int, default=SEED)
    args = parser.parse_args()
    manifest = build_dataset(args.boolq_path, args.output_dir, args.seed)
    print(json.dumps({'count': manifest['count'], 'sha256': manifest['cases_sha256'], 'distribution': manifest['distribution']}, indent=2))


if __name__ == '__main__':
    main()
