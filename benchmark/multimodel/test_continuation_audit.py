import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

def load(name):
 s=importlib.util.spec_from_file_location('audit_test_'+name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
A=load('continuation_audit');fixtures=load('test_multimodel_continuation')

class AmendmentAuditTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.ContinuationTests();self.fixture.setUp();self.f=self.fixture
  original=self.f.r.run_once
  self.f.r.run_once=lambda client,key,entry,prepared:original(client,'unrelated-synthetic-credential',entry,prepared)
 def tearDown(self):self.fixture.tearDown()
 def audit(self):return A.verify_continuations(self.f.run,lambda c,s:fixtures.FakeAdapter('fake').build_request(c,s))
 def test_success_preserves_auditable_history(self):
  self.f.resume();result=self.audit();self.assertTrue(result['passed'],result['issues']);self.assertEqual(result['linked_recovery_budget_accounted_usd'],.002)
 def test_retained_missing_bills_cannot_be_erased(self):
  self.f.resume();path=self.f.run/'responses.jsonl';rr=A.rows(path);rr[199]['cost_usd']=0
  path.write_text(''.join(json.dumps(r)+'\n' for r in rr));result=self.audit()
  self.assertFalse(result['passed']);self.assertFalse(result['checks']['amendment_1_responses.jsonl_preserved_prefix'])
 def test_prior_failed_probe_and_its_cost_remain_auditable(self):
  fixtures.FakeAdapter.probe_valid=False;self.f.resume();fixtures.FakeAdapter.probe_valid=True;self.f.resume()
  result=self.audit();self.assertTrue(result['passed'],result['issues']);self.assertAlmostEqual(result['linked_recovery_budget_accounted_usd'],.003)
 def test_forged_recovery_reasoning_is_detected(self):
  result=self.f.resume();p=Path(result['amendment']['recovery_run_dir']);rr=A.rows(p/'responses.jsonl');rr[0]['usage']['completion_tokens_details']['reasoning_tokens']=10
  (p/'responses.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rr));result=self.audit()
  self.assertFalse(result['passed']);self.assertFalse(result['checks']['amendment_1_probe_0_valid_observed_zero_reasoning'])
 def paced_resume(self):
  paced=load('continuation_paced');original=self.f.r.run_once
  def recorded(client,key,entry,prepared):
   r=original(client,key,entry,prepared)
   r.update(original_worker_setting=16,effective_worker_ceiling=2,effective_max_active_requests=2,min_dispatch_spacing_s=.25,quality_pacing_amendment='capacity-recovery-two-active')
   return r
  with patch.object(paced,'_load_protocol',return_value=self.f.r),patch.object(paced,'ROOT',self.f.p),patch.object(self.f.r,'run_once',recorded):
   return paced.resume_qwen('fake',self.f.run,budget=1)
 def test_original_and_paced_wrapper_hashes_are_both_supported(self):
  fixtures.FakeAdapter.probe_valid=False;self.f.resume();fixtures.FakeAdapter.probe_valid=True;self.paced_resume()
  result=self.audit();self.assertTrue(result['passed'],result['issues'])
  self.assertEqual([a['continuation_file'] for a in result['amendments']],['continuation.py','continuation_paced.py'])
 def test_paced_row_field_mutation_is_detected(self):
  self.paced_resume();p=self.f.run/'responses.jsonl';lines=p.read_text().splitlines(keepends=True);last=json.loads(lines[-1]);last['effective_worker_ceiling']=16
  lines[-1]=json.dumps(last)+'\n';p.write_text(''.join(lines));result=self.audit()
  self.assertFalse(result['checks']['amendment_1_paced_new_quality_row_fields'])
 def test_unsafe_continuation_filename_is_rejected(self):
  self.f.resume();p=self.f.run/'metadata.json';m=json.loads(p.read_text());m['operational_amendments'][0]['continuation_file']='../continuation.py';p.write_text(json.dumps(m))
  self.assertFalse(self.audit()['checks']['amendment_1_safe_continuation_filename'])
 def test_paced_dispatch_spacing_is_checked_from_the_journal(self):
  fields={'original_worker_setting':16,'effective_worker_ceiling':2,'effective_max_active_requests':2,'min_dispatch_spacing_s':.25}
  rr=[dict(fields,sequence=i,quality_pacing_amendment='capacity-recovery-two-active',concurrency_pairs=16) for i in range(2)]
  dd=[{'sequence':0,'dispatched_at':'2026-10-02T00:00:00+00:00'},{'sequence':1,'dispatched_at':'2026-10-02T00:00:00.250000+00:00'}]
  runtime=[{'effective_max_active_requests':2}]
  checks,_=A.paced_evidence(fields,fields,rr,dd,runtime);self.assertTrue(all(checks.values()))
  dd[1]['dispatched_at']='2026-10-02T00:00:00.100000+00:00'
  self.assertFalse(A.paced_evidence(fields,fields,rr,dd,runtime)[0]['observed_dispatch_spacing_at_least_0_25s'])

 def test_budget_omission_is_detected(self):
  self.f.resume();p=self.f.run/'metadata.json';m=json.loads(p.read_text());m['budget_usd']=1;p.write_text(json.dumps(m))
  self.assertFalse(self.audit()['checks']['quality_budget_excludes_all_linked_probe_charges'])

if __name__=='__main__':unittest.main()
