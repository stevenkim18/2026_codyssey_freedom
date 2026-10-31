"""Seeded, domain-balanced, type-covering unique train sampling and verified cache.

Two bounded priority pools (domain and rare-type anchors) retain original rows.
Conversion remains all-or-nothing. No official dev/test or model scores select
training rows. Existing caches require exact source/config/output digests.
"""
from collections import Counter
import argparse
import hashlib
import heapq
import json
import math
from pathlib import Path

from .conversion import ConversionError, convert_record
from ..evaluation.runner import document_id, sha256_file


class SampleError(ValueError):
    pass


def _offer(heap,item,limit):
    if len(heap)<limit:
        heapq.heappush(heap,item)
    elif item>heap[0]:
        heapq.heapreplace(heap,item)


def prepare_sample(source,output,planner,mapping,*,domains,count=5000,seed=42,source_sha256):
    source,output=Path(source),Path(output)
    if sha256_file(source)!=source_sha256:
        raise SampleError('source_hash_mismatch')
    domains=sorted(domains)
    if not domains or len(set(domains))!=len(domains) or count<max(len(mapping),len(domains)):
        raise SampleError('invalid_sample_configuration')
    configuration={'algorithm':'domain_priority_type_anchors_v1','count':count,'seed':seed,
                   'source_sha256':source_sha256,'domains':domains,'label_mapping':list(mapping.items()),
                   'planner':planner.metadata,'implementation_sha256':sha256_file(__file__)}
    # Normalize JSON tuple/list representation before storing/comparing.
    configuration=json.loads(json.dumps(configuration,sort_keys=True))
    if output.exists():
        try:
            manifest=json.loads((output/'manifest.json').read_text())
            if manifest['selection_configuration']!=configuration:
                raise SampleError('sample_cache_configuration_mismatch')
            for name,digest in manifest['files_sha256'].items():
                if Path(name).name!=name or (output/name).is_symlink() or sha256_file(output/name)!=digest:
                    raise SampleError('sample_cache_payload_mismatch')
            if manifest['summary']['documents']!=count or manifest['summary']['unique_texts']!=count:
                raise SampleError('sample_cache_count_mismatch')
            return manifest
        except (OSError,KeyError,ValueError) as exc:
            raise SampleError('sample_cache_not_verified') from exc
    per_domain={d:[] for d in domains}
    per_type={kind:[] for kind in mapping}
    capacity=max(8,2*math.ceil(count/len(domains)))
    seen_ids,seen_text=set(),set()
    scanned=duplicates=0
    with source.open() as handle:
        for line_number,line in enumerate(handle,1):
            row=json.loads(line)
            scanned+=1
            rid=document_id(row)
            if rid in seen_ids:
                raise SampleError('duplicate_source_document_id')
            seen_ids.add(rid)
            text_hash=hashlib.sha256(row['text'].encode()).hexdigest()
            if text_hash in seen_text:
                duplicates+=1
                continue
            seen_text.add(text_hash)
            domain=row.get('domain')
            kinds={entity['type'] for entity in row['entities']}
            if domain not in per_domain or not kinds<=set(mapping):
                raise SampleError('unexpected_domain_or_label')
            priority=int(hashlib.sha256(f'{seed}:{rid}:{text_hash}'.encode()).hexdigest(),16)
            item=(-priority,rid,line_number,row)
            _offer(per_domain[domain],item,capacity)
            for kind in kinds:
                _offer(per_type[kind],item,32)
    converted={};rejections=Counter();selected={};domain_counts=Counter();type_counts=Counter()
    def accept(item):
        _,rid,line,row=item
        if rid in selected:
            return True
        if rid not in converted:
            try:
                converted[rid]=convert_record(row,planner,mapping)
            except ConversionError as exc:
                converted[rid]=None
                rejections[exc.code]+=1
        if converted[rid] is None:
            return False
        selected[rid]=(line,row,converted[rid])
        domain_counts[row['domain']]+=1
        type_counts.update(e['type'] for e in row['entities'])
        return True
    # First ensure each supported type survives actual conversion.
    for kind in mapping:
        if type_counts[kind]:continue
        if not any(accept(item) for item in sorted(per_type[kind],reverse=True)):
            raise SampleError('no_convertible_type_anchor:'+kind)
    quotas={d:count//len(domains)+(i<count%len(domains)) for i,d in enumerate(domains)}
    for domain in domains:
        for item in sorted(per_domain[domain],reverse=True):
            if domain_counts[domain]>=quotas[domain]:break
            accept(item)
        if domain_counts[domain]<quotas[domain]:
            raise SampleError('insufficient_convertible_domain_pool:'+domain)
    if len(selected)!=count or any(not type_counts[k] for k in mapping):
        raise SampleError('sample_invariant_failed')
    output.mkdir(parents=True,exist_ok=False)
    paths={name:output/name for name in ('train.sample.jsonl','train.gliner.jsonl','selection.jsonl')}
    windows=0
    with paths['train.sample.jsonl'].open('x') as raw,paths['train.gliner.jsonl'].open('x') as gliner,paths['selection.jsonl'].open('x') as ledger:
        for rid,(line,row,result) in selected.items():
            raw.write(json.dumps(row,ensure_ascii=False)+'\n')
            for example in result['examples']:
                gliner.write(json.dumps(example,ensure_ascii=False)+'\n')
                windows+=1
            ledger.write(json.dumps({'document_id':rid,'source_line':line,'domain':row['domain'],
                                    'windows':len(result['examples'])},ensure_ascii=False)+'\n')
    manifest={'schema_version':'1.0','selection_configuration':configuration,
              'source_scan':{'documents':scanned,'unique_texts':len(seen_text),'duplicate_texts_excluded':duplicates},
              'summary':{'documents':len(selected),'unique_texts':len(selected),'windows':windows,
                         'domains':dict(sorted(domain_counts.items())),
                         'entities_by_type':dict(sorted(type_counts.items()))},
              'conversion_rejections_in_candidate_pool':dict(rejections),
              'files_sha256':{name:sha256_file(path) for name,path in paths.items()},
              'scope':'train-only preparation; no training, model selection or final-test scoring performed'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--tokenizer-dir',type=Path,required=True)
    parser.add_argument('--count',type=int,default=5000)
    parser.add_argument('--seed',type=int,default=42)
    args=parser.parse_args(argv)
    root=Path(__file__).parents[1]
    lock=json.loads((root/'configs/assets.lock.json').read_text())['dataset']
    expected=next(x['digest']['value'] for x in lock['artifacts'] if x['path']=='data/train.jsonl')
    mapping=json.loads((root/'configs/conversion-plan.json').read_text())['label_mapping']
    domains=json.loads((root/'reports/data-audit-v2.json').read_text())['splits']['train']['domain_counts']
    from ..segmentation.planner import load_candidate_planner
    planner=load_candidate_planner(args.tokenizer_dir,mapping,'ko_atomic_suffix_v3')
    result=prepare_sample(args.source,args.output_dir,planner,mapping,domains=domains,
                          count=args.count,seed=args.seed,source_sha256=expected)
    print(json.dumps(result['summary'],ensure_ascii=False))


if __name__=='__main__':
    main()
