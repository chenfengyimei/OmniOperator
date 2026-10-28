"""默认套件离线。真实设备/账号验收使用 scripts/manual_*，不由 pytest 偷跑。"""
import os
from pathlib import Path
import tempfile

import pytest

from offline_guard import deny, install

_directory = tempfile.TemporaryDirectory(prefix='omni-offline-')
_violations = Path(_directory.name) / 'violations.txt'
os.environ['OMNI_TEST_OFFLINE'] = '1'
os.environ['OMNI_TEST_VIOLATIONS'] = str(_violations)
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
_tests = Path(__file__).resolve().parent
os.environ['PYTHONPATH'] = os.pathsep.join([str(_tests / 'offline'), str(_tests), os.environ.get('PYTHONPATH', '')])
install()


@pytest.fixture(autouse=True)
def offline_model_gate(monkeypatch):
    from omni.llm import gateway, embedding
    transports = {'local': embedding._local_request, 'api': embedding._api_request}
    before = _violations.stat().st_size if _violations.exists() else 0
    monkeypatch.setattr(gateway, '_completion', lambda **kwargs: deny('model.completion'))
    monkeypatch.setattr(embedding, '_local_request', lambda *args, **kwargs: deny('embedding.local'))
    monkeypatch.setattr(embedding, '_api_request', lambda *args, **kwargs: deny('embedding.api'))
    yield transports
    after = _violations.stat().st_size if _violations.exists() else 0
    assert after == before, '本用例或其子进程触发了出网/模型闸门；即使业务层捕获异常也不能算通过'


def pytest_sessionfinish(session, exitstatus):
    if _violations.exists() and _violations.stat().st_size:
        session.exitstatus = 1
