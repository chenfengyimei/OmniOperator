"""Observed external effects survive IPC truncation and malformed worker results never imply success."""

import asyncio
import json
import sys
import uuid

import pytest

from omni.adapters.workers.client import WorkerClient
from omni.adapters.workers.main import Worker
from omni.adapters.workers.protocol import VERSION
from omni.domain.contracts import Effect, InvocationContext, Outcome


def test_large_escaped_shell_output_preserves_applied_effect_without_replay(tmp_path):
    async def scenario():
        client = WorkerClient("builtin", tool_writes={"shell_execute": True})
        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1, str(tmp_path))
        try:
            result = await client.invoke(
                "shell_execute",
                {
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; import sys; p=Path('effect.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); sys.stdout.write(chr(1)*262144)",
                    ]
                },
                operation_id=str(uuid.uuid4()),
                context=context,
            )
            assert (tmp_path / "effect.txt").read_text() == "x"
            assert result.effect == Effect.APPLIED and result.status == Outcome.OK
            assert result.data["truncated"] is True and not result.retryable
        finally:
            assert await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("writes", [False, True])
@pytest.mark.parametrize(
    "kind",
    [
        "missing_effect",
        "text_type",
        "artifacts_type",
        "data_type",
        "retryable_effect",
        "nan",
        "infinity",
        "duplicate",
        "wrong_id",
        "wrong_version",
        "half_frame",
        "unknown_without_effect",
    ],
)
def test_malformed_worker_response_preserves_dispatch_uncertainty(tmp_path, kind, writes):
    helper = tmp_path / "fake_worker.py"
    helper.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "q=json.loads(sys.stdin.buffer.readline()); Path(sys.argv[2]).write_text('x')\n"
        "r={'status':'ok','text':'observed','effect':'none','error_code':'','retryable':False,'artifacts':[],'verification':'','data':{}}\n"
        "mode=sys.argv[1]\n"
        "if mode=='missing_effect': del r['effect']\n"
        "if mode=='text_type': r['text']=True\n"
        "if mode=='artifacts_type': r['artifacts']='unexpected'\n"
        "if mode=='data_type': r['data']=[]\n"
        "if mode=='retryable_effect': r.update(effect='applied',retryable=True)\n"
        "if mode=='nan': r['data']={'value':float('nan')}\n"
        "if mode=='infinity': r['data']={'value':float('inf')}\n"
        "if mode=='unknown_without_effect': r['status']='unknown'\n"
        "body={'version':q['version'],'id':q['id'],'result':r}\n"
        "if mode=='wrong_id': body['id']='00000000-0000-0000-0000-000000000000'\n"
        "if mode=='wrong_version': body['version']=True\n"
        "raw=json.dumps(body)\n"
        'if mode==\'duplicate\': raw=raw.replace(\'"effect": "none"\',\'"effect": "none", "effect": "applied"\')\n'
        "sys.stdout.write(raw+('' if mode=='half_frame' else '\\n')); sys.stdout.flush()\n",
        encoding="utf-8",
    )

    async def scenario():
        count = tmp_path / "count.txt"
        client = WorkerClient(
            "fixture", tool_writes={"fixture": writes}, argv=[sys.executable, str(helper), kind, str(count)]
        )
        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1, str(tmp_path))
        try:
            result = await client.invoke("fixture", {}, operation_id=str(uuid.uuid4()), context=context)
            assert result.status == (Outcome.UNKNOWN if writes else Outcome.ERROR)
            assert result.effect == (Effect.UNKNOWN if writes else Effect.NONE)
            assert result.error_code == "worker_disconnected" and not result.retryable
            assert count.read_text() == "x"
        finally:
            assert await client.close()

    asyncio.run(scenario())


