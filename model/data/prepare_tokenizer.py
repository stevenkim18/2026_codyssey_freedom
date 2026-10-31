"""Prepare three unchanged pinned tokenizer files; never load model weights."""
from pathlib import Path
import argparse
import json
import os
import re
import subprocess

from .download import _curl_fetch
from .gliner_planner import _digest, _verify_artifact
from .manifest import DEFAULT_LOCK, MODEL_ROOT, strict_json

FILES = ('config.json', 'tokenizer_config.json', 'spm.model')


class TokenizerPreparationError(ValueError):
    pass


def _no_symlinks(path):
    supplied = Path(path).absolute()
    if any(part.is_symlink() for part in (supplied, *supplied.parents)):
        raise TokenizerPreparationError('tokenizer_symlink_path')


def _load_lock(path):
    try:
        _no_symlinks(path)
        value = strict_json(Path(path).read_bytes())['backbone']
        if value['repo_id'] != 'microsoft/mdeberta-v3-base' or not re.fullmatch('[a-f0-9]{40}',value['revision']):
            raise ValueError
        items = [item for item in value['artifacts'] if item['path'] in FILES]
        selected = {item['path']: item for item in items}
        if len(items) != 3 or set(selected) != set(FILES):
            raise ValueError
        for name,item in selected.items():
            expected = f"https://huggingface.co/{value['repo_id']}/resolve/{value['revision']}/{name}"
            algorithm = 'sha256' if name == 'spm.model' else 'git-blob-sha1'
            length = 64 if algorithm == 'sha256' else 40
            if (type(item['size_bytes']) is not int or item['size_bytes'] < 1
                    or item['digest']['algorithm'] != algorithm
                    or not re.fullmatch('[a-f0-9]{'+str(length)+'}',item['digest']['value'])
                    or item['url'] != expected):
                raise ValueError
        return value,selected
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise TokenizerPreparationError('invalid_tokenizer_lock') from None


def prepare_tokenizer(destination=None, *, lock_path=DEFAULT_LOCK,
                      metadata_root=MODEL_ROOT/'metadata', fetch=None):
    """Copy verified local JSON metadata and resume/download pinned spm.model."""
    value,artifacts = _load_lock(lock_path)
    root = Path(destination) if destination is not None else MODEL_ROOT/'.cache/tokenizer'/value['revision']
    metadata = Path(metadata_root)
    _no_symlinks(root)
    _no_symlinks(metadata)
    sources = {name:metadata/('backbone-'+name) for name in FILES if name != 'spm.model'}
    try:
        for name,path in sources.items():
            _no_symlinks(path)
            _verify_artifact(path,artifacts[name])
        if root.exists() and not root.is_dir():
            raise ValueError
        # Inspect all final destinations before creating or transferring files.
        for name,item in artifacts.items():
            final = root/name
            partial = root/(name+'.part')
            _no_symlinks(final)
            _no_symlinks(partial)
            if final.exists():
                _verify_artifact(final,item)
            if partial.exists():
                if not partial.is_file() or partial.stat().st_size > item['size_bytes']:
                    raise ValueError
                if name != 'spm.model':
                    _verify_artifact(partial,item)
    except (OSError, ValueError):
        raise TokenizerPreparationError('tokenizer_source_or_existing_file_invalid') from None

    root.mkdir(parents=True,exist_ok=True)
    fetch = fetch or _curl_fetch
    result = {'repo_id':value['repo_id'],'revision':value['revision'],
              'destination':str(root),'files':[],'model_weights_loaded':False}
    for name in FILES:
        item = artifacts[name]
        final = root/name
        partial = root/(name+'.part')
        reused = final.exists()
        if not reused:
            try:
                if name in sources:
                    if not partial.exists():
                        with partial.open('xb') as handle:
                            handle.write(sources[name].read_bytes())
                else:
                    complete = False
                    if partial.exists():
                        try:
                            _verify_artifact(partial,item)
                            complete = True
                        except ValueError:
                            pass
                    if not complete:
                        fetch(item['url'],partial,item['size_bytes'])
                _verify_artifact(partial,item)
                os.link(partial,final)
                partial.unlink()
            except (OSError, ValueError, subprocess.SubprocessError):
                raise TokenizerPreparationError('tokenizer_preparation_failed_partial_preserved') from None
        result['files'].append({'path':name,'verified':True,'reused':reused,
                                'sha256':_digest(final),'size_bytes':final.stat().st_size})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--lock',type=Path,default=DEFAULT_LOCK)
    parser.add_argument('--metadata-root',type=Path,default=MODEL_ROOT/'metadata')
    arguments = parser.parse_args(argv)
    try:
        result = prepare_tokenizer(arguments.destination,lock_path=arguments.lock,
                                   metadata_root=arguments.metadata_root)
        print(json.dumps({'status':'verified',**result},ensure_ascii=False),flush=True)
        return 0
    except (TokenizerPreparationError,OSError):
        print(json.dumps({'status':'failed','error_code':'tokenizer_preparation_failed'}),flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
