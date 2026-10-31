"""Download fixed public JSONL files with resumable partials and digest checks."""
from pathlib import Path
import argparse
import json
import os
import subprocess

from .manifest import DEFAULT_LOCK, MODEL_ROOT, SPLITS, ManifestError, file_digest, load_dataset_lock


class DownloadError(RuntimeError):
    pass


def _curl_fetch(url, partial, expected_size):
    subprocess.run([
        'curl', '-L', '--fail', '--silent', '--show-error', '--retry', '2',
        '--retry-delay', '1', '--connect-timeout', '15', '--max-time', '900',
        '--max-filesize', str(expected_size), '--continue-at', '-',
        '--output', str(partial), url,
    ], check=True)


def download_dataset(lock_path, destination, *, fetch=None):
    try:
        dataset = load_dataset_lock(lock_path)
    except ManifestError:
        raise DownloadError('invalid_dataset_lock') from None
    root = Path(destination)
    if root.is_symlink() or (root/'data').is_symlink():
        raise DownloadError('symlink_destination')
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {x['path']: x for x in dataset['artifacts']}
    fetch = fetch or _curl_fetch
    result = {'repo_id': dataset['repo_id'], 'revision': dataset['revision'], 'files': []}
    for split in SPLITS:
        item = artifacts[dataset['splits'][split]['path']]
        final = root / item['path']
        partial = final.with_suffix(final.suffix + '.part')
        if final.is_symlink() or partial.is_symlink():
            raise DownloadError('symlink_artifact')
        expected = (item['size_bytes'], item['digest']['value'])
        reused = final.exists()
        if reused:
            if not final.is_file() or file_digest(final) != expected:
                raise DownloadError('existing_file_integrity_mismatch')
        else:
            final.parent.mkdir(parents=True, exist_ok=True)
            if partial.exists() and (not partial.is_file() or partial.stat().st_size > item['size_bytes']):
                raise DownloadError('invalid_partial_file')
            try:
                if not partial.exists() or file_digest(partial) != expected:
                    fetch(item['url'], partial, item['size_bytes'])
            except (OSError, subprocess.SubprocessError):
                raise DownloadError('download_interrupted_partial_preserved') from None
            if not partial.is_file() or file_digest(partial) != expected:
                raise DownloadError('download_integrity_mismatch_partial_preserved')
            # Publish a complete verified file without overwriting any existing file.
            try:
                os.link(partial, final)
                partial.unlink()
            except OSError:
                raise DownloadError('publish_failed_partial_preserved') from None
        result['files'].append({'split': split, 'path': item['path'], 'size_bytes': expected[0],
                                'sha256': expected[1], 'verified': True, 'reused': reused})
        print(json.dumps({'split': split, 'verified': True, 'reused': reused}), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=DEFAULT_LOCK)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args(argv)
    try:
        lock = load_dataset_lock(args.lock)
        destination = args.destination or MODEL_ROOT / '.cache/k-pii-bench' / lock['revision']
        print(json.dumps({'stage': 'download_started', 'revision': lock['revision'],
                          'expected_bytes': sum(x['size_bytes'] for x in lock['artifacts'])}), flush=True)
        result = download_dataset(args.lock, destination)
        print(json.dumps({'status': 'verified', 'file_count': len(result['files'])}), flush=True)
        return 0
    except (DownloadError, ManifestError) as exc:
        print(json.dumps({'status': 'failed', 'error_code': str(exc)}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
