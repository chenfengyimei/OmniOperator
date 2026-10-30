import json
import os
from pathlib import Path
import subprocess
import sys

from omni.system.observability import BoundedLog, EventLog, redact
from omni.memory.state import StateStore
from omni.memory.runs import RunStore


def test_event_log_excludes_private_payload_and_rolls_with_bound(tmp_path):
    log = BoundedLog(tmp_path/'events.jsonl', max_bytes=1024, backups=2)
    events = EventLog(log)
    for index in range(35):
        events.emit({'run_id': 'abc', 'session_id': 7, 'event_id': f'abc:{index}', 'kind': 'tool_result',
                     'tool': 'test', 'status': 'unknown', 'password': 'secret-value', 'content': '私人聊天'})
    files = list(tmp_path.glob('events.jsonl*'))
    contents = [p for p in files if not p.name.endswith('.lock')]
    assert len(contents) == 3 and all(p.stat().st_size <= 1024 for p in contents)
    text = ''.join(p.read_text(encoding='utf-8') for p in contents)
    assert 'secret-value' not in text and '私人聊天' not in text
    rows = [json.loads(line) for p in contents for line in p.read_text(encoding='utf-8').splitlines()]
    assert all(row['run_id'] == 'abc' and row['session_id'] == 7 and row['event_id'] for row in rows)


def test_failed_log_sink_does_not_lose_authoritative_event(tmp_path):
    state = StateStore(tmp_path/'state.db')
    def unavailable(event): raise OSError('disk full')
    runs = RunStore(state, event_sink=unavailable)
    try:
        runs.create(run_id='r', session_id=1, channel='test', principal='private-user', owner='test')
        runs.append_event('r', 'tool_result', result={'status': 'unknown'}, text='private-text')
        assert runs.events('r')[0].seq == 1
        report = runs.metrics()
        assert report['tool_outcomes'] == {'unknown': 1} and report['log_sink_failures'] == 1
        assert 'private' not in json.dumps(report)
    finally:
        state.close()


def test_redaction_handles_tokens_auth_urls_and_split_console_writes(tmp_path):
    from omni.system.observability import _ServiceStream
    log = BoundedLog(tmp_path/'console.log')
    stream = _ServiceStream(log)
    stream.write('api_key='); stream.write('secret-value\n')
    stream.write('Authorization: Bearer top-secret\nhttps://user:pass@host/a?token=private\n')
    stream.flush()
    result = log.path.read_text(encoding='utf-8')
    assert all(secret not in result for secret in ('secret-value', 'top-secret', 'user:pass', 'token=private'))


def test_service_log_without_inherited_file_handle_rotates(tmp_path):
    log = tmp_path/'service.log'
    code = """
import sys
from omni.system.observability import configure_service_logging
configure_service_logging()
sys.stdout.log.max_bytes=1024
for i in range(100): print('api_key=secret-value ' + 'x'*80)
sys.stdout.flush()
"""
    child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=dict(os.environ, OMNI_SERVICE_LOG=str(log)))
    assert child.wait(timeout=15) == 0
    assert (tmp_path/'service.log.1').exists()
    assert log.stat().st_size <= 1024
    assert 'secret-value' not in log.read_text(encoding='utf-8')
