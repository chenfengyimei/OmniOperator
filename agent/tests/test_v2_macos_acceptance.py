"""Mac acceptance scripts use injected credential stores here; no real Keychain or input."""

import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/accept_macos_keychain.py"

spec = importlib.util.spec_from_file_location("owned_keychain_acceptance", SOURCE)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
SECRET = "OWNED-SECRET-MUST-NOT-LOG"
TOKEN = "a" * 32


@pytest.mark.parametrize("fault", ["none", "mismatch", "set_after_write", "delete", "delete_verify", "existing"])
def test_keychain_probe_never_exports_secrets_or_replays_unknown_effects(monkeypatch, capsys, fault):
    class Store:
        def __init__(self, namespace):
            assert namespace == "macos-acceptance-" + TOKEN
            self.value = SECRET if fault == "existing" else None
            self.sets = self.deletes = 0

        def get(self, reference):
            assert reference == "probe/" + TOKEN
            if fault == "delete_verify" and self.deletes:
                raise OSError(SECRET)
            if fault == "mismatch" and self.sets and not self.deletes:
                return "different-owned-value"
            return self.value

        def set(self, reference, value):
            self.sets += 1
            self.value = value
            if fault == "set_after_write":
                raise OSError(SECRET)

        def delete(self, reference):
            self.deletes += 1
            if fault == "delete":
                raise OSError(SECRET)
            self.value = None

    store = Store("macos-acceptance-" + TOKEN)
    monkeypatch.setattr(script, "SystemSecrets", lambda _namespace: store)
    monkeypatch.setattr(script.secrets, "token_urlsafe", lambda _length: SECRET)
    monkeypatch.setattr(
        script.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(json.dumps({"token": TOKEN, "mode": "roundtrip"}).encode())),
    )
    assert script.worker() == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert SECRET not in captured.out + captured.err
    assert result["status"] == (
        "passed" if fault == "none" else "unknown" if fault in {"delete", "delete_verify"} else "failed"
    )
    assert store.sets == (0 if fault == "existing" else 1)
    assert store.deletes == (0 if fault == "existing" else 1)
    assert result["cleanup_verified"] == (fault not in {"delete", "delete_verify", "existing"})
    assert result["cleanup_required"] == (fault in {"delete", "delete_verify"})


def test_explicit_keychain_cleanup_only_targets_the_owned_test_namespace(monkeypatch, capsys):
    calls = []

    class Store:
        def get(self, reference):
            calls.append(("get", reference))
            return None

        def delete(self, reference):
            calls.append(("delete", reference))

        def set(self, *args):
            pytest.fail("Cleanup cannot create an item")

    monkeypatch.setattr(script, "SystemSecrets", lambda namespace: calls.append(("namespace", namespace)) or Store())
    monkeypatch.setattr(
        script.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(json.dumps({"token": TOKEN, "mode": "cleanup"}).encode())),
    )
    assert script.worker() == 0
    assert json.loads(capsys.readouterr().out)["cleanup_verified"]
    assert calls == [
        ("namespace", "macos-acceptance-" + TOKEN),
        ("delete", "probe/" + TOKEN),
        ("get", "probe/" + TOKEN),
    ]
    for invalid in ["real-owner-profile", "../credentials", "A" * 32]:
        with pytest.raises(ValueError):
            script.identity(invalid)


@pytest.mark.parametrize(
    "fault",
    ["none", "invalid_receipt", "exit", "stderr_overflow", "cleanup", "leaked_field", "false_success", "leaked_error"],
)
def test_keychain_worker_transport_bounds_private_output_and_actual_cleanup(tmp_path, monkeypatch, fault):
    import asyncio

    async def scenario():
        output = asyncio.StreamReader()
        errors = asyncio.StreamReader()
        receipt = {"status": "passed", "cleanup_verified": True, "cleanup_required": False, "readback_verified": True}
        if fault == "invalid_receipt":
            receipt.pop("cleanup_required")
        if fault == "leaked_field":
            receipt["secret_body"] = SECRET
        if fault == "leaked_error":
            receipt.update(status="failed", error_type=SECRET)
        if fault == "false_success":
            receipt["cleanup_verified"] = False

        class Input:
            frames = []

            def write(self, value):
                self.frames.append(value)

            async def drain(self):
                pass

            def close(self):
                output.feed_data(json.dumps(receipt).encode())
                errors.feed_data(b"x" * (script.MAX_FRAME + 1) if fault == "stderr_overflow" else SECRET.encode())
                output.feed_eof()
                errors.feed_eof()

        async def wait():
            return 2 if fault == "exit" else 0

        class Owner:
            created = 1234.5
            calls = 0
            process = SimpleNamespace(pid=42, stdin=Input(), stdout=output, stderr=errors, wait=wait)

            async def terminate(self, timeout):
                self.calls += 1
                return fault != "cleanup"

        owner = Owner()

        async def spawn(argv, **kwargs):
            assert argv[1] == "-I" and argv[-1] == "--worker"
            assert SECRET not in repr(argv) + repr(kwargs["env"])
            assert kwargs["cwd"] == str(tmp_path)
            return owner

        monkeypatch.setattr(script, "OwnedProcess", SimpleNamespace(spawn=spawn))
        metadata = {}
        if fault in {"none", "leaked_error"}:
            result = await script.invoke(TOKEN, "roundtrip", tmp_path, worker_status=metadata)
            assert result["status"] == ("passed" if fault == "none" else "failed")
            assert result["stderr_bytes"] == len(SECRET)
            assert SECRET not in json.dumps(result)
        else:
            with pytest.raises(RuntimeError):
                await script.invoke(TOKEN, "roundtrip", tmp_path, worker_status=metadata)
        assert owner.calls == 1
        assert metadata == {"pid": 42, "created": 1234.5, "reclaimed": fault != "cleanup"}
        assert json.loads(b"".join(owner.process.stdin.frames)) == {"token": TOKEN, "mode": "roundtrip"}

    asyncio.run(scenario())
