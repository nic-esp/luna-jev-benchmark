"""Freeze full BoolQ/RTE/WiC validation splits and larger exact synthetic tasks.
No network or model calls. Python standard library only. Public labels are retained.
Only state, question and criteria are model-visible. All other fields are metadata.
"""
from __future__ import annotations
import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import random

HERE=Path(__file__).resolve().parent
WORK=HERE.parents[2]/'work'
SEED=20261002
PILOT_GENERATOR_SHA='5d6f08efd3f3b0ead5354c82aefe9d582f5d4f273b0f0303f4f4662e935291b3'
SPLIT_COUNTS={'boolq':3270,'rte':277,'wic':638,'policy':300,'probability':300}


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_jsonl(path): return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
def write_jsonl(path,rows):
    Path(path).write_text(''.join(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n' for row in rows))
def pilot_module():
    path=HERE.parent/'datasets.py'
    if sha(path)!=PILOT_GENERATOR_SHA: raise ValueError('Pilot generator differs from verified dependency')
    spec=importlib.util.spec_from_file_location('frozen_pilot_datasets',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def make_case(identifier,experiment,state,question,target,kind,source,criteria=None):
    return {'id':identifier,'experiment':experiment,'state':state,'question':question,
            'criteria':criteria or {'true':'The answer to the stated question is yes.','false':'The answer to the stated question is no.'},
            'target':float(target),'target_kind':kind,'source':source}


def public_cases(task,rows,raw_sha,hf_revision,pilot_ids):
    assert len(rows)==SPLIT_COUNTS[task]
    result=[]
    for index,row in enumerate(rows):
        if task=='boolq':
            assert isinstance(row['answer'],bool)
            identifier=f'boolq-dev-{index:04d}'
            case=make_case(identifier,task,{'passage':row['passage']},row['question'].rstrip('?')+'?',int(row['answer']),'label',{
                'dataset':'BoolQ','split':'development','kind':'published_human_label','row_index_zero_based':index,
                'repository':'https://github.com/google-research-datasets/boolean-questions',
                'mirror':'https://huggingface.co/datasets/google/boolq','raw_jsonl_sha256':raw_sha,
                'license':'CC BY-SA 3.0','license_url':'https://creativecommons.org/licenses/by-sa/3.0/',
                'paper':'https://arxiv.org/abs/1905.10044','published_label':row['answer'],
                'label_mapping':'target_yes = int(answer)','pilot_overlap':identifier in pilot_ids,
                'overlap_policy':'New calls for every expanded case; pilot predictions are never reused.',
            },{'true':'The answer to the question is yes, using the supplied passage as evidence.','false':'The answer to the question is no, using the supplied passage as evidence.'})
        else:
            assert row['label'] in (0,1)
            source={'dataset':'SuperGLUE '+task.upper(),'configuration':task,'split':'validation','kind':'published_human_label',
                    'row_index_zero_based':index,'source_idx':row['idx'],'published_label':row['label'],
                    'hf_repository':'aps/super_glue','hf_revision_observed':hf_revision,
                    'mirror':'https://huggingface.co/datasets/aps/super_glue','raw_jsonl_sha256':raw_sha,
                    'source_identity_note':'Current accessible HF mirror is aps/super_glue; do not describe it as verified Google-owned.',
                    'pilot_overlap':False}
            if task=='rte':
                source.update(label_mapping='HF 0=entailment, 1=not_entailment; target_yes = 1-label',
                    task_source='https://www.tensorflow.org/datasets/catalog/super_glue#super_gluerte',
                    license='Original dataset terms; no unified license verified',license_status='unresolved_original_RTE_terms')
                case=make_case(f'rte-validation-{index:04d}',task,{'premise':row['premise'],'hypothesis':row['hypothesis']},
                    'Does the premise entail the hypothesis?',1-row['label'],'label',source,
                    {'true':'The hypothesis follows from the premise.','false':'The hypothesis does not follow from the premise; it may be unsupported or contradicted.'})
            else:
                state={'target_word':row['word']}
                for j in (1,2):
                    sentence=row[f'sentence{j}'];start=row[f'start{j}'];end=row[f'end{j}']
                    assert 0<=start<end<=len(sentence) and sentence[start:end].strip()
                    assert '[TARGET]' not in sentence and '[/TARGET]' not in sentence
                    state[f'sentence_{j}']=sentence[:start]+'[TARGET]'+sentence[start:end]+'[/TARGET]'+sentence[end:]
                source.update(label_mapping='HF 0=False, 1=True; target_yes = label',
                    task_source='https://pilehvar.github.io/wic/',license='CC BY-NC 4.0',
                    license_url='https://creativecommons.org/licenses/by-nc/4.0/',
                    target_offsets={f'{edge}{j}':row[f'{edge}{j}'] for j in (1,2) for edge in ('start','end')},
                    transformations='Inserted TARGET markers around the source character spans; inflected surface forms are retained.')
                case=make_case(f'wic-validation-{index:04d}',task,state,
                    'Does the word marked with [TARGET] and [/TARGET] have the same meaning in both sentences?',row['label'],'label',source,
                    {'true':'Both marked occurrences use the target word in the same sense.','false':'The marked occurrences use different senses, even if their topics are related.'})
        result.append(case)
    return result


def policy_truth(family,r):
    if family=='refund':
        return r['recalled'] or (r['receipt'] and not r['final_sale'] and ((r['defective'] and r['days_since_purchase']<=90) or (r['unused'] and r['days_since_purchase']<=30)))
    if family=='delivery':
        return r['mainland'] and not r['hazardous'] and r['weight_kg']<=20 and (r['subtotal_credits']>=100 or (r['member'] and r['subtotal_credits']>=50) or r['active_promotion'])
    if family=='access':
        return r['account_active'] and not r['suspended'] and (r['emergency_permit'] or (r['training_complete'] and ((r['role']=='staff' and r['clearance']>=2) or (r['role']=='contractor' and r['clearance']>=3 and r['sponsor']))))
    raise ValueError(family)


def policy_candidates(family):
    if family=='refund':
        keys=['recalled','receipt','final_sale','defective','unused','days_since_purchase']
        spaces=[[False,True]]*5+[[0,1,28,29,30,31,32,60,88,89,90,91,92,120]]
    elif family=='delivery':
        keys=['mainland','hazardous','weight_kg','subtotal_credits','member','active_promotion']
        spaces=[[False,True],[False,True],[0,1,18,19,19.9,20,20.1,21],[0,1,48,49,49.9,50,50.1,51,98,99,99.9,100,100.1,101],[False,True],[False,True]]
    else:
        keys=['account_active','suspended','emergency_permit','training_complete','role','clearance','sponsor']
        spaces=[[False,True]]*4+[['staff','contractor','visitor'],[0,1,2,3,4,5],[False,True]]
    return [dict(zip(keys,values)) for values in itertools.product(*spaces)]


def policy_anchor_pairs(family):
    """Six meaningful condition contrasts per family; no output-based selection."""
    if family=='refund':
        b=dict(recalled=False,receipt=True,final_sale=False,defective=False,unused=True,days_since_purchase=30)
        return [(b,dict(b,days_since_purchase=31)),
                (dict(b,defective=True,unused=False,days_since_purchase=90),dict(b,defective=True,unused=False,days_since_purchase=91)),
                (dict(b,days_since_purchase=29),dict(b,receipt=False,days_since_purchase=29)),
                (dict(b,days_since_purchase=0),dict(b,final_sale=True,days_since_purchase=0)),
                (dict(b,recalled=True,receipt=False,final_sale=True,unused=False,days_since_purchase=120),dict(b,receipt=False,final_sale=True,unused=False,days_since_purchase=120)),
                (dict(b,defective=True,unused=False,days_since_purchase=60),dict(b,defective=False,unused=False,days_since_purchase=60))]
    if family=='delivery':
        b=dict(mainland=True,hazardous=False,weight_kg=20,subtotal_credits=100,member=False,active_promotion=False)
        return [(b,dict(b,subtotal_credits=99.9)),(dict(b,member=True,subtotal_credits=50),dict(b,member=True,subtotal_credits=49.9)),
                (dict(b,subtotal_credits=101),dict(b,subtotal_credits=101,weight_kg=20.1)),
                (dict(b,weight_kg=19,subtotal_credits=101),dict(b,weight_kg=19,subtotal_credits=101,mainland=False)),
                (dict(b,member=True,subtotal_credits=51),dict(b,member=True,subtotal_credits=51,hazardous=True)),
                (dict(b,subtotal_credits=0,active_promotion=True),dict(b,subtotal_credits=0,active_promotion=False))]
    b=dict(account_active=True,suspended=False,emergency_permit=False,training_complete=True,role='staff',clearance=2,sponsor=False)
    return [(b,dict(b,clearance=1)),(dict(b,role='contractor',clearance=3,sponsor=True),dict(b,role='contractor',clearance=2,sponsor=True)),
            (dict(b,role='contractor',clearance=4,sponsor=True),dict(b,role='contractor',clearance=4,sponsor=False)),
            (dict(b,clearance=3),dict(b,clearance=3,training_complete=False)),
            (dict(b,role='visitor',emergency_permit=True),dict(b,role='visitor',emergency_permit=False)),
            (dict(b,role='visitor',emergency_permit=True,training_complete=False),dict(b,role='visitor',emergency_permit=True,training_complete=False,suspended=True))]


def build_policies(pilot,seed=SEED):
    rng=random.Random(seed+10)
    result=[]
    for family in ('refund','delivery','access'):
        anchors=[]
        for pair_index,(yes,no) in enumerate(policy_anchor_pairs(family)):
            assert policy_truth(family,yes) is True and policy_truth(family,no) is False
            anchors.extend([(yes,1,f'{family}-contrast-{pair_index+1}'),(no,0,f'{family}-contrast-{pair_index+1}')])
        anchor_keys={json.dumps(r,sort_keys=True) for r,_,_ in anchors}
        pools={label:[r for r in policy_candidates(family) if int(policy_truth(family,r))==label and json.dumps(r,sort_keys=True) not in anchor_keys] for label in (0,1)}
        selected=list(anchors)
        for label in (0,1): selected.extend((r,label,None) for r in rng.sample(pools[label],50-sum(y==label for _,y,_ in anchors)))
        rng.shuffle(selected)
        for r,label,pair_id in selected:
            question={'refund':'Is this item eligible for a refund under the supplied policy?','delivery':'Does this order qualify for free delivery under the supplied policy?','access':'Is this person allowed laboratory access under the supplied policy?'}[family]
            source={'kind':'synthetic_programmatic','rule_family':family,'generator':'expanded/datasets.py','seed':seed+10,
                    'truth_validation':'Boolean oracle plus independently specified condition-contrast anchors','anchor_pair_id':pair_id,
                    'sampling':'100 per family, 50 per label, including 6 yes/no contrast pairs; remaining records uniform without replacement within label',
                    'pilot_overlap':False,'shared_pilot_templates':True}
            result.append(make_case('', 'policy', {'policy':pilot.POLICIES[family],'record':r}, question,label,'label',source))
    rng.shuffle(result)
    for i,c in enumerate(result,1):c['id']=f'expanded-policy-{i:04d}'
    return result


def build_probabilities(pilot,seed=SEED):
    cases=[]
    # Keep model-visible probability problems distinct from each other and pilot.
    signatures={json.dumps({'state':c['state'],'question':c['question']},sort_keys=True)
                for c in read_jsonl(HERE.parent/'data/cases.jsonl') if c['experiment']=='probability'}
    counts=Counter();batch=0
    while min(counts.get(family,0) for family in ('conditional_table','without_replacement','weighted_mixture'))<100:
        batch_seed=seed+100+batch
        for c in pilot.build_probability_cases(batch_seed):
            family=c['source']['probability_family']
            if counts[family]>=100:continue
            signature=json.dumps({'state':c['state'],'question':c['question']},sort_keys=True)
            if signature in signatures:continue
            signatures.add(signature);counts[family]+=1
            c['source'].update(generator='expanded/datasets.py using pinned pilot probability generator',batch_index=batch,
                actual_rng_seed=batch_seed+1,pilot_overlap=False,shared_pilot_templates=True,
                selection='Keep first unique generated problems until 100 per family; exclude pilot-identical inputs.')
            cases.append(c)
        batch+=1
        if batch>100:raise ValueError('Unable to fill unique probability set')
    for i,c in enumerate(cases,1):c['id']=f'expanded-probability-{i:04d}'
    return cases


def validate(cases):
    assert len(cases)==4785 and len({c['id'] for c in cases})==4785
    assert Counter(c['experiment'] for c in cases)==SPLIT_COUNTS
    for c in cases:
        assert 0<=c['target']<=1 and c['target_kind'] in ('label','probability')
        if c['target_kind']=='label':assert c['target'] in (0,1)
        assert set(c['criteria'])=={'true','false'}
    forbidden={'target','target_kind','source','label','answer','published_label','truth_numerator','truth_denominator'}
    def visit(obj):
        if isinstance(obj,dict):
            assert not forbidden.intersection(obj)
            for value in obj.values():visit(value)
        elif isinstance(obj,list):
            for value in obj:visit(value)
    for c in cases:visit({key:c[key] for key in ('state','question','criteria')})


def build(output_dir=HERE/'data',work_dir=WORK):
    output_dir=Path(output_dir);work_dir=Path(work_dir)
    source_dir=HERE/'sources';source_dir.mkdir(parents=True,exist_ok=True)
    hf=json.loads((work_dir/'expanded/hf-superglue-metadata.json').read_text())
    pilot=pilot_module()
    pilot_rows=read_jsonl(HERE.parent/'data/cases.jsonl')
    pilot_ids={c['id'] for c in pilot_rows}
    raw_paths={'boolq':work_dir/'boolq-dev.jsonl','rte':work_dir/'expanded/rte-validation.jsonl','wic':work_dir/'expanded/wic-validation.jsonl'}
    groups={task:public_cases(task,read_jsonl(path),sha(path),hf['sha'],pilot_ids) for task,path in raw_paths.items()}
    groups['policy']=build_policies(pilot);groups['probability']=build_probabilities(pilot)
    for c in groups['policy']:
        family=c['source']['rule_family'];record=c['state']['record']
        overlaps=[old['id'] for old in pilot_rows if old['experiment']=='policy' and old['source']['rule_family']==family and all(old['state']['record'].get(k)==v for k,v in record.items())]
        c['source']['semantic_pilot_overlap_ids']=overlaps
    all_cases=[c for group in groups.values() for c in group];validate(all_cases)
    output_dir.mkdir(parents=True,exist_ok=True)
    files={}
    for task,records in groups.items():
        path=output_dir/f'{task}.jsonl';write_jsonl(path,records)
        files[task]={'file':path.name,'sha256':sha(path),'count':len(records),'target_kind':records[0]['target_kind'],
                     'labels':dict(Counter('yes' if c['target'] else 'no' for c in records)) if task!='probability' else None}
    write_jsonl(output_dir/'cases.jsonl',all_cases)
    source_notes=json.loads((work_dir/'expanded-source-notes.json').read_text())
    source_notes['source_identity']['hf_revision_observed']=hf['sha']
    source_notes['source_identity']['note']='Retrieved aps/super_glue rows API; observed dataset repository revision from live Hugging Face metadata. Rows API is not itself revision-pinned; normalized downloaded row hashes are authoritative for reproduction.'
    (source_dir/'provenance.json').write_text(json.dumps(source_notes,indent=2)+'\n')
    for task in ('rte','wic'):
        (source_dir/f'{task}-features.json').write_bytes((work_dir/f'expanded/{task}-features.json').read_bytes())
    manifest={'name':'Expanded Luna and Jev binary evaluation','version':1,'created_date':'2026-10-02','count':len(all_cases),
              'base_seed':SEED,'cases_sha256':sha(output_dir/'cases.jsonl'),'files':files,
              'pilot_generator_sha256':PILOT_GENERATOR_SHA,'generator_sha256':sha(__file__),
              'sampling':{'boolq':'All3270 published development rows; no filtering.','rte':'All277 published validation rows; no filtering.','wic':'All638 published validation rows; no filtering.',
                          'policy':'300 deterministic cases;100 per family;50 yes/50 no per family;6 informative contrast pairs per family plus seeded stratified samples.',
                          'probability':'300 exact-probability tasks;100 each conditional table, without replacement and weighted mixture; successive seeded batches, retaining first unique inputs and excluding pilot-identical inputs.'},
              'raw_sources':{task:{'normalized_jsonl_sha256':sha(path),'rows':len(groups[task])} for task,path in raw_paths.items()},
              'hf_superglue':{'repository':hf['id'],'observed_revision':hf['sha'],'last_modified':hf['lastModified'],
                             'rows_api_revision_pinned':False,'downloaded_content_hashes_authoritative':True},
              'label_mappings':{'boolq':'target_yes=int(answer)','rte':'0=entailment,1=not_entailment; target_yes=1-label','wic':'0=False,1=True; target_yes=label'},
              'pilot_overlap':{'boolq_case_count':60,'boolq_case_ids':sorted(c['id'] for c in groups['boolq'] if c['source']['pilot_overlap']),
                               'new_api_run_required':True,'reuse_predictions':False,'synthetic_templates_shared':True,
                               'policy_semantic_overlap_count':sum(bool(c['source']['semantic_pilot_overlap_ids']) for c in groups['policy']),
                               'probability_identical_input_overlap_count':0},
              'model_visible_fields':['state','question','criteria'],
              'ground_truth_status':{'boolq':'Published human labels retained, including known label contradiction1842.','rte':'Published human entailment labels retained.','wic':'Published human word-sense labels retained.','policy':'Programmatic Boolean oracle.','probability':'Exact rational event probability; not an observed event.'},
              'license_status':{'boolq':'CC BY-SA3.0','wic':'CC BY-NC4.0; source authors explicit','rte':'Original RTE terms; no unified permissive license verified'},
              'limitations':['Public validation sets may overlap model training; no unseen-data generalization claim.','The dataset is larger, but evaluation reliability also requires repeated timing and provider control.','Public labels are annotations, not infallible truth.','WiC sense identity and RTE entailment can be ambiguous; no model-assisted label corrections applied.','Synthetic cases share task templates and cannot be treated as independent real-world domains.','No official SuperGLUE leaderboard claim: only binary RTE and WiC tasks are included, with a probability-output adaptation.','Known BoolQ label audit remains post-hoc; primary labels unchanged.'],
              'source_provenance_file':'../sources/provenance.json'}
    (output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,default=HERE/'data');parser.add_argument('--work-dir',type=Path,default=WORK)
    args=parser.parse_args();result=build(args.output_dir,args.work_dir)
    print(json.dumps({'count':result['count'],'cases_sha256':result['cases_sha256'],'files':result['files']},indent=2))
