"""Offline actual tokenizer/GLiNER collator oracles, without model weights."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import warnings

from .conversion import ConversionError, convert_record
from .manifest import MODEL_ROOT, strict_json


def verify_runtime(tokenizer_dir):
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    attempts=[]
    def deny(*unused,**kw):
        attempts.append(1)
        raise RuntimeError('offline_runtime_connection_denied')
    previous=(socket.socket.connect,socket.socket.connect_ex,socket.create_connection)
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=deny
    try:
        from .gliner_planner import load_planner
        from gliner.data_processing.collator import UniEncoderSpanDataCollator
        labels=strict_json((MODEL_ROOT/'configs/conversion-plan.json').read_bytes())['label_mapping']
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter('always')
            planner=load_planner(tokenizer_dir,labels)
        results=[]
        for name,text,a,b,expected in [
            ('single','김가상',0,3,'converted'),
            ('particle','김가상에게 연락',0,3,'unaligned_entity_boundary'),
            ('repeat','김가상 김가상',4,7,'converted'),
            ('non_bmp','😀 김가상',2,5,'converted'),
            ('combining','e\u0301 김가상',3,6,'converted'),
            ('zwj','👩\u200d💻 김가상',4,7,'converted'),
        ]:
            row={'text':text,'entities':[{'type':'PERSON','start':a,'end':b,'surface':text[a:b]}]}
            try:
                value=convert_record(row,planner,labels)
                status='converted'
                for example,window in zip(value['examples'],value['windows'],strict=True):
                    for first,last,label in example['ner']:
                        if (window['word_offsets'][first][0],window['word_offsets'][last][1],label)!=(a,b,labels['PERSON']):
                            raise ValueError('actual_roundtrip_mismatch')
            except ConversionError as exc:
                status=exc.code
            if status!=expected:
                raise ValueError('actual_fixture_unexpected_result')
            results.append({'case':name,'expected':expected,'actual':status,'passed':True})

        # Fixed full label inventory, inclusive word ends, multi-label spans and
        # negatives pass through the real GLiNER collator on CPU.
        collator=UniEncoderSpanDataCollator(planner.config,data_processor=planner.processor)
        example={'tokenized_text':['alpha','beta','gamma'],
                 'ner':[[0,0,label] for label in labels.values()],
                 'ner_labels':list(labels.values())}
        example['ner'].append([1,2,labels['PERSON']])
        raw=planner.processor.collate_raw_batch([example])
        classes=raw['classes_to_id'][0]
        with warnings.catch_warnings(record=True) as collator_warnings:
            warnings.simplefilter('always')
            batch=collator([example])
        if any('truncat' in str(x.message).lower() for x in collator_warnings):
            raise ValueError('collator_truncated')
        tensor=batch['labels']
        if (list(tensor.shape)!=[1,36,18] or tensor.device.type!='cpu'
                or int(tensor.sum().item())!=19 or list(classes)!=list(labels.values())
                or tensor[0,13,classes[labels['PERSON']]-1].item()!=1):
            raise ValueError('collator_gold_or_shape_mismatch')
        negative=collator([{'tokenized_text':['public'], 'ner':[], 'ner_labels':list(labels.values())}])
        if int(negative['labels'].sum().item())!=0 or negative['labels'].shape[-1]!=18:
            raise ValueError('collator_negative_mismatch')
        if attempts:
            raise ValueError('unexpected_network_attempt')
        return {'status':'passed','scope':'Actual pinned tokenizer, B planning and GLiNER CPU collator only; no neural model/training/quality/GPU.',
                'planner':planner.metadata,'cases':results,
                'collator':{'label_shape':list(tensor.shape),'positive_cells':int(tensor.sum().item()),
                            'fixed_label_order_verified':True,'multi_label_and_inclusive_end_verified':True,
                            'negative_labels_zero':True,'truncation_warnings':False},
                'network_connection_attempts':0,'initialization_warning_count':len(emitted),
                'model_weights_loaded':False,'training_performed':False,
                'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    finally:
        socket.socket.connect,socket.socket.connect_ex,socket.create_connection=previous


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tokenizer-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.exists():
        raise SystemExit('output_already_exists')
    result=verify_runtime(args.tokenizer_dir)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps({'status':result['status'],'actual_cases':len(result['cases']),'collator':result['collator']}))


if __name__=='__main__':
    main()
