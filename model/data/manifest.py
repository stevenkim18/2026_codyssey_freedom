"""Read only the three pinned K-PII-Bench files selected in stage 1."""
from pathlib import Path
import hashlib
import json
import math
import re

SPLITS = ('train', 'dev', 'test_track_a')
MODEL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = MODEL_ROOT / 'configs/assets.lock.json'


class ManifestError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _constant(value):
    raise ValueError('nonfinite_json_number')


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite_json_number')
    return result


def strict_json(value):
    return json.loads(value, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)


def load_dataset_lock(path):
    try:
        value = strict_json(Path(path).read_bytes())['dataset']
        if value['repo_id'] != 'woohyun212/k-pii-bench' or not re.fullmatch('[a-f0-9]{40}', value['revision']):
            raise ValueError
        labels = value['canonical_labels']
        if not isinstance(labels, list) or not labels or any(not isinstance(x, str) or not re.fullmatch('[A-Z][A-Z0-9_]*', x) for x in labels) or len(set(labels)) != len(labels):
            raise ValueError
        if set(value['splits']) != set(SPLITS) or len(value['artifacts']) != len(SPLITS):
            raise ValueError
        artifacts = {x['path']: x for x in value['artifacts']}
        if len(artifacts) != len(SPLITS):
            raise ValueError
        for split in SPLITS:
            info = value['splits'][split]
            relative = 'data/' + split + '.jsonl'
            item = artifacts[relative]
            expected = f"https://huggingface.co/datasets/{value['repo_id']}/resolve/{value['revision']}/{relative}"
            if (info['path'] != relative or type(info['expected_documents_from_upstream_metadata']) is not int
                    or info['expected_documents_from_upstream_metadata'] < 0
                    or type(item['size_bytes']) is not int or item['size_bytes'] < 0
                    or item['digest']['algorithm'] != 'sha256'
                    or not re.fullmatch('[a-f0-9]{64}', item['digest']['value']) or item['url'] != expected):
                raise ValueError
        return value
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ManifestError('invalid_dataset_lock') from None


def file_digest(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
            size += len(block)
    return size, digest.hexdigest()
