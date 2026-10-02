"""Synthetic closure ledger tests; no model or network calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import verify_aborted_cache as A
import cache_protocol as P
from test_verify_cache import FakeAdapter,SPECS

class AbortedCacheTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name)
  cases,primes,rulebook,hashes=P.load_source(P.SOURCE);spec=copy.deepcopy(SPECS[0]);client=FakeAdapter()
  plan,prepared=P.create_plan(client,[spec],cases,primes,hashes,17)
  P.atomic_json(self.run/'plan.json',plan)
  (self.run/'cases.jsonl').write_bytes((P.SOURCE/'cases.jsonl').read_bytes());(self.run/'rulebook.txt').write_text(rulebook)
  saved=[];journal=[]
  for e in plan['attempts'][:55]:
   c,s,request=prepared[e['sequence']]
   row=client.run(case=c,spec=s,arm_label=e['model_label'],phase=e['phase'],sequence=e['sequence'],endpoint=request['endpoint'],payload=request['payload'])
   row.update(e,experiment='cache_policy',target_kind='label',requested_model=s['id'],request=request['payload'],endpoint=request['endpoint'])
   if e['sequence']==54:row.update(valid=False,probability=None,cost_usd=None,latency_s=None,usage={},response={},http_status=None,lost_outcome=True,error='Unknown lost dispatched outcome')
   saved.append(row);journal.append({k:e[k] for k in ('sequence','request_sha256','case_id','model_label')})
  (self.run/'responses.jsonl').write_text(''.join(P.canonical(r)+'\n' for r in saved[:54]));(self.run/'dispatches.jsonl').write_text(''.join(P.canonical(r)+'\n' for r in journal))
  evidence={n:{'sha256':A.sha(self.run/n),'bytes':(self.run/n).stat().st_size} for n in ('responses.jsonl','dispatches.jsonl','plan.json')}
  with (self.run/'responses.jsonl').open('a') as f:f.write(P.canonical(saved[54])+'\n')
  (self.run/'close_aborted_cache.py').write_text('"""Synthetic closure fixture; never executed."""\n')
  meta={'status':'aborted','primary_analysis_eligible':False,'models':[spec],'calls_completed':55,'calls_dispatched':55,'source_dir':str(P.SOURCE),'source_hashes':hashes,'seed':17,'protocol_sha256':A.sha(Path(P.__file__)),'plan_sha256':A.sha(self.run/'plan.json'),'lost_outcome_n':1,'unresolved_dispatch_n':0,'charged_usd':None,'known_charge_subtotal_usd':.0054,'cost_missing_n':1,'unknown_bill_allowance_total_usd':.01,'budget_accounted_usd':.0154}
  closure={'status':'passed','checks':{'fixture_closed':True},'closure_script_sha256':A.sha(self.run/'close_aborted_cache.py'),'original_evidence':evidence,'original_saved_responses':54,'final_attempts':55,'valid':54,'lost_sequences_retained_as_unknown':[54],'cost':{'total_usd':None,'known_subtotal_usd':.0054,'missing_n':1,'unknown_bill_allowance_usd':.01,'budget_accounted_usd':.0154}}
  (self.run/'metadata.json').write_text(json.dumps(meta));(self.run/'aborted-session-audit.json').write_text(json.dumps(closure))
 def tearDown(self):self.tmp.cleanup()
 def audit(self):return A.verify_aborted_cache(self.run)
 def test_independent_closure_preserves_unknown_bill(self):
  r=self.audit();self.assertTrue(r['passed'],r['issues']);self.assertEqual(r['attempts'],55);self.assertEqual(r['lost_outcomes'],1);self.assertIsNone(r['cost']['total_billed_usd']);self.assertFalse(r['primary_analysis_eligible'])
 def test_original_prefix_mutation_fails(self):
  p=self.run/'responses.jsonl';text=p.read_text();p.write_text(text.replace('"probability":','"altered_probability":',1))
  self.assertFalse(self.audit()['checks']['responses.jsonl_original_prefix_preserved'])
 def test_lost_outcome_cannot_acquire_a_zero_bill(self):
  p=self.run/'responses.jsonl';lines=p.read_text().splitlines(keepends=True);r=json.loads(lines[-1]);r['cost_usd']=0;lines[-1]=json.dumps(r)+'\n';p.write_text(''.join(lines))
  self.assertFalse(self.audit()['checks']['lost_dispatches_have_no_fabricated_outcome'])
 def test_original_dispatch_cannot_be_removed(self):
  p=self.run/'dispatches.jsonl';p.write_text(''.join(p.read_text().splitlines(keepends=True)[:-1]))
  self.assertFalse(self.audit()['passed'])
 def test_reported_bill_subtotal_must_reconcile(self):
  p=self.run/'aborted-session-audit.json';r=json.loads(p.read_text());r['cost']['known_subtotal_usd']=0;p.write_text(json.dumps(r))
  self.assertFalse(self.audit()['checks']['closure_billing_complete_or_unknown'])

if __name__=='__main__':unittest.main()