def test_file_replace_exception_after_effect_is_unknown_and_validation_is_unapplied(tmp_path, monkeypatch):
    import omni.adapters.workers.main as module

    original = module.publish_file

    def replace_then_fail(source, target, **options):
        original(source, target, **options)
        raise OSError("private-native-exception-canary")

    async def scenario():
        worker = Worker()
        context = {"workspace": str(tmp_path)}
        invalid = await worker.invoke("files_write", {"path": "../outside", "content": "blocked"}, context)
        assert invalid.status == Outcome.ERROR and invalid.effect == Effect.NONE
        monkeypatch.setattr(module, "publish_file", replace_then_fail)
        result = await worker.invoke("files_write", {"path": "effect.txt", "content": "applied"}, context)
        assert (tmp_path / "effect.txt").read_text() == "applied"
        assert result.status == Outcome.UNKNOWN and result.effect == Effect.UNKNOWN
        assert "private-native-exception-canary" not in result.text and not result.retryable
        assert not list(tmp_path.glob(".omni-write-*"))

    asyncio.run(scenario())


def test_protocol_preserves_valid_retryable_none_effect_and_rejects_bool_version():
    from omni.adapters.workers.protocol import response

    operation = str(uuid.uuid4())
    data = {
        "version": VERSION,
        "id": operation,
        "result": {
            "status": "error",
            "effect": "none",
            "text": "not dispatched",
            "error_code": "not_started",
            "retryable": True,
            "artifacts": [],
            "verification": "",
            "data": {},
        },
    }
    frame = lambda: (json.dumps(data) + "\n").encode()
    result = response(frame(), operation, writes=True)
    assert result.retryable and result.effect == Effect.NONE
    data["version"] = 2.0
    with pytest.raises(ValueError):
        response(frame(), operation, writes=True)


def test_truncated_result_keeps_registered_artifacts_and_bounded_verification():
    from omni.adapters.workers.protocol import encode_response, response
    from omni.domain.contracts import ActionResult

    identity, artifact = str(uuid.uuid4()), str(uuid.uuid4())
    original = ActionResult(Outcome.OK, "\x01" * 262144, Effect.APPLIED, artifacts=(artifact,), verification="f" * 64)
    result = response(encode_response(identity, original), identity, writes=True)
    assert result.status == Outcome.OK and result.effect == Effect.APPLIED
    assert result.artifacts == (artifact,) and result.verification == "f" * 64
    assert result.data["truncated"] is True


def test_workspace_hardlinks_cannot_expose_or_modify_outside_content(tmp_path):
    import hashlib
    import os

    outside = tmp_path / "private.txt"
    outside.write_text("private-outside-canary", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    os.link(outside, workspace / "alias.txt")

    async def scenario():
        worker = Worker()
        context = {"workspace": str(workspace)}
        read = await worker.invoke("files_read", {"path": "alias.txt"}, context)
        write = await worker.invoke(
            "files_write",
            {
                "path": "alias.txt",
                "content": "changed",
                "expected_sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
            },
            context,
        )
        assert read.status == write.status == Outcome.ERROR
        assert read.effect == write.effect == Effect.NONE
        assert "private-outside-canary" not in read.text
        assert outside.read_text() == "private-outside-canary"
        listing = await worker.invoke("files_list", {"path": "."}, context)
        assert listing.status == Outcome.OK and listing.data["entries"] == []

    asyncio.run(scenario())


def test_read_tool_exposes_the_exact_fingerprint_required_for_overwrite(tmp_path):
    import hashlib

    payload = "source text".encode()
    (tmp_path / "source.txt").write_bytes(payload)

    async def scenario():
        result = await Worker().invoke("files_read", {"path": "source.txt"}, {"workspace": str(tmp_path)})
        digest = hashlib.sha256(payload).hexdigest()
        assert result.verification == result.data["sha256"] == digest
        assert f"sha256={digest}" in result.text and "source text" in result.text

    asyncio.run(scenario())


def test_shell_buffer_truncation_is_visible_before_transport_limit(tmp_path):
    async def scenario():
        worker = Worker()
        result = await worker.invoke(
            "shell_execute",
            {"argv": [sys.executable, "-c", "import sys; sys.stdout.write('x'*300000)"]},
            {"workspace": str(tmp_path)},
        )
        assert result.status == Outcome.OK and result.effect == Effect.APPLIED
        assert result.data["truncated"] and result.data["stdout_truncated"] and not result.data["stderr_truncated"]
        assert result.text.startswith("[stdout truncated") and len(result.text) < 263000

    asyncio.run(scenario())
