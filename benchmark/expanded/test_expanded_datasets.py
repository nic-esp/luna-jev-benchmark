"""Independent truth, transform, provenance and freeze checks; no network calls."""
from collections import Counter
from fractions import Fraction
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('expanded_dataset_builder',HERE/'datasets.py')
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)


def policy_oracle(family,r):
    if family=='refund':
        if r['recalled']:return 1
        if not r['receipt'] or r['final_sale']:return 0
        if r['defective'] and r['days_since_purchase']<=90:return 1
        return int(r['unused'] and r['days_since_purchase']<=30)
    if family=='delivery':
        if not r['mainland'] or r['hazardous'] or r['weight_kg']>20:return 0
        if r['active_promotion']:return 1
        threshold=50 if r['member'] else 100
        return int(r['subtotal_credits']>=threshold)
    if not r['account_active'] or r['suspended']:return 0
    if r['emergency_permit']:return 1
    if not r['training_complete'] or r['role']=='visitor':return 0
    if r['role']=='staff':return int(r['clearance']>=2)
    if not r['sponsor']:return 0
    return int(r['clearance']>=3)


def probability_oracle(case):
    s=case['state'];family=case['source']['probability_family']
    if family=='conditional_table':
        row=s['counts'][s['selected_group']]
        population=[1]*row['marked']+[0]*row['unmarked']
        return Fraction(sum(population),len(population))
    if family=='without_replacement':
        row=s['counts'];population=[1]*row['marked']+[0]*row['unmarked']
        pairs=list(itertools.combinations(population,2));event=s['event']
        desired={'both_marked':lambda x:sum(x)==2,'exactly_one_marked':lambda x:sum(x)==1,'at_least_one_marked':lambda x:sum(x)>0}[event]
        return Fraction(sum(desired(x) for x in pairs),len(pairs))
    outcomes=[]
    for name,tickets in s['box_selection_tickets'].items():
        row=s['boxes'][name];population=[1]*row['marked']+[0]*row['unmarked']
        for _ in range(tickets):outcomes.append(sum(Fraction(x,len(population)) for x in population))
    return sum(outcomes)/len(outcomes)


class ExpandedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=d.read_jsonl(HERE/'data/cases.jsonl')
        cls.manifest=json.loads((HERE/'data/manifest.json').read_text())
        cls.groups={task:[c for c in cls.cases if c['experiment']==task] for task in d.SPLIT_COUNTS}

    def test_complete_counts_schema_and_hashes(self):
        d.validate(self.cases)
        self.assertEqual(d.sha(HERE/'data/cases.jsonl'),self.manifest['cases_sha256'])
        self.assertEqual(d.sha(HERE/'datasets.py'),self.manifest['generator_sha256'])
        for task,group in self.groups.items():
            self.assertEqual(len(group),d.SPLIT_COUNTS[task])
            self.assertEqual(d.sha(HERE/'data'/f'{task}.jsonl'),self.manifest['files'][task]['sha256'])
        self.assertEqual(d.sha(HERE.parent/'data/cases.jsonl'),'60e6db60c72869936548c3c4620aa3b070cf12e516f3be42a61a306d0f592660')

    def test_exact_public_label_mappings(self):
        expected={'rte':['entailment','not_entailment'],'wic':['False','True']}
        for task,names in expected.items():
            features=json.loads((HERE/'sources'/f'{task}-features.json').read_text())
            self.assertEqual(next(x['type']['names'] for x in features if x['name']=='label'),names)
        for task in ('boolq','rte','wic'):
            for c in self.groups[task]:
                raw=c['source']['published_label']
                self.assertEqual(c['target'],1-raw if task=='rte' else int(raw))
        # Independent semantic smoke checks guard against reversing either map.
        rte=self.groups['rte'];wic=self.groups['wic']
        self.assertEqual(rte[0]['target'],0) # Widow's death does not entail husband's accident.
        self.assertIn('accident',rte[0]['state']['hypothesis'])
        self.assertEqual(rte[1]['target'],1) # Resistance supports bacteria winning against antibiotics.
        self.assertEqual(wic[0]['target'],0) # Social class versus dignity/style.
        self.assertEqual(wic[3]['target'],1) # Both occurrences refer to the act of brushing.

    def test_public_source_rows_and_wic_offsets(self):
        paths={'boolq':d.WORK/'boolq-dev.jsonl','rte':d.WORK/'expanded/rte-validation.jsonl','wic':d.WORK/'expanded/wic-validation.jsonl'}
        if not all(p.exists() for p in paths.values()):self.skipTest('Source downloads are retained under workspace work/; provide them to rerun full source reconciliation')
        for task,path in paths.items():
            rows=d.read_jsonl(path)
            self.assertEqual(d.sha(path),self.manifest['raw_sources'][task]['normalized_jsonl_sha256'])
            self.assertEqual(len(rows),len(self.groups[task]))
            for index,(raw,c) in enumerate(zip(rows,self.groups[task])):
                self.assertEqual(c['source']['row_index_zero_based'],index)
                if task=='boolq':
                    self.assertEqual(c['state']['passage'],raw['passage'])
                    self.assertEqual(c['target'],int(raw['answer']))
                elif task=='rte':
                    self.assertEqual(c['state'],{k:raw[k] for k in ('premise','hypothesis')})
                    self.assertEqual(c['target'],int(raw['label']==0))
                else:
                    for j in (1,2):
                        marked=c['state'][f'sentence_{j}'];original=raw[f'sentence{j}']
                        self.assertEqual(marked.replace('[TARGET]','').replace('[/TARGET]',''),original)
                        span=marked.split('[TARGET]')[1].split('[/TARGET]')[0]
                        self.assertEqual(span,original[raw[f'start{j}']:raw[f'end{j}']])
                        self.assertEqual(marked.count('[TARGET]'),1);self.assertEqual(marked.count('[/TARGET]'),1)
                        self.assertTrue(span.strip())

    def test_balanced_policy_oracle_and_meaningful_contrasts(self):
        labels=Counter();pairs={}
        semantic=set()
        for c in self.groups['policy']:
            family=c['source']['rule_family'];record=c['state']['record']
            self.assertEqual(c['target'],policy_oracle(family,record),c['id'])
            labels[(family,int(c['target']))]+=1
            signature=(family,json.dumps(record,sort_keys=True));self.assertNotIn(signature,semantic);semantic.add(signature)
            if c['source']['anchor_pair_id']:pairs.setdefault(c['source']['anchor_pair_id'],[]).append(c)
        self.assertEqual(labels,{(f,y):50 for f in ('refund','delivery','access') for y in (0,1)})
        self.assertEqual(len(pairs),18)
        for pair in pairs.values():
            self.assertEqual(len(pair),2);self.assertEqual({c['target'] for c in pair},{0,1})
            self.assertNotEqual(pair[0]['state']['record'],pair[1]['state']['record'])

    def test_all_probability_targets_by_outcome_enumeration(self):
        families=Counter();inputs=set()
        pilot_inputs={json.dumps({'state':c['state'],'question':c['question']},sort_keys=True) for c in d.read_jsonl(HERE.parent/'data/cases.jsonl') if c['experiment']=='probability'}
        for c in self.groups['probability']:
            exact=probability_oracle(c)
            self.assertEqual(exact,Fraction(c['source']['truth_numerator'],c['source']['truth_denominator']))
            self.assertEqual(c['target'],float(exact))
            signature=json.dumps({'state':c['state'],'question':c['question']},sort_keys=True)
            self.assertNotIn(signature,inputs);self.assertNotIn(signature,pilot_inputs);inputs.add(signature)
            families[c['source']['probability_family']]+=1
        self.assertEqual(families,{'conditional_table':100,'without_replacement':100,'weighted_mixture':100})

    def test_overlap_and_source_label_error_preserved(self):
        pilot_ids={c['id'] for c in d.read_jsonl(HERE.parent/'data/cases.jsonl') if c['experiment']=='boolq'}
        flagged={c['id'] for c in self.groups['boolq'] if c['source']['pilot_overlap']}
        self.assertEqual(pilot_ids,flagged);self.assertEqual(len(flagged),60)
        carbon=next(c for c in self.groups['boolq'] if c['id']=='boolq-dev-1842')
        self.assertEqual(carbon['target'],1);self.assertIn('nonmetallic',carbon['state']['passage'])
        count=sum(bool(c['source']['semantic_pilot_overlap_ids']) for c in self.groups['policy'])
        self.assertEqual(count,self.manifest['pilot_overlap']['policy_semantic_overlap_count'])

    def test_synthetic_generation_reproduces_frozen_inputs(self):
        pilot=d.pilot_module()
        current=d.build_policies(pilot)
        for original,new in zip(self.groups['policy'],current):
            new['source']['semantic_pilot_overlap_ids']=original['source']['semantic_pilot_overlap_ids']
        self.assertEqual(current,self.groups['policy'])
        self.assertEqual(d.build_probabilities(pilot),self.groups['probability'])

if __name__=='__main__':unittest.main()
