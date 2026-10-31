from pathlib import Path
import hashlib
import json

SPLITS = ('train', 'dev', 'test_track_a')
REVISION = 'a' * 40


def record(doc_id='d1', template_id='t1', text='김가상', entities=None):
    if entities is None:
        entities = [{'type': 'PERSON', 'start': 0, 'end': 3, 'surface': text[:3]}]
    return {
        'doc_id': doc_id, 'template_id': template_id, 'domain': 'chat',
        'locale': 'ko-KR', 'text': text, 'template_text': '틀-' + template_id,
        'entities': entities, 'meta': {}, 'tokens': [text],
        'bio_tags': ['B-' + entities[0]['type'] if entities else 'O'],
        'token_offsets': [[0, len(text)]],
    }


def dataset_fixture(root, records=None, raw=None):
    records = records or {}
    raw = raw or {}
    splits, artifacts = {}, []
    for split in SPLITS:
        rows = records.get(split, [])
        content = raw.get(split, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows).encode())
        relative = 'data/' + split + '.jsonl'
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        splits[split] = {'path': relative, 'expected_documents_from_upstream_metadata': len(rows)}
        artifacts.append({'path': relative, 'size_bytes': len(content),
                          'digest': {'algorithm': 'sha256', 'value': hashlib.sha256(content).hexdigest()},
                          'url': f'https://huggingface.co/datasets/woohyun212/k-pii-bench/resolve/{REVISION}/{relative}'})
    lock = {'dataset': {'repo_id': 'woohyun212/k-pii-bench', 'revision': REVISION,
                         'canonical_labels': ['PERSON', 'EMAIL_ADDRESS'],
                         'splits': splits, 'artifacts': artifacts}}
    path = root / 'lock.json'
    path.write_text(json.dumps(lock))
    return path


def change_lock(path, mutate):
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))
