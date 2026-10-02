import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

def load(name):
    s=importlib.util.spec_from_file_location('continuation_test_'+name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

C=load('continuation')

class FakeAdapter:
    calls=[];probe_reasoning=0;probe_valid=True
    def __init__(self,key):self.key=key
    def close(self):pass
    def build_request(self,case,spec):
        return {'endpoint':'/fake','payload':{'model':spec['id'],'state':case['state'],'questions':{'answer':{'type':'noul','instructions':case['question'],'criteria':case['criteria']}}}}
    def reserve_usd(self,request,spec):return .001
    def run(self,case,spec,arm_label,phase,sequence,endpoint,payload):
        self.calls.append((phase,case['id'],sequence))
        valid=self.probe_valid if phase=='recovery_probe' else True
        p=case['target'] if valid else None;usage={'cost':.001,'completion_tokens_details':{'reasoning_tokens':self.probe_reasoning if phase=='recovery_probe' else 0}}
        response={'usage':usage,'choices':[{'message':{'content':json.dumps({'type':'noul','noul':p})},'finish_reason':'stop'}]}
        return {'valid':valid,'probability':p,'cost_usd':.001,'usage':usage,'response':response,'request':payload,'endpoint':endpoint,'latency_s':.01,'error':None if valid else 'Synthetic unavailable'}


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.r=load('run_study')
        self.r.PHASE_LOCK=self.p/'phase.lock';self.r.adapter_class=lambda:FakeAdapter
        FakeAdapter.calls=[];FakeAdapter.probe_reasoning=0;FakeAdapter.probe_valid=True
        self.cases=[{'id':f'case-{i}','experiment':'policy','state':{'approved':True},'question':'Approve?','criteria':{'true':'approved','false':'not approved'},'target':1,'target_kind':'label'}for i in range(201)]
        self.hashes={'cases_sha256':'frozen-fixture','timing_cases_sha256':'timing-fixture'}
        self.r.load_frozen_sources=lambda:(self.cases,[],self.hashes)
        self.spec={'label':C.LABEL,'id':'qwen/qwen3.8-flash','provider':'alibaba','reasoning':{'effort':'none'},'cache_control':'unsupported','prices':{'prompt':'0.000001','completion':'0.000001'}}
        self.run=self.p/'quality';self.run.mkdir();self.plan,self.prepared=self.r.create_plan(FakeAdapter('fake'),self.cases,[self.spec],'quality',42)
        self.r.save(self.run/'plan.json',self.plan)
        request_rows=[{'sequence':e['sequence'],'request_sha256':e['request_sha256'],**self.prepared[e['sequence']][2]} for e in self.plan]
        (self.run/'requests.jsonl').write_text(''.join(self.r.canonical(x)+'\n'for x in request_rows))
        rr=[]
        for e in self.plan[:200]:
            c,s,req=self.prepared[e['sequence']];valid=e['sequence']<195
            rr.append({**e,'experiment':c['experiment'],'target':1,'target_kind':'label','valid':valid,'probability':1 if valid else None,'cost_usd':.001 if valid else None,'error':None if valid else 'HTTP 429','request':req['payload'],'endpoint':req['endpoint']})
        (self.run/'responses.jsonl').write_text(''.join(self.r.canonical(x)+'\n'for x in rr))
        journal=[{'sequence':e['sequence'],'request_sha256':e['request_sha256'],'dispatched_at':'2026-10-02T00:00:00Z'}for e in self.plan[:200]]
        (self.run/'attempts.jsonl').write_text(''.join(self.r.canonical(x)+'\n'for x in journal))
        code={}
        for name,path in self.r.source_files().items():(self.run/name).write_bytes(path.read_bytes());code[name]=C.sha(path)
        self.meta={'status':'stopped','phase':'quality','concurrency_pairs':16,'models':[self.spec],'seed':42,'source_hashes':self.hashes,'code_hashes':code,'calls_completed':200,'planned_calls':201,'stop_reason':'Five consecutive invalid responses (all models)'}
        for name,key in [('plan.json','plan_sha256'),('requests.jsonl','requests_sha256'),('responses.jsonl','responses_sha256'),('attempts.jsonl','attempts_sha256')]:self.meta[key]=C.sha(self.run/name)
        self.r.save(self.run/'metadata.json',self.meta)
    def tearDown(self):self.tmp.cleanup()
    def resume(self):
        with patch.object(C,'_load_protocol',return_value=self.r),patch.object(C,'ROOT',self.p):
            return C.resume_qwen('fake',self.run,budget=1)
    def test_recovery_and_only_unattempted_dispatch_preserve_prefix(self):
        before=(self.run/'responses.jsonl').read_bytes();journal=(self.run/'attempts.jsonl').read_bytes()
        result=self.resume()
        self.assertEqual(result['metadata']['status'],'complete')
        self.assertEqual(len(FakeAdapter.calls),3);self.assertEqual([x[0]for x in FakeAdapter.calls],['recovery_probe','recovery_probe','quality'])
        self.assertEqual(FakeAdapter.calls[-1][2],200)
        self.assertTrue((self.run/'responses.jsonl').read_bytes().startswith(before));self.assertTrue((self.run/'attempts.jsonl').read_bytes().startswith(journal))
        self.assertEqual(result['metadata']['unknown_billing'],5)
        self.assertAlmostEqual(result['metadata']['unknown_billing_guard_usd'],.05)
        self.assertEqual(result['metadata']['invalid'],5)
        self.assertAlmostEqual(result['metadata']['budget_usd'],.998)
        self.assertIsNone(result['metadata']['charged_usd'])
        self.assertEqual(result['amendment']['original_attempts'],200)
        self.assertEqual(result['amendment']['recovery_calls'],2)
    def test_failed_probe_stops_without_benchmark_dispatch(self):
        FakeAdapter.probe_valid=False;before=C.sha(self.run/'responses.jsonl');result=self.resume()
        self.assertEqual(result['status'],'recovery_failed');self.assertEqual(len(FakeAdapter.calls),1)
        self.assertEqual(C.sha(self.run/'responses.jsonl'),before)
        self.assertEqual(result['amendment']['status'],'recovery_failed')
    def test_positive_or_missing_reasoning_refuses_recovery(self):
        FakeAdapter.probe_reasoning=4;result=self.resume()
        self.assertEqual(result['status'],'recovery_failed');self.assertFalse(any(x[0]=='quality' for x in FakeAdapter.calls))
    def test_tampered_prefix_rejected_before_probe(self):
        with (self.run/'responses.jsonl').open('a')as f:f.write('\n')
        with self.assertRaisesRegex(ValueError,'hash changed'):self.resume()
        self.assertEqual(FakeAdapter.calls,[])
    def test_active_phase_blocks_before_paid_probe(self):
        with self.r.isolated_phase('another_quality_run'):
            with self.assertRaisesRegex(RuntimeError,'active'):self.resume()
        self.assertEqual(FakeAdapter.calls,[])
    def test_cumulative_invalid_fraction_boundary_is_enforced(self):
        rr=C.rows(self.run/'responses.jsonl')
        for r in rr[-10:]:r.update(valid=False,probability=None,cost_usd=None)
        (self.run/'responses.jsonl').write_text(''.join(self.r.canonical(r)+'\n' for r in rr))
        self.meta['responses_sha256']=C.sha(self.run/'responses.jsonl');self.r.save(self.run/'metadata.json',self.meta)
        with self.assertRaisesRegex(ValueError,'below 5%'):self.resume()
        self.assertEqual(FakeAdapter.calls,[])
    def test_missing_reasoning_counter_cannot_establish_recovery(self):
        FakeAdapter.probe_reasoning=None;result=self.resume()
        self.assertEqual(result['status'],'recovery_failed');self.assertFalse(any(x[0]=='quality' for x in FakeAdapter.calls))
    def test_previous_failed_probe_charge_remains_reserved(self):
        FakeAdapter.probe_valid=False;self.resume()
        FakeAdapter.probe_valid=True;result=self.resume()
        self.assertAlmostEqual(result['amendment']['historical_recovery_budget_accounted_usd'],.001)
        self.assertAlmostEqual(result['metadata']['budget_usd'],.997)

    def test_nonconsecutive_stop_is_not_cleared(self):
        self.meta['stop_reason']='Observed/unknown charges exhausted the local budget';self.r.save(self.run/'metadata.json',self.meta)
        with self.assertRaisesRegex(ValueError,'only re-arms'):self.resume()
        self.assertEqual(FakeAdapter.calls,[])
    def test_live_circuit_rearmed_counts_and_budget_retained(self):
        prior=C.rows(self.run/'responses.jsonl');cls=C._armed_session_class(self.r,{'id':'test'})
        obj=cls(self.run,{'phase':'quality','concurrency_pairs':16},prior,set(range(200)),self.plan,1,None)
        self.assertIsNone(obj.guard.stop_reason);self.assertEqual(obj.circuit.groups['all models']['n'],200)
        self.assertEqual(obj.circuit.groups['all models']['invalid'],5);self.assertEqual(obj.circuit.groups['all models']['consecutive'],0)
        for i in range(4):self.assertIsNone(obj.circuit.add({'model_label':C.LABEL,'valid':False}))
        self.assertIn('Five consecutive',obj.circuit.add({'model_label':C.LABEL,'valid':False}))
        self.assertAlmostEqual(obj.guard.unknown_guard,.05)

if __name__=='__main__':unittest.main()
