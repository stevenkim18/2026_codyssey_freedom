"""Actual per-process OS file-write failure; never fills the host filesystem."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = r'''
import asyncio, errno, json, resource, signal, sys
from pathlib import Path
import httpx
from gateway.delivery import BatchReceipt, LocalEventShipper
from gateway.engine import Gateway, GatewayError
from gateway.journal import Journal
from gateway.tests.test_journal import event
from gateway.tests.test_engine import Inspector
from gateway.transport import UpstreamClient

path, mode = Path(sys.argv[1]), sys.argv[2]
journal = Journal(path)
journal.append(event('durable-before-fault'))
old = resource.getrlimit(resource.RLIMIT_FSIZE)
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
received, central, attempts = [], {}, []

async def transport(request):
    received.append(json.loads(request.content))
    return httpx.Response(500)

async def send(events):
    ids = [e['event_id'] for e in events]
    attempts.append(ids)
    for e in events: central.setdefault(e['event_id'], e)
    return BatchReceipt(ids)

async def run():
    resource.setrlimit(resource.RLIMIT_FSIZE, (4096, old[1]))
    try:
        try:
            (path.parent / 'write-limit-canary').write_bytes(b'x' * 4097)
        except OSError as error:
            assert error.errno == errno.EFBIG
        else:
            raise AssertionError('OS write limit did not activate')
        if mode == 'append':
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                upstream = UpstreamClient(client, 'https://api.openai.com/v1/responses', 'synthetic', {'mock-model'})
                gateway = Gateway(Inspector(), upstream, journal, allowed_models={'mock-model'})
                try:
                    await gateway.handle({'model': 'mock-model', 'input': 'synthetic safe request'})
                except GatewayError as error:
                    assert error.code == 'JOURNAL_UNAVAILABLE'
                else:
                    raise AssertionError('storage failure allowed forwarding')
                assert received == []
        else:
            report = await LocalEventShipper(journal, send).run_once()
            assert report.error_code == 'JOURNAL_ACK_FAILED' and report.acknowledged == 0
            assert len(central) == 1
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, old)
        journal.close()
    # Reopen only after restoring writes, allowing SQLite's own recovery.
    with Journal(path) as recovered:
        assert recovered.pending() == [event('durable-before-fault')]
        report = await LocalEventShipper(recovered, send).run_once()
        assert report.acknowledged == 1 and report.error_code is None
        assert recovered.pending() == []
    if mode == 'ack': assert attempts == [['durable-before-fault']] * 2
    assert list(central) == ['durable-before-fault']
    print(json.dumps({'status': 'passed', 'fault': 'OS_EFBIG', 'mode': mode,
        'external_received': len(received), 'central_unique': len(central),
        'pending_preserved_after_restart': True, 'recovered_acknowledged': 1}))

asyncio.run(run())
'''


@pytest.mark.parametrize('mode', ['append', 'ack'])
def test_os_write_failure_blocks_forwarding_and_preserves_retry_after_restart(tmp_path, mode):
    """RLIMIT_FSIZE models write failure, not actual ENOSPC or power loss."""
    directory = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, '-c', PROBE, str(tmp_path / 'events.sqlite3'), mode],
        cwd=directory, env={'PYTHONPATH': str(directory)},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'passed', 'fault': 'OS_EFBIG', 'mode': mode,
        'external_received': 0, 'central_unique': 1,
        'pending_preserved_after_restart': True, 'recovered_acknowledged': 1}
