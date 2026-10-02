import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent

def load(name):
    s=importlib.util.spec_from_file_location('test_multi_'+name,ROOT/(name+'.py'))
    m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

A=load('analyze');V=load('verify')

def case(i,target=1,kind='label'):
    return {'id':f'c{i}','experiment':'policy' if kind=='label' else 'probability','target':target,'target_kind':kind,
            'state':{'fact':i},'question':'Is the condition true?','criteria':{'true':'yes','false':'no'}}

def row(c,model,p,**extra):
    return {'case_id':c['id'],'experiment':c['experiment'],'target':c['target'],'target_kind':c['target_kind'],
            'model_label':model,'probability':p,'valid':p is not None,'error':None if p is not None else 'HTTP 520',
            'cost_usd':.1,'latency_s':1.,'phase':'quality','repeat':0,**extra}


def full_row(c,label='Luna',sequence=0,cost=.1):
    model='typesafe/jev-1.13' if label=='Jev' else 'openai/gpt-6-luna'
    answer={'type':'noul','noul':c['target']}
    usage={'cost':cost,'completion_tokens':20,'completion_tokens_details':{'reasoning_tokens':0},'prompt_tokens_details':{'cached_tokens':0,'cache_write_tokens':0}}
    payload={'model':model,**V.shared(c)} if label=='Jev' else {'model':model,'messages':[{'role':'user','content':json.dumps(V.shared(c))}],
        'response_format':{'type':'json_schema','json_schema':{'name':'noul_answer','strict':True,'schema':V.SCHEMA}},
        'reasoning':{'effort':'none'},'max_tokens':128,'provider':{'only':['OpenAI'],'allow_fallbacks':False,'require_parameters':True},
        'prompt_cache_options':{'mode':'explicit'},'stream':False}
    response={'model':model,'provider':'TypeSafe' if label=='Jev' else 'OpenAI','usage':usage}
    if label=='Jev':response['answers']={'answer':answer}
    else:response['choices']=[{'message':{'content':json.dumps(answer)},'finish_reason':'stop'}]
    return row(c,label,c['target'],request=payload,response=response,answer=answer,usage=usage,cost_usd=cost,sequence=sequence,
        requested_model=model,response_model=model,provider=response['provider'],http_status=200,endpoint='/api/alpha/decisions' if label=='Jev' else '/api/v1/chat/completions')


