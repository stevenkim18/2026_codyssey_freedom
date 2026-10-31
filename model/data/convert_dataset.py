"""Inspect/export strict diagnostic GLiNER rows; never assert training readiness."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import socket
import time

from .conversion import ConversionError, convert_record, _tokens as checked_tokens
from .manifest import DEFAULT_LOCK, MODEL_ROOT, SPLITS, file_digest, load_dataset_lock, strict_json


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _alignment_profile(row, tokens, max_span):
    starts = {a:i for i,(_,a,_) in enumerate(tokens)}
    ends = {b:i for i,(_,_,b) in enumerate(tokens)}
    counts = {'aligned': Counter(), 'unaligned': Counter(), 'too_wide': Counter()}
    for entity in row['entities']:
        a,b,kind = entity['start'],entity['end'],entity['type']
        if a not in starts or b not in ends:
            counts['unaligned'][kind] += 1
        elif ends[b]-starts[a]+1 > max_span:
            counts['too_wide'][kind] += 1
        else:
            counts['aligned'][kind] += 1
    return counts


def run_conversion(data_root, output_root, planner, labels, lock_path=DEFAULT_LOCK,
                   plan_path=MODEL_ROOT/'configs/conversion-plan.json', progress=None):
    """Write an exclusive diagnostic export plus a ledger for every source row.

    Every source document is wholly converted or rejected. Rejections do not
    silently become empty-negative training rows. Raw/tokenized text stays in
    the Git-ignored output directory; only aggregate findings are published.
    """
    started = time.monotonic()
    dataset = load_dataset_lock(lock_path)
    plan = strict_json(Path(plan_path).read_bytes())
    if list(labels.items()) != list(plan['label_mapping'].items()) or set(labels) != set(dataset['canonical_labels']):
        raise ValueError('mapping_plan_mismatch')
    root = Path(data_root)
    out = Path(output_root)
    if out.exists() or out.is_symlink():
        raise ValueError('output_already_exists')
    artifacts = {x['path']:x for x in dataset['artifacts']}
    # Verify every source before writing any diagnostic output.
    for split in SPLITS:
        item = artifacts[dataset['splits'][split]['path']]
        source = root/item['path']
        if source.is_symlink() or file_digest(source) != (item['size_bytes'],item['digest']['value']):
            raise ValueError('dataset_integrity_mismatch')
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'schema_version':'1.0', 'status':'diagnostic_only',
        'created_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'dataset_revision':dataset['revision'], 'assets_lock_sha256':_sha(lock_path),
        'conversion_plan_sha256':_sha(plan_path), 'python_version':platform.python_version(),
        'label_mapping_status':plan['status'], 'planner':planner.metadata,
        'scope':'All-source exact gold-boundary diagnostics and B-compatible window planning. No model inference/training or performance evaluation.',
        'source_to_output_policy':'All gold and full non-whitespace text survive, or the entire source document is rejected with a reason.',
        'duplicate_policy':plan['duplicate_policy'], 'splits':{},
        'implementation_sha256':{name:_sha(Path(__file__).parent/name)
                                 for name in ('conversion.py','convert_dataset.py','gliner_planner.py')},
        'training_performed':False, 'training_ready':False,
    }
    for split in SPLITS:
        summary = {'source_documents':0,'source_gold_entities':0,'converted_documents':0,
                   'rejected_documents':0,'examples':0,'converted_source_gold_entities':0,
                   'rejected_source_gold_entities':0,'unique_converted_texts':0,
                   'alignment_unavailable_entities':0,
                   'rejection_reasons':Counter(), 'converted_labels':Counter({k:0 for k in labels}),
                   'alignment_by_type':{key:Counter({k:0 for k in labels})
                                        for key in ('aligned','unaligned','too_wide')}}
        unique = set()
        source = root/dataset['splits'][split]['path']
        example_path = out/(split+'.diagnostic.jsonl')
        ledger_path = out/(split+'.ledger.jsonl')
        with source.open('r',encoding='utf-8') as handle, example_path.open('x',encoding='utf-8') as examples, ledger_path.open('x',encoding='utf-8') as ledger:
            for number,line in enumerate(handle,1):
                row = strict_json(line)
                count = len(row['entities'])
                summary['source_documents'] += 1
                summary['source_gold_entities'] += count
                profile_complete = False
                try:
                    tokenized = checked_tokens(planner,row['text'])
                    for group,counts in _alignment_profile(row,tokenized,planner.max_span).items():
                        summary['alignment_by_type'][group].update(counts)
                    profile_complete = True
                    converted = convert_record(row,planner,labels)
                except ConversionError as exc:
                    if not profile_complete:
                        summary['alignment_unavailable_entities'] += count
                    summary['rejected_documents'] += 1
                    summary['rejected_source_gold_entities'] += count
                    summary['rejection_reasons'][exc.code] += 1
                    ledger.write(json.dumps({'source_line':number,'status':'rejected','reason':exc.code})+'\n')
                    continue
                summary['converted_documents'] += 1
                summary['converted_source_gold_entities'] += count
                unique.add(hashlib.sha256(row['text'].encode()).digest())
                summary['converted_labels'].update(entity['type'] for entity in row['entities'])
                first = summary['examples']
                for example in converted['examples']:
                    examples.write(json.dumps(example,ensure_ascii=False)+'\n')
                    summary['examples'] += 1
                ledger.write(json.dumps({'source_line':number,'status':'converted',
                                         'first_output_line':first+1,'example_count':len(converted['examples']),
                                         'windows':converted['windows']},ensure_ascii=False)+'\n')
                if progress and number%10000==0:
                    progress({'split':split,'source_documents':number})
        if summary['source_documents'] != dataset['splits'][split]['expected_documents_from_upstream_metadata']:
            raise ValueError('source_document_count_mismatch')
        if (summary['converted_documents']+summary['rejected_documents'] != summary['source_documents']
                or summary['converted_source_gold_entities']+summary['rejected_source_gold_entities'] != summary['source_gold_entities']):
            raise ValueError('conversion_ledger_mismatch')
        summary['unique_converted_texts'] = len(unique)
        summary['diagnostic_artifacts'] = {
            'examples':{'filename':example_path.name,'sha256':_sha(example_path),'size_bytes':example_path.stat().st_size},
            'ledger':{'filename':ledger_path.name,'sha256':_sha(ledger_path),'size_bytes':ledger_path.stat().st_size},
        }
        report['splits'][split] = summary
        if progress:
            progress({'split':split,'converted':summary['converted_documents'],
                      'rejected':summary['rejected_documents']})
    required = plan['required_unique_training_documents']
    report['gates'] = {
        'required_unique_train_documents':required,
        'available_unique_train_documents':report['splits']['train']['unique_converted_texts'],
        'minimum_train_count_met':report['splits']['train']['unique_converted_texts'] >= required,
        'all_source_documents_representable':all(x['rejected_documents']==0 for x in report['splits'].values()),
        'a_b_label_mapping_adopted':False,
    }
    report['status'] = 'blocked_compatibility'
    report['elapsed_seconds'] = round(time.monotonic()-started,3)
    report['raw_or_per_record_text_hashes_exported_in_report'] = False
    with (out/'completion.json').open('x') as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2)
        handle.write('\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokenizer-dir',type=Path,required=True)
    parser.add_argument('--data-root',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.report.exists():
        print(json.dumps({'status':'failed','error_code':'report_already_exists'}))
        return 2
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    attempts=[]
    def deny(*unused,**kw):
        attempts.append(1)
        raise RuntimeError('offline_conversion_network_denied')
    previous=(socket.socket.connect,socket.socket.connect_ex,socket.create_connection)
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=deny
    try:
        from .gliner_planner import load_planner
        dataset=load_dataset_lock(DEFAULT_LOCK)
        labels=strict_json((MODEL_ROOT/'configs/conversion-plan.json').read_bytes())['label_mapping']
        planner=load_planner(args.tokenizer_dir,labels)
        report=run_conversion(args.data_root or MODEL_ROOT/'.cache/k-pii-bench'/dataset['revision'],
                              args.output_dir,planner,labels,
                              progress=lambda value:print(json.dumps(value),flush=True))
        if attempts:
            raise ValueError('unexpected_network_attempt')
        report['network_connection_attempts']=len(attempts)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        with args.report.open('x') as handle:
            json.dump(report,handle,ensure_ascii=False,indent=2)
            handle.write('\n')
        print(json.dumps({'status':report['status'],'gates':report['gates'],'report':str(args.report)}))
        return 1
    except (ValueError,OSError,KeyError,TypeError):
        print(json.dumps({'status':'failed','error_code':'conversion_input_or_runtime_error'}))
        return 2
    finally:
        socket.socket.connect,socket.socket.connect_ex,socket.create_connection=previous


if __name__=='__main__':
    raise SystemExit(main())
