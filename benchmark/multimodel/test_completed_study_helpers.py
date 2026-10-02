import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

def load(name):
 s=importlib.util.spec_from_file_location('test_helper_'+name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
V=load('verify_completed_study');A=load('analyze_completed_study')

class CompletedHelperTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.spec={'label':'Jev','id':'typesafe/jev-1.13','provider':'TypeSafe','reasoning':None,'cache_control':'unsupported','max_tokens':None}
  self.selection=self.root/'selection.json';self.selection.write_text(json.dumps({'models':[self.spec]}))
  self.quality=self.root/'quality';self.timing=self.root/'timing';self.cache=self.root/'cache';self.output=self.root/'verification.json'
  for d,phase in [(self.quality,'quality'),(self.timing,'timing'),(self.cache,'measured')]:
   d.mkdir();(d/'metadata.json').write_text(json.dumps({'status':'complete','models':[self.spec]}))
   (d/'responses.jsonl').write_text(json.dumps({'case_id':'synthetic-1','model_label':'Jev','phase':phase,'valid':True,'probability':1,'cost_usd':None})+'\n')
 def tearDown(self):self.temp.cleanup()
 def run_audit(self,**kw):
  kw.setdefault('supplementary_cache_dirs',[])
  return V.verify_completed([self.quality],self.timing,self.cache,probe_dirs=[],selection_path=self.selection,output_path=self.output,**kw)
 def test_complete_audit_retains_unknown_cost(self):
  with patch.object(V.audit,'verify_study',return_value={'passed':True}):result=self.run_audit()
  self.assertTrue(result['passed']);self.assertEqual(result['totals']['cost']['missing_bills'],3);self.assertIsNone(result['totals']['cost']['total_billed_usd'])
 def test_active_run_stops_before_protocol_audit(self):
  p=self.timing/'metadata.json';m=json.loads(p.read_text());m['status']='running';p.write_text(json.dumps(m))
  with patch.object(V.audit,'verify_study') as fn:result=self.run_audit();fn.assert_not_called()
  self.assertFalse(result['passed']);self.assertFalse(result['checks']['all_primary_runs_complete'])
 def test_component_failure_cannot_be_overridden(self):
  with patch.object(V.audit,'verify_study',return_value={'passed':False}):result=self.run_audit()
  self.assertFalse(result['passed']);self.assertEqual(result['status'],'failed')
 def test_mutation_during_verification_fails(self):
  def mutate(*a,**kw):
   with (self.quality/'responses.jsonl').open('a') as f:f.write('\n')
   return {'passed':True}
  with patch.object(V.audit,'verify_study',side_effect=mutate):result=self.run_audit()
  self.assertFalse(result['checks']['all_inputs_unchanged_during_audit'])
 def test_omitted_recovery_ledger_fails(self):
  recovery=self.root/'recovery';recovery.mkdir();(recovery/'responses.jsonl').write_text('{}\n')
  p=self.quality/'metadata.json';m=json.loads(p.read_text());m['operational_amendments']=[{'recovery_run_dir':str(recovery)}];p.write_text(json.dumps(m))
  with patch.object(V.audit,'verify_study',return_value={'passed':True}):result=self.run_audit()
  self.assertFalse(result['checks']['all_linked_recovery_probes_in_ledger'])
 def test_aborted_cache_is_in_ledger_but_excluded_from_primary_inputs(self):
  supplementary=self.root/'aborted';supplementary.mkdir()
  (supplementary/'metadata.json').write_text(json.dumps({'status':'aborted','primary_analysis_eligible':False}))
  (supplementary/'responses.jsonl').write_text(json.dumps({'phase':'measured','valid':False,'cost_usd':None})+'\n')
  with patch.object(V.audit,'verify_study',return_value={'passed':True}) as study,patch.object(V.aborted_cache,'verify_aborted_cache',return_value={'passed':True,'primary_analysis_eligible':False}):
   r=self.run_audit(supplementary_cache_dirs=[supplementary])
  self.assertTrue(r['passed']);self.assertEqual(r['totals']['attempts'],4);self.assertEqual(r['totals']['cost']['missing_bills'],4)
  entry=next(e for e in r['ledger'] if e['role']=='supplementary_aborted_cache')
  self.assertFalse(entry['primary_estimate_eligible']);self.assertEqual(study.call_args.args[2],[self.cache.resolve()])

 def test_analysis_requires_passed_verification(self):
  self.output.write_text(json.dumps({'passed':False,'status':'failed'}))
  with self.assertRaisesRegex(ValueError,'passed'):A.preflight([self.quality],self.timing,self.cache,['Jev'],self.output)
 def test_analysis_rejects_changed_input_path(self):
  with patch.object(V.audit,'verify_study',return_value={'passed':True}):self.run_audit()
  with self.assertRaisesRegex(ValueError,'Explicit inputs'):A.preflight([self.quality],self.cache,self.timing,['Jev'],self.output)

if __name__=='__main__':unittest.main()