class MultiAnalysisTests(unittest.TestCase):
    def test_reference_directions_and_generalized_discordance(self):
        cases={c['id']:c for c in [case(1,1),case(2,0)]}
        rs=[row(c,m,c['target'] if m!='Luna' else 1-c['target']) for c in cases.values() for m in ('Jev','Luna','Qwen3.8 Flash')]
        pair=A.paired_quality(rs,cases,'label','Qwen3.8 Flash','Luna',100,3)
        self.assertEqual(pair['metrics']['accuracy_all_attempts_difference']['estimate'],1)
        self.assertEqual(pair['correctness_pairs']['model_only_correct'],2)
        self.assertNotIn('luna_only_correct',pair['correctness_pairs'])
        self.assertEqual(len(A.comparison_pairs(['Jev','Luna','Qwen3.8 Flash'])),3)

    def test_invalid_answer_denominator_and_unknown_bill(self):
        c=case(1,1);r=row(c,'Jev',None,cost_usd=None)
        s=A.base.describe([r],'label')
        self.assertEqual(s['quality']['accuracy_all_attempts'],0)
        self.assertIsNone(s['quality']['brier_valid_only'])
        self.assertEqual(s['cost']['missing_bills'],1)
        self.assertIsNone(s['cost']['usd_per_1000_attempts'])

    def test_duplicate_and_target_tampering_rejected(self):
        c=case(1);r=row(c,'Jev',1)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            A.check_source([r,r],{c['id']:c},'quality')
        r['target']=0
        with self.assertRaisesRegex(ValueError,'mismatch'):
            A.check_source([r],{c['id']:c},'quality')

    def test_probability_pair_excludes_invalid_but_tracks_it(self):
        cases={c['id']:c for c in [case(1,.25,'probability'),case(2,.75,'probability')]}
        rs=[row(c,m,None if c['id']=='c2' and m=='Jev' else c['target']) for c in cases.values() for m in ('Jev','Qwen3.8 Flash')]
        s=A.paired_quality(rs,cases,'probability','Qwen3.8 Flash','Jev',20,9)
        self.assertEqual(s['paired_cases'],2);self.assertEqual(s['valid_response_pairs'],1)
        self.assertEqual(s['metrics']['mae_valid_pairs_difference']['estimate'],0)

    def test_timing_common_support_and_pairwise_sensitivity(self):
        models=['Jev','Luna','Qwen3.8 Flash'];cases={c['id']:c for c in [case(1),case(2)]}
        rs=[row(c,m,None if c['id']=='c2' and m=='Qwen3.8 Flash' and repeat==1 else 1,
                phase='timing',repeat=repeat,latency_s=1 if m=='Jev' else 4) for c in cases.values() for m in models for repeat in range(3)]
        s=A.timing_analysis(rs,cases,models,{'metadata':{'status':'complete'}},30,1)['tasks']['policy']
        self.assertEqual(s['common_complete_cases'],1)
        self.assertEqual(s['comparisons']['Luna / Jev']['paired_cases'],1)
        self.assertEqual(s['pairwise_complete_sensitivity']['Luna / Jev']['paired_cases'],2)
        self.assertEqual(s['comparisons']['Luna / Jev']['metrics']['ratio_of_case_medians']['estimate'],4)

    def cache_rows(self):
        return [row(case(i,i%2),m,i%2,phase='measured',pair_id='pair'+str(i//2),block=i//4) for i in range(8) for m in ['Jev','Luna uncached','Luna cached']]

    def test_cache_pair_clustering_and_missing_bill_ci(self):
        rs=self.cache_rows();next(r for r in rs if r['model_label']=='Luna cached')['cost_usd']=None
        s=A.cache_analysis(rs,{},30,2)
        p=s['comparisons']['Luna cached / Luna uncached']
        self.assertEqual(p['clusters'],4);self.assertEqual(p['fixed_blocks'],2)
        self.assertIsNone(p['metrics']['cost_ratio']['estimate']);self.assertIsNone(p['metrics']['cost_ratio']['ci95'])
        self.assertIsNone(s['arms']['Luna cached']['setup_inclusive_usd_per_1000_decisions'])

    def test_cache_unknown_prime_makes_setup_total_unknown(self):
        rs=self.cache_rows();r=copy.deepcopy(rs[-1]);r.update(phase='cache_prime',case_id='prime',cost_usd=None);rs.append(r)
        s=A.cache_analysis(rs,{},10,4)
        self.assertIsNotNone(s['arms']['Luna cached']['cost']['total_billed_usd'])
        self.assertIsNone(s['arms']['Luna cached']['setup_inclusive_cost']['total_billed_usd'])

    def test_cache_unpaired_counterfactual_rejected(self):
        rs=[r for r in self.cache_rows() if not(r['case_id']=='c0' and r['model_label']=='Luna cached')]
        with self.assertRaisesRegex(ValueError,'counterfactual'):
            A.cache_analysis(rs,{},10,4)

    def test_analysis_preserves_source_provenance_and_missing_models(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);c=case(1);cp=d/'cases.jsonl';cp.write_text(json.dumps(c)+'\n')
            run=d/'run';run.mkdir();(run/'metadata.json').write_text(json.dumps({'status':'complete','cases_sha256':A.base.digest(cp)}))
            (run/'responses.jsonl').write_text(json.dumps(row(c,'Jev',1))+'\n')
            s=A.analyze([run],cases_path=cp,models=['Jev','Luna'],output_dir=d/'out',bootstrap_samples=10)
            self.assertEqual(s['quality']['missing_attempts'],1)
            self.assertEqual(s['tasks']['policy']['models']['Luna']['missing_attempts'],1)
            self.assertEqual(s['model_provenance']['Jev']['source_runs'],[str(run.resolve())])


class VerificationTests(unittest.TestCase):
    def test_contract_tampering_and_unknown_counter(self):
        c=case(1);r=full_row(c);issues,obs=V.check_record(r,c,{})
        self.assertEqual(issues,[])
        r['request']['max_tokens']=129
        self.assertIn('max_tokens_128',V.check_record(r,c,{})[0])
        r=full_row(c);del r['response']['usage']['completion_tokens_details'];r['usage']=r['response']['usage']
        issues,obs=V.check_record(r,c,{})
        self.assertEqual(issues,[]);self.assertIsNone(obs['reasoning_tokens'])

    def test_raw_probability_tampering_detected(self):
        c=case(1);r=full_row(c);r['probability']=.2
        self.assertIn('probability_readback',V.check_record(r,c,{})[0])
        r=full_row(c);r['request']['messages'][0]['content']=json.dumps({**V.shared(c),'answer':1})
        self.assertIn('source_input_exact',V.check_record(r,c,{})[0])

    def test_lost_outcome_has_explicit_unknown_timing(self):
        c=case(1);r=full_row(c,cost=None)
        r.update(valid=False,error='Lost response',probability=None,lost_outcome=True,latency_s=None,response=None)
        self.assertEqual(V.check_record(r,c,{})[0],[])

    def test_malformed_raw_record_retains_known_billing(self):
        c=case(1);r=full_row(c,cost=.2);raw=json.dumps(r)
        r.update(valid=False,error='Adapter JSON contract failure',probability=None,response=None,raw_adapter_record_json=raw)
        self.assertEqual(V.check_record(r,c,{})[0],[])

    def test_duplicate_answer_fields_and_completion_cap_are_detected(self):
        c=case(1);r=full_row(c)
        r['response']['choices'][0]['message']['content']='{"type":"noul","noul":0,"noul":1}'
        self.assertIn('valid_response_unparseable',V.check_record(r,c,{})[0])
        r=full_row(c);r['usage']['completion_tokens']=129
        self.assertIn('reported_output_within_128_cap',V.check_record(r,c,{})[0])

    def test_saved_plan_and_billing_verification(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);c=case(1);cp=d/'cases.jsonl';cp.write_text(json.dumps(c)+'\n')
            run=d/'run';run.mkdir();meta={'status':'complete','cases_sha256':A.base.digest(cp),'models':{'Luna':{'id':'openai/gpt-6-luna','provider':'OpenAI'}},'charged_usd':None}
            (run/'metadata.json').write_text(json.dumps(meta));(run/'responses.jsonl').write_text(json.dumps(full_row(c,cost=None))+'\n')
            (run/'plan.json').write_text(json.dumps([{'case_id':c['id'],'model':'Luna','phase':'quality','repeat':0}]))
            s=V.verify_run(run,cp);self.assertTrue(s['passed'],s['issues']);self.assertEqual(s['cost']['missing_bills'],1)
            meta['charged_usd']=0;(run/'metadata.json').write_text(json.dumps(meta))
            self.assertFalse(V.verify_run(run,cp)['checks']['metadata_billing_complete_or_unknown'])
            (run/'plan.json').write_text(json.dumps([{'case_id':'tampered','model':'Luna','phase':'quality','repeat':0}]))
            self.assertFalse(V.verify_run(run,cp)['passed'])

if __name__=='__main__':unittest.main()
