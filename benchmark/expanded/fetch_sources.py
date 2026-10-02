"""Recreate raw source JSONL under workspace work/; no model calls.
Public dataset downloads only. Requires network access. Fails if content differs
from the source hashes used for the frozen benchmark, rather than silently
regenerating a benchmark with changed labels. Python standard library only.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

HERE=Path(__file__).resolve().parent
WORK=HERE.parents[2]/'work'
EXPECTED={
 'boolq':('google/boolq','default',3270,'e8fb84fbf510b022e963cddf3a3aded04151afa0ea0ef1cc1bf22f260ddd2344'),
 'rte':('aps/super_glue','rte',277,'eec4bac5953538dc63792265f59f7df4f68c0048bcacd3d07336b7d7dc60db40'),
 'wic':('aps/super_glue','wic',638,'dccc5a7709e2d016ed6dcab6566039da37dc2a6a1be2fd500f8b04952a5dcaec'),
}

def get_json(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Luna-Jev-reproducible-benchmark/1'})
    with urllib.request.urlopen(request,timeout=60) as response:return json.load(response)

def fetch():
    folder=WORK/'expanded';folder.mkdir(parents=True,exist_ok=True)
    for task,(repo,config,count,expected) in EXPECTED.items():
        def page(offset):
            query=urllib.parse.urlencode({'dataset':repo,'config':config,'split':'validation','offset':offset,'length':100})
            result=get_json('https://datasets-server.huggingface.co/rows?'+query)
            assert result['num_rows_total']==count
            assert all(not r.get('truncated_cells') for r in result['rows'])
            return result
        with ThreadPoolExecutor(max_workers=6) as pool:pages=list(pool.map(page,range(0,count,100)))
        rows=sorted([r for result in pages for r in result['rows']],key=lambda r:r['row_idx'])
        assert [r['row_idx'] for r in rows]==list(range(count))
        raw=''.join(json.dumps(r['row'],ensure_ascii=False,sort_keys=True)+'\n' for r in rows).encode()
        observed=hashlib.sha256(raw).hexdigest()
        if observed!=expected:raise RuntimeError(f'{task} content changed: expected {expected}, observed {observed}; frozen cases remain unchanged')
        target=WORK/'boolq-dev.jsonl' if task=='boolq' else folder/f'{task}-validation.jsonl'
        target.write_bytes(raw)
        if task!='boolq':(folder/f'{task}-features.json').write_text(json.dumps(pages[0]['features'],indent=2)+'\n')
        print(json.dumps({'dataset':task,'rows':count,'sha256':observed,'path':str(target)}))
    metadata=get_json('https://huggingface.co/api/datasets/aps/super_glue')
    if metadata['sha']!='3de24cf8022e94f4ee4b9d55a6f539891524d646':
        raise RuntimeError('HF repository revision changed; raw content hashes passed but review provenance before rebuilding')
    (folder/'hf-superglue-metadata.json').write_text(json.dumps(metadata)+'\n')
    (WORK/'expanded-source-notes.json').write_bytes((HERE/'sources/provenance.json').read_bytes())

if __name__=='__main__':fetch()
