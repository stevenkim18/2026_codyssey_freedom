"""Read-only recovery of existing training provenance; safe to stream over SSH.

Uses only the standard library, never unpickles training/optimizer files and
never reads credentials or prints source text/training examples. JSON goes to
stdout; redirect it to a fresh ignored local path before publishing a summary.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_info(path, *, document=False):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return {'exists': False}
    result = {'exists': True, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
    if document:
        result['json'] = json.loads(path.read_text())
    return result


def sample_info(path):
    path = Path(path)
    result = file_info(path)
    if not result['exists']:
        return result
    domains, types, ids, texts = Counter(), Counter(), set(), set()
    rows = 0
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            rows += 1
            ids.add(row.get('document_id') or row.get('id') or row.get('doc_id'))
            texts.add(hashlib.sha256(row['text'].encode()).hexdigest())
            domains[str(row.get('domain', 'MISSING'))] += 1
            types.update(entity['type'] for entity in row.get('entities', []))
    result.update(documents=rows, unique_ids=len(ids), unique_texts=len(texts),
                  domains=dict(sorted(domains.items())), entities_by_type=dict(sorted(types.items())))
    return result


def main():
    home = Path('/home1/ncloud')
    work = home / 'work_model'
    base = home / 'b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0'
    roots = [base, home / 'm1_model', home / 'm1_checkpoints/checkpoint-631',
             home / 'm1_checkpoints/checkpoint-1262']
    packages = {}
    for name in ('torch', 'gliner', 'transformers', 'tokenizers', 'accelerate', 'safetensors', 'numpy'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    files = {}
    for root in roots:
        for name in ('pytorch_model.bin', 'model.safetensors', 'gliner_config.json', 'config.json',
                     'spm.model', 'tokenizer.json', 'tokenizer_config.json', 'manifest.json',
                     'added_tokens.json', 'special_tokens_map.json',
                     'trainer_state.json', 'training_args.bin', 'training_args.json'):
            path = root / name
            files[str(path)] = file_info(path, document=name.endswith('.json'))
    sources = {}
    for relative in ('model/training/run_stage6_m1.py', 'model/evaluation/evaluate_m0.py',
                     'model/segmentation/splitters.py', 'dev/detector/gliner_backend.py'):
        path = work / relative
        sources[relative] = file_info(path)
        if path.is_file():
            sources[relative]['source'] = path.read_text()
    samples = {name: sample_info(home / 'data' / name) if name == 'train_5k.jsonl'
               else file_info(home / 'data' / name)
               for name in ('train_5k.jsonl', 'train_5k_gliner.jsonl')}
    git = subprocess.run(['git', '-C', str(work), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    json.dump({'schema_version': '1.0', 'collected_at_utc': datetime.now(timezone.utc).isoformat(),
               'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
               'packages': packages, 'remote_git_commit': git.stdout.strip() if git.returncode == 0 else None,
               'files': files, 'sources': sources, 'samples': samples,
               'scope': 'read-only; hashes/configs/trainer JSON; no unpickling or GPU inference'},
              sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == '__main__':
    main()
