"""Tamper/failure tests for the independent verifier; mock records only."""
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('expanded_verifier',Path(__file__).with_name('verify_expanded.py'))
v=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(v)


class VerifyTests(unittest.TestCase):
    def fixture(self,base,failed=False,amend=False):
        run=base/'run';run.mkdir();cp=base/'cases.jsonl'
        case={'id':'case-a','experiment':'policy','state':{'x':3},'question':'Is x at least two?','criteria':{'true':'x >=2','false':'x<2'},'target':1,'target_kind':'label'}
        cp.write_text(json.dumps(case)+'\n');rng=random.Random(14);cl=[case];rng.shuffle(cl);arms=list(v.adapter.MODELS);rng.shuffle(arms)
        plan=[{'pair_index':0,'case_id':case['id'],'models':arms}];(run/'plan.json').write_text(json.dumps(plan))
        rows=[]
        for sequence,arm in enumerate(arms):
            endpoint,payload=v.adapter.payload_for(case,arm);answer={'type':'noul','noul':1};usage={'cost':.01,'completion_tokens_details':{'reasoning_tokens':0},'prompt_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}
            response={'model':v.adapter.MODELS[arm],'provider':'TypeSafe' if arm=='Jev' else 'OpenAI','usage':usage}
            if arm=='Jev':response['answers']={'answer':answer}
            else:response['choices']=[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]
            row={'case_id':case['id'],'experiment':'policy','model_label':arm,'target':1,'target_kind':'label','valid':True,'probability':1,'answer':answer,'response':response,'usage':usage,'provider':response['provider'],'response_model':response['model'],'requested_model':v.adapter.MODELS[arm],'cost_usd':.01,'error':None,'http_status':200,'request':payload,'endpoint':endpoint,'latency_s':1,'sequence':sequence,'pair_index':0,'phase':'quality','repeat':0,'measurement_role':'bulk_quality'}
            if failed and arm=='Jev':
                row.update(valid=False,probability=None,error='HTTP 520',http_status=520,cost_usd=None,response={},usage={},response_model=None,provider=None)
                row.pop('answer')
            rows.append(row)
        raw=''.join(json.dumps(r)+'\n' for r in rows);(run/'responses.jsonl').write_text(raw)
        source=Path(v.ROOT/'run_study.py');(run/'original-runner.py').write_bytes(source.read_bytes())
        meta={'status':'complete','seed':14,'cases_sha256':v.sha(cp),'runner_sha256':v.sha(source),'adapter_sha256':v.sha(v.PILOT/'runner.py'),'calls_completed':2,'planned_calls':2,'charged_usd':None if failed else .02,'known_charge_subtotal_usd':.01 if failed else .02,'unknown_billing':int(failed)}
        if amend:
            initial=(json.dumps(rows[0])+'\n').encode();snapshot=run/'metadata-before-amendment-1.json';snapshot.write_text(json.dumps({'status':'stopped'}))
            meta['protocol_amendments']=[{'initial_raw_prefix_bytes':len(initial),'initial_raw_prefix_sha256':v.sha_bytes(initial),'initial_metadata_snapshot':snapshot.name,'initial_metadata_sha256':v.sha(snapshot),'code_sha256':v.sha(v.ROOT/'continue_quality.py'),'initial_code_sha256':meta['runner_sha256'],'frozen_plan_sha256':v.sha(run/'plan.json'),'initial_attempts':1,'unattempted_calls':1}]
        (run/'metadata.json').write_text(json.dumps(meta))
        return run,cp,rows

    def test_complete_quality_reconciles_exact_requests(self):
        with tempfile.TemporaryDirectory() as t:
            run,cp,_=self.fixture(Path(t));r=v.verify_quality(run,cp)
            self.assertTrue(r['passed'],r['issues'])
            self.assertEqual(r['attempts'],2)
            self.assertEqual(r['billing']['total_billed_usd'],.02)

    def test_failed_attempt_and_unknown_bill_are_retained(self):
        with tempfile.TemporaryDirectory() as t:
            run,cp,_=self.fixture(Path(t),failed=True);r=v.verify_quality(run,cp)
            self.assertTrue(r['passed'],r['issues'])
            self.assertEqual(r['invalid'],1)
            self.assertEqual(r['failed_attempts'][0]['http_status'],520)
            self.assertEqual(r['billing']['missing_bills'],1)
            self.assertIsNone(r['billing']['total_billed_usd'])

    def test_payload_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            run,cp,rows=self.fixture(Path(t));rows[0]['request']['model']='other/model'
            (run/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));r=v.verify_quality(run,cp)
            self.assertFalse(r['passed'])
            self.assertIn('payload_exact',r['issues']['all_requests_responses_and_targets_match'][0]['issues'])

    def test_duplicate_attempt_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as t:
            run,cp,rows=self.fixture(Path(t));rows.append(rows[0]);(run/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            r=v.verify_quality(run,cp)
            self.assertFalse(r['checks']['all_expected_identities_exactly_once'])

    def test_amendment_prefix_and_snapshot_verified(self):
        with tempfile.TemporaryDirectory() as t:
            run,cp,rows=self.fixture(Path(t),amend=True);r=v.verify_quality(run,cp)
            self.assertTrue(r['passed'],r['issues'])
            rows[0]['latency_s']=2;(run/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            r=v.verify_quality(run,cp)
            self.assertFalse(r['checks']['amendment_1_original_prefix_unchanged'])


if __name__=='__main__':unittest.main()
