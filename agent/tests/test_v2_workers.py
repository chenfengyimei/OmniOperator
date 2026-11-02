"""Real isolated worker IPC, file CAS and owned process cleanup; no external services."""

import asyncio
import hashlib
import sys
import uuid

import psutil

from omni.adapters.platform.processes import OwnedProcess
from omni.adapters.workers.client import WorkerClient
from omni.domain.contracts import InvocationContext, Outcome


def test_real_worker_file_scope_and_atomic_overwrite(tmp_path):
    async def scenario():
        client = WorkerClient("builtin")
        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1, str(tmp_path))

        async def call(name, args):
            return await client.invoke(name, args, operation_id=str(uuid.uuid4()), context=context)

        try:
            assert (await call("system_info", {})).status == Outcome.OK
            result = await call("files_write", {"path": "hello.txt", "content": "hello"})
            assert result.status == Outcome.OK, result
            assert (tmp_path / "hello.txt").read_text() == "hello"
            assert (await call("files_write", {"path": "hello.txt", "content": "bad"})).status == Outcome.ERROR
            digest = hashlib.sha256(b"hello").hexdigest()
            assert (
                await call("files_write", {"path": "hello.txt", "content": "changed", "expected_sha256": digest})
            ).status == Outcome.OK
            assert (await call("files_read", {"path": "../escape.txt"})).status == Outcome.ERROR
        finally:
            assert await client.close()

    asyncio.run(scenario())


def test_owned_process_reclaims_observed_descendant(tmp_path):
    helper = tmp_path / "child.py"
    helper.write_text(
        "import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])\nprint(p.pid,flush=True)\ntime.sleep(120)\n",
        encoding="utf-8",
    )

    async def scenario():
        owned = await OwnedProcess.spawn([sys.executable, str(helper)], stdout=asyncio.subprocess.PIPE)
        try:
            async with asyncio.timeout(10):
                child_pid = int(await owned.process.stdout.readline())
            identity = psutil.Process(child_pid).create_time()
            assert await owned.terminate()
            if psutil.pid_exists(child_pid):
                current = psutil.Process(child_pid)
                assert current.create_time() != identity or current.status() == psutil.STATUS_ZOMBIE
        finally:
            await owned.terminate()

    asyncio.run(scenario())
