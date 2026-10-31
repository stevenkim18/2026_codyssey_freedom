"""Complete-only inference and provenance guards, without heavyweight imports."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time


class InferenceRunError(ValueError):
    pass


def document_id(row):
    value = row.get('document_id') or row.get('id') or row.get('doc_id')
    if not isinstance(value, str) or not value:
        raise InferenceRunError('invalid_document_id')
    return value


def _ids(rows):
    ids = [document_id(row) for row in rows]
    if len(set(ids)) != len(ids):
        raise InferenceRunError('duplicate_document_id')
    return ids


def run_inference(backend, rows, name='dataset', *, prediction_path=None):
    """Persist status before failing; never score incomplete/error as clean.

    Existing output files are refused. Failure files are retained as evidence
    and cannot be confused with an executed record, which is only made after
    validate_prediction_coverage succeeds. No original text is written.
    """
    _ids(rows)
    handle = Path(prediction_path).open('x', encoding='utf-8') if prediction_path else None
    predictions, pred_rows = {}, []
    started = time.monotonic()
    try:
        for index, row in enumerate(rows):
            rid = document_id(row)
            tick = time.monotonic()
            status, findings = 'error', []
            try:
                result = backend.inspect([{'segment_id': rid, 'text': row['text']}])
                status = result.status
                if status == 'complete':
                    for finding in result.findings:
                        if (type(finding.start) is not int or type(finding.end) is not int
                                or not 0 <= finding.start < finding.end <= len(row['text'])
                                or type(finding.score) not in (int, float)
                                or not math.isfinite(finding.score) or not 0 <= finding.score <= 1):
                            raise InferenceRunError('invalid_finding')
                        findings.append({'type': finding.type, 'start': finding.start,
                                         'end': finding.end, 'score': finding.score})
                elif status not in ('incomplete', 'error'):
                    status = 'error'
            except Exception:
                # Exception strings may contain source text or credentials.
                status, findings = 'error', []
            output = {'document_id': rid, 'status': status, 'findings': findings,
                      'timing_ms': (time.monotonic() - tick) * 1000}
            pred_rows.append(output)
            if handle:
                handle.write(json.dumps(output, ensure_ascii=False, allow_nan=False) + '\n')
                handle.flush()
            if status != 'complete':
                raise InferenceRunError(f'inspection_not_complete:{status}:completed={index}')
            predictions[rid] = findings
            if (index + 1) % 1000 == 0 or index + 1 == len(rows):
                elapsed = time.monotonic() - started
                print(f'[{name}] {index + 1}/{len(rows)} complete; {elapsed:.1f}s', flush=True)
    finally:
        if handle:
            handle.close()
    validate_prediction_coverage(rows, pred_rows)
    return predictions, pred_rows


def validate_prediction_coverage(rows, prediction_rows):
    expected = set(_ids(rows))
    actual = []
    for row in prediction_rows:
        if row.get('status') != 'complete' or not isinstance(row.get('findings'), list):
            raise InferenceRunError('prediction_not_complete')
        actual.append(document_id(row))
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise InferenceRunError('prediction_coverage_mismatch')


def condition_id(configuration):
    """Digest actual shared conditions; caller must exclude model weights/paths.

    Ordered label prompts remain ordered in lists. Dict insertion order is not
    meaningful. Include dataset hash, split, thresholds, rules, policy, splitter,
    chunking and postprocessing. Artifact model hashes are recorded separately.
    """
    encoded = json.dumps(configuration, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode()
    return 'condition-sha256:' + hashlib.sha256(encoded).hexdigest()


def sha256_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def require_frozen_holdout(manifest, dataset_sha256):
    """A machine check of a human review attestation, not a substitute for it."""
    validation = manifest.get('validation', {})
    counts = validation.get('counts', {})
    frozen_at = manifest.get('frozen_at_utc')
    try:
        timestamp = datetime.fromisoformat(frozen_at.replace('Z', '+00:00'))
        valid_time = (timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0
                      and timestamp <= datetime.now(timezone.utc))
    except (AttributeError, TypeError, ValueError):
        valid_time = False
    if (manifest.get('frozen') is not True or manifest.get('sha256') != dataset_sha256
            or not re.fullmatch(r'service-holdout-v(?:[2-9]|[1-9][0-9]+)-frozen',manifest.get('dataset_version',''))
            or not isinstance(manifest.get('reviewer'), str) or not manifest['reviewer'].strip()
            or not valid_time or validation.get('status') != 'ready_to_freeze'
            or validation.get('errors') != [] or validation.get('warnings') != []
            or counts.get('documents') != 300 or counts.get('unreviewed_documents') != 0):
        raise InferenceRunError('holdout_not_reviewed_and_frozen')
