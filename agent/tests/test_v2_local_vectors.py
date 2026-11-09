"""Local bundle selection, scope-independent transport and real supervised processes without native ML extras."""

import asyncio
import hashlib
import json
import sys

import pytest

from omni.adapters.local_vectors import LocalVectors, configured_vectors
from omni.adapters.vector_files import configure, inspect_bundle, read_manifest, selection
from omni.domain.contracts import BusinessError
from omni.domain.local_vectors import (
    MAX_FRAME,
    canonical,
    decode,
    fingerprint,
    manifest,
    texts_request,
    version,
)


def bundle(root):
    root.mkdir()
    files = {"model.onnx": b"fixture graph", "tokenizer.json": b"{}"}
    for name, data in files.items():
        (root / name).write_bytes(data)
    spec = {
        "schema": 1,
        "model_id": "isolated-fixture",
        "dimension": 2,
        "max_tokens": 64,
        "pooling": "mean_masked",
        "output": "last_hidden_state",
        "pad_id": 0,
        "pad_token": "[PAD]",
        "query_prefix": "",
        "document_prefix": "",
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()
        },
    }
    (root / "manifest.json").write_bytes(canonical(spec))
    return spec


def test_manifest_identity_covers_pooling_prefix_tokenization_and_runtime(tmp_path):
    spec = bundle(tmp_path / "bundle")
    assert manifest(spec) == spec
    assert version(spec) != version({**spec, "query_prefix": "query: "})
    assert version(spec) != version({**spec, "max_tokens": 128})
    assert version(spec) != version(spec, {"onnxruntime": "other"})
    for invalid in ({**spec, "dimension": True}, {**spec, "pooling": "unknown"}, {**spec, "extra": 1}):
        with pytest.raises(BusinessError):
            manifest(invalid)
    with pytest.raises(BusinessError):
        texts_request(["text"] * 9, "document")
    with pytest.raises(BusinessError):
        texts_request(["text"], "remote")


def test_selection_is_explicit_repeatable_and_does_not_load_or_import_native_code(tmp_path):
    root, home = tmp_path / "bundle", tmp_path / "home"
    spec = bundle(root)
    assert selection(home) is None
    inspected = inspect_bundle(root)
    assert inspected["sha256"] == fingerprint(spec) and not inspected["native_model_verified"]
    for _ in range(2):
        result = configure(home, root, fingerprint(spec))
        assert result["applies"] == "next_start" and not result["native_model_verified"]
    assert selection(home) == (root, fingerprint(spec))
    with pytest.raises(BusinessError):
        read_manifest(root, "0" * 64)
    (root / "model.onnx").write_bytes(b"changed")
    with pytest.raises(BusinessError):
        inspect_bundle(root)
    assert json.loads((home / "config/local-vector.json").read_bytes())["sha256"] == fingerprint(spec)


@pytest.mark.parametrize(
    "payload",
    [b"{}", b'{"protocol":"bad"}\n', b"x" * (MAX_FRAME + 1), b'{"protocol":"x","protocol":"y"}\n'],
    ids=["incomplete", "wrong-version", "oversized", "duplicates"],
)
def test_protocol_rejects_unbounded_or_ambiguous_frames(payload):
    with pytest.raises(BusinessError):
        decode(payload)


def fake_argv(mode):
    source = """
import json,sys,time
for line in sys.stdin:
 r=json.loads(line); a=r['arguments']
 if r['method']=='load':
  out={'version':a['version'],'dimension':2,'state':'loaded'}
 else:
  if MODE=='block': time.sleep(60)
  if MODE=='crash': sys.exit(2)
  if MODE=='stderr': print('private-vector-stderr-canary',file=sys.stderr,flush=True)
  out={'version':a['version'],'vectors':[[1.,0.] for _ in a['texts']]}
 if MODE=='identity' and r['method']=='embed': r['id']='00000000-0000-0000-0000-000000000000'
 if MODE=='nan' and r['method']=='embed': out['vectors']=[[float('nan'),0.]]
 print(json.dumps({'protocol':r['protocol'],'id':r['id'],'result':out,'error':''}),flush=True)
""".replace("MODE", repr(mode))
    return [sys.executable, "-u", "-c", source]


@pytest.mark.parametrize("mode", ["ok", "identity", "nan", "crash", "stderr"])
def test_real_worker_ipc_is_scoped_and_reclaims_bad_results(tmp_path, caplog, mode):
    async def scenario():
        spec = bundle(tmp_path / "bundle")
        client = LocalVectors(tmp_path / "bundle", fingerprint(spec), spec, argv=fake_argv(mode))
        try:
            if mode in {"identity", "nan", "crash"}:
                with pytest.raises((BusinessError, OSError)):
                    await client.embed(["private-query-canary"], purpose="query")
                assert not client.status()["available"] and client._owned is None
            else:
                assert await client.embed(["private-query-canary"], purpose="query") == [[1.0, 0.0]]
                assert client.status()["state"] == "loaded"
        finally:
            await client.close()
        assert (
            client._owned is None
            and "private-query-canary" not in caplog.text
            and "private-vector-stderr-canary" not in caplog.text
        )

    asyncio.run(scenario())


def test_cancel_retains_actual_spawn_and_cleanup_failure_until_close_succeeds(tmp_path, monkeypatch):
    from omni.adapters.platform.processes import OwnedProcess

    async def scenario():
        spec = bundle(tmp_path / "bundle")
        client = LocalVectors(tmp_path / "bundle", fingerprint(spec), spec, argv=fake_argv("block"))
        original, failed = OwnedProcess.terminate, True

        async def terminate(self, timeout_s=5):
            return False if failed else await original(self, timeout_s)

        monkeypatch.setattr(OwnedProcess, "terminate", terminate)
        task = asyncio.create_task(client.embed(["query"]))
        try:
            async with asyncio.timeout(10):
                while not client._loaded:
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client._owned is not None and client.status()["reason"] == "vector_cleanup_pending"
            assert client.status()["cleanup_pending"]
            with pytest.raises(BusinessError):
                await client.close()
            assert client._owned is not None
        finally:
            failed = False
            await client.close()
            assert client._owned is None and not client.status()["cleanup_pending"]

    asyncio.run(scenario())


def test_invalid_optional_installation_does_not_prevent_core_startup(tmp_path):
    from omni.composition.instance import open_instance

    async def scenario():
        home = tmp_path / "home"
        (home / "config").mkdir(parents=True)
        (home / "config/local-vector.json").write_text('{"unexpected": true}', encoding="utf-8")
        instance = await open_instance(home, capabilities=())
        try:
            status = instance.documents.vector_status()
            assert not status["available"] and status["reason"] == "vector_bundle_invalid"
            assert instance.health()["ready"]
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await instance.documents.save(agent, "source", "ordinary searchable text")
            result = await instance.documents.search(agent, "searchable")
            assert result["mode"] == "full_text" and result["degraded_reason"] == "vector_bundle_invalid"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_missing_dependencies_report_actionable_state_without_loading_model(tmp_path, monkeypatch):
    import importlib.metadata

    async def scenario():
        root, home = tmp_path / "bundle", tmp_path / "home"
        spec = bundle(root)
        configure(home, root, fingerprint(spec))
        monkeypatch.setattr(
            importlib.metadata,
            "version",
            lambda name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError(name)),
        )
        client = await configured_vectors(home)
        assert client.status()["reason"] == "vector_runtime_not_installed" and client._owned is None
        await client.close()

    asyncio.run(scenario())
