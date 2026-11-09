"""A syntactically successful unknown response still requires actual process reclamation."""

import asyncio
import sys
import uuid

import pytest

from omni.adapters.workers.client import WorkerClient
from omni.domain.contracts import Effect, InvocationContext, Outcome


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_unknown_response_reclaims_owned_worker_before_review(tmp_path, monkeypatch, cleanup_fails):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json,sys,time\n"
        "q=json.loads(sys.stdin.buffer.readline())\n"
        "r={'status':'unknown','effect':'unknown','text':'uncertain','error_code':'uncertain','retryable':False,'artifacts':[],'verification':'','data':{}}\n"
        "print(json.dumps({'version':q['version'],'id':q['id'],'result':r}),flush=True)\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )

    async def scenario():
        client = WorkerClient("fixture", tool_writes={"fixture": True}, argv=[sys.executable, str(script)])
        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1, str(tmp_path))
        actual = client._reclaim_actual

        async def failed(_timeout):
            return False

        if cleanup_fails:
            monkeypatch.setattr(client, "_reclaim_actual", failed)
        try:
            result = await client.invoke("fixture", {}, operation_id=str(uuid.uuid4()), context=context)
            assert result.status == Outcome.UNKNOWN and result.effect == Effect.UNKNOWN and not result.retryable
            assert client.cleanup_pending is cleanup_fails
            assert client.review_quiescent() is not cleanup_fails
            if not cleanup_fails:
                assert client._owned is None
            else:
                assert client._owned is not None and not client._owned.reclaimed
        finally:
            monkeypatch.setattr(client, "_reclaim_actual", actual)
            assert await client.close()

    asyncio.run(scenario())
