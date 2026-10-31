"""Interrupt only this offline run, retaining provenance and partial predictions."""
import argparse
import json
import os
from pathlib import Path
import signal


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    if (args.run_dir/'result.json').exists() or (args.run_dir/'failed.json').exists():
        print('Run already finished; no process signalled.')
        return
    state=json.loads((args.run_dir/'run-state.json').read_text())
    pid=int(state['pid']);proc=Path('/proc')/str(pid)
    if not proc.exists():
        print('Process no longer exists; retained outputs unchanged.')
        return
    command=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode()
    if (str((proc/'cwd').resolve())!=state['workspace'] or 'model.evaluation.run_checkpoint' not in command
            or state['experiment_id'] not in command or proc.stat().st_uid!=os.getuid()):
        raise RuntimeError('Process identity mismatch; refusing to signal.')
    os.kill(pid,signal.SIGINT)
    print('Own offline run interrupted; partial predictions and provenance retained.')


if __name__=='__main__':main()
