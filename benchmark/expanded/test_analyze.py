"""Numerical failure-accounting and paired/cluster inference tests; no API calls."""
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('expanded_analysis',Path(__file__).with_name('analyze.py'))
a=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(a)


def row(cid='a',model='Jev',target=1,p=.8,**extra):
    r={'case_id':cid,'model_label':model,'experiment':'policy','target':target,'target_kind':'label','probability':p,
       'valid':True,'cost_usd':.01,'latency_s':1.,'phase':'quality','repeat':0}
    r.update(extra);return r


def case(cid,task='policy',target=1,passage=None):
    return {'id':cid,'experiment':task,'target':target,'target_kind':'label','state':{'passage':passage} if passage is not None else {},'question':'Question?','criteria':{}}


class AnalysisTests(unittest.TestCase):
    def test_invalid_denominators_and_balanced_accuracy(self):
        rs=[row('a',target=1,p=1),row('b',target=1,p=1),row('c',target=1,p=None,valid=False),row('d',target=0,p=1)]
        d=a.describe(rs,'label');q=d['quality']
        self.assertEqual(q['accuracy_all_attempts'],.5)
        self.assertAlmostEqual(q['accuracy_valid_only'],2/3)
        self.assertAlmostEqual(q['balanced_accuracy_all_attempts'],1/3)
        self.assertAlmostEqual(q['balanced_accuracy_valid_only'],.5)
        self.assertAlmostEqual(q['brier_valid_only'],1/3)
        self.assertEqual(q['proper_score_denominator'],3)
        self.assertEqual(d['success_rate'],.75)
        self.assertEqual(q['class_denominators']['yes']['attempts'],3)
        self.assertEqual(d['cost']['usd_per_correct_answer'],.02)

    def test_probability_truth_and_missing_bill(self):
        d=a.describe([row('a',target=.2,p=.4,target_kind='probability'),row('b',target=.5,p=.5,target_kind='probability',cost_usd=None)],'probability')
        self.assertAlmostEqual(d['quality']['mae_valid_only'],.1)
        self.assertAlmostEqual(d['quality']['rmse_valid_only'],math.sqrt(.02))
        self.assertAlmostEqual(d['quality']['expected_brier_valid_only'],.225)
        self.assertIsNone(d['cost']['total_billed_usd'])
        self.assertIsNone(d['cost']['usd_per_1000_attempts'])
        self.assertEqual(d['cost']['known_billed_usd'],.01)
        self.assertNotIn('accuracy_all_attempts',d['quality'])

    def test_wilson_does_not_make_perfect_small_sample_certain(self):
        lo,hi=a.wilson(60,60)
        self.assertAlmostEqual(lo,.93982814785791)
        self.assertAlmostEqual(hi,1)
        self.assertIsNone(a.wilson(0,0))
        self.assertAlmostEqual(a.mcnemar_exact(3,0),.25)
        self.assertEqual(a.mcnemar_exact(0,0),1)

    def test_clustered_boolq_preserves_duplicate_passages(self):
        cases={c['id']:c for c in [case('a','boolq',passage='same'),case('b','boolq',passage='same'),case('c','boolq',passage='other')]}
        rows=[]
        for cid in cases:
            rows.extend([row(cid,'Jev',p=0,experiment='boolq'),row(cid,'Luna',p=1 if cid!='c' else 0,experiment='boolq')])
        p=a.paired_quality(rows,cases,'label',draws=200,seed=2)
        self.assertEqual(p['paired_cases'],3)
        self.assertEqual(p['resampling_clusters'],2)
        self.assertAlmostEqual(p['metrics']['accuracy_all_attempts_difference']['estimate'],2/3)
        self.assertEqual(p['metrics']['accuracy_all_attempts_difference']['ci95'],[0,1])
        self.assertIsNone(p['metrics']['balanced_accuracy_all_attempts_difference']['estimate'])
        self.assertEqual(p,a.paired_quality(rows,cases,'label',draws=200,seed=2))

    def test_failed_pairs_affect_accuracy_not_probability_scores(self):
        cases={'a':case('a'),'b':case('b')}
        rs=[row('a','Jev',p=1),row('a','Luna',p=None,valid=False),row('b','Jev',p=0),row('b','Luna',p=1)]
        p=a.paired_quality(rs,cases,'label',draws=100,seed=6)
        self.assertEqual(p['metrics']['accuracy_all_attempts_difference']['estimate'],0)
        self.assertEqual(p['valid_response_pairs'],1)
        self.assertEqual(p['invalid_response_pairs'],1)
        self.assertEqual(p['metrics']['brier_valid_pairs_difference']['estimate'],-1)
        self.assertLess(p['metrics']['brier_valid_pairs_difference']['defined_bootstrap_draws'],100)
        self.assertEqual(p['correctness_pairs']['luna_only_correct'],1)
        self.assertEqual(p['correctness_pairs']['jev_only_correct'],1)

    def test_duplicate_quality_rows_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate quality'):
            a.paired_quality([row(),row()],{'a':case('a')},'label')

    def test_timing_medians_cluster_repeats_and_exclude_incomplete(self):
        rs=[]
        for cid,values in [('a',[1,2,100]),('b',[3,4,100])]:
            for repeat,j in enumerate(values):
                rs.extend([row(cid,'Jev',phase='timing',repeat=repeat,latency_s=j),row(cid,'Luna',phase='timing',repeat=repeat,latency_s=2*j)])
        rs.extend([row('c','Jev',phase='timing',repeat=0),row('c','Luna',phase='timing',repeat=0),row('warm','Jev',phase='timing_warmup',latency_s=900)])
        t=a.timing_analysis(rs,{'status':'complete','repeats':3},draws=100,seed=4)
        d=t['tasks']['policy']
        self.assertEqual(d['complete_valid_paired_cases'],2)
        self.assertEqual(d['excluded_cases'],1)
        self.assertEqual(d['primary_case_median_latency_s'],{'Jev':3,'Luna':6})
        self.assertEqual(d['paired']['metrics']['ratio_of_case_medians']['estimate'],2)
        self.assertEqual(d['paired']['metrics']['ratio_of_case_medians']['ci95'],[2,2])
        self.assertEqual(t['warmup_attempts'],1)
        self.assertEqual(t['cost_all_attempts']['requests'],15)
        self.assertEqual(t['cost_measured']['requests'],14)
        self.assertEqual(d['repeat_blocks']['2']['Jev']['median_s'],100)

    def test_integration_separates_bulk_latency_and_billing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);run=root/'run';run.mkdir()
            cases=[case('a',target=1),case('b',target=0)]
            cp=root/'cases.jsonl';cp.write_text(''.join(json.dumps(c)+'\n' for c in cases))
            rr=[row('a','Jev',p=1,latency_s=500),row('a','Luna',p=1,latency_s=600),row('b','Jev',target=0,p=0),row('b','Luna',target=0,p=0)]
            (run/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rr))
            (run/'metadata.json').write_text(json.dumps({'status':'complete','concurrency_pairs':16}))
            summary=a.analyze(run,output_dir=root/'out',cases_path=cp,bootstrap_samples=50)
            self.assertIsNone(summary['timing'])
            self.assertIn('bulk_latency_diagnostic_only',summary['quality_run'])
            self.assertEqual(summary['quality_run']['cost_all_phases']['total_billed_usd'],.04)
            self.assertEqual(summary['tasks'],summary['experiments'])
            self.assertEqual(summary['quality_run']['missing_attempts'],0)
            self.assertTrue((root/'out/summary.json').exists())
            self.assertIn('No comparative speed conclusion', (root/'out/analysis.md').read_text())
            (run/'metadata.json').write_text(json.dumps({'status':'running'}))
            with self.assertRaisesRegex(ValueError,'still running'):
                a.analyze(run,output_dir=root/'out',cases_path=cp,bootstrap_samples=1)

    def test_nonfinite_predictions_and_empty_groups(self):
        for p in (None,float('nan'),float('inf'),True,-.1,1.1):
            self.assertFalse(a.valid(row(p=p)))
        empty=a.describe([],'label')
        self.assertIsNone(empty['quality']['accuracy_all_attempts'])
        paired=a.paired_quality([],{},'label',draws=50)
        self.assertEqual(paired['paired_cases'],0)
        self.assertIsNone(paired['metrics']['accuracy_all_attempts_difference']['ci95'])


if __name__=='__main__':unittest.main()
