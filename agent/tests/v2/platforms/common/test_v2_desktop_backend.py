"""Desktop helper host protocols and ownership use only injected processes; no real desktop or native API."""

from omni_test_support.v2.desktop_backend import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.desktop_backend import (
    Input as Input,
)
from omni_test_support.v2.desktop_backend import (
    Owner as Owner,
)
from omni_test_support.v2.desktop_backend import (
    Process as Process,
)
from omni_test_support.v2.desktop_backend import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.desktop_backend import (
    asyncio as asyncio,
)
from omni_test_support.v2.desktop_backend import (
    capabilities as capabilities,
)
from omni_test_support.v2.desktop_backend import (
    d as d,
)
from omni_test_support.v2.desktop_backend import (
    encoded as encoded,
)
from omni_test_support.v2.desktop_backend import (
    factory as factory,
)
from omni_test_support.v2.desktop_backend import (
    json as json,
)
from omni_test_support.v2.desktop_backend import (
    parse_request as parse_request,
)
from omni_test_support.v2.desktop_backend import (
    pytest as pytest,
)
from omni_test_support.v2.desktop_backend import (
    response as response,
)
from omni_test_support.v2.desktop_backend import (
    sys as sys,
)
from omni_test_support.v2.desktop_backend import (
    target as target,
)
from omni_test_support.v2.desktop_backend import (
    uuid as uuid,
)


@pytest.fixture(autouse=True)
def use_python_worker_transport(monkeypatch):
    # These injected replies exercise the Python worker path even on a Mac
    # without a helper installed. The fixed Darwin helper has its own tests.
    monkeypatch.setattr(d, "sys", SimpleNamespace(platform="win32", executable=sys.executable))


def test_constructor_is_pure_and_invalid_requests_never_spawn(monkeypatch):
    calls, before = [], set(sys.modules)

    async def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Native helper must not start")

    monkeypatch.setattr(
        d, "helper_environment", lambda: pytest.fail("Construction must not inspect desktop environment")
    )
    backend = d.DesktopBackend(process_factory=forbidden)
    assert backend.health()["pending"] == backend.health()["resident"] == 0
    assert not (
        {"omni.adapters.platform.desktop.desktop_native", "omni.adapters.workers.desktop"} & (set(sys.modules) - before)
    )

    async def scenario():
        try:
            with pytest.raises(BusinessError):
                await backend.targets(51)
            with pytest.raises(BusinessError):
                await backend.verify({**target(), "window_title": "must not accept UI text"})
            assert calls == []
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["probe", "targets", "verify"])
def test_one_shot_success_uses_exact_request_eof_and_reclaims_before_return(operation):
    async def scenario():
        owner, calls = Owner(), []
        backend = d.DesktopBackend(process_factory=factory(owner, calls=calls))
        try:
            result = await (
                backend.probe()
                if operation == "probe"
                else backend.targets(3)
                if operation == "targets"
                else backend.verify(target())
            )
            assert owner.reclaimed and not backend.health()["resident"]
            request = owner.process.request
            assert request["operation"] == operation and request["request_id"] == str(uuid.UUID(request["request_id"]))
            assert len(owner.process.stdin.frames) == 1 and owner.process.stdin.closed
            assert len(owner.process.stdin.frames[0]) <= d.MAX_FRAME and owner.process.stdin.frames[0].endswith(b"\n")
            assert calls[0][0][-3:] == ["-u", "-m", "omni.adapters.workers.desktop"]
            assert (
                result["acceptance"] == "pending"
                if operation == "probe"
                else result["truncated"] is False
                if operation == "targets"
                else result["matches"] is True
            )
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["request_id", "operation", "target", "extra_field"])
def test_response_identity_operation_and_verified_target_are_bound(mismatch):
    def malformed(request):
        result = {"matches": True, "target": request["target"], "reason": None}
        answer = response(request, result)
        if mismatch == "target":
            answer["result"]["target"] = {**request["target"], "pid": request["target"]["pid"] + 1}
        elif mismatch == "extra_field":
            answer["private_title"] = "native-private-canary"
        else:
            answer[mismatch] = str(uuid.uuid4()) if mismatch == "request_id" else "probe"
        return json.dumps(answer).encode() + b"\n"

    async def scenario():
        owner = Owner(malformed)
        backend = d.DesktopBackend(process_factory=factory(owner))
        try:
            with pytest.raises(BusinessError) as rejected:
                await backend.verify(target())
            assert rejected.value.code == "desktop_protocol_invalid" and rejected.value.__cause__ is None
            assert "native-private-canary" not in str(rejected.value)
            assert owner.reclaimed
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "attack", ["multiframe", "no_lf", "duplicate", "nan", "utf8", "stdout_limit", "stderr_limit", "combined_limit"]
)
def test_framing_and_combined_stream_budgets_are_enforced_without_logging_native_text(attack, caplog):
    def malformed(request):
        frame = encoded(request)
        if attack == "multiframe":
            return frame + frame
        if attack == "no_lf":
            return frame[:-1]
        if attack == "duplicate":
            return frame.replace(b'"request_id":', b'"request_id":"duplicate", "request_id":')
        if attack == "nan":
            return frame.replace(b'"error_code":null', b'"error_code":NaN')
        if attack == "utf8":
            return b"\xff-native-private-canary\n"
        if attack == "stdout_limit":
            return b"x" * (d.MAX_FRAME + 1)
        return frame

    async def scenario():
        stderr = (
            b"native-private-canary" * 4000
            if attack == "stderr_limit"
            else b"x" * (d.MAX_FRAME - 10)
            if attack == "combined_limit"
            else b""
        )
        owner = Owner(malformed, stderr=stderr)
        backend = d.DesktopBackend(process_factory=factory(owner))
        try:
            with pytest.raises(BusinessError) as rejected:
                await backend.probe()
            assert rejected.value.code == (
                "desktop_output_limit" if attack.endswith("limit") else "desktop_protocol_invalid"
            )
            assert "native-private-canary" not in str(rejected.value) + caplog.text
            assert rejected.value.__cause__ is None and owner.reclaimed
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


def test_peer_error_text_is_not_reexported_and_native_faults_have_static_errors(caplog):
    def native_error(request):
        return json.dumps(response(request, ok=False, error="private_native_error_canary")).encode() + b"\n"

    async def scenario():
        owner = Owner(native_error)
        backend = d.DesktopBackend(process_factory=factory(owner))
        try:
            with pytest.raises(BusinessError) as rejected:
                await backend.probe()
            assert rejected.value.code == "desktop_helper_error"
            assert "private_native_error_canary" not in str(rejected.value) + caplog.text
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


def test_production_isolated_argv_and_minimal_local_desktop_environment(monkeypatch):
    async def scenario():
        monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
        monkeypatch.setenv("PYTHONPATH", "untrusted-startup")
        monkeypatch.setenv("OMNI_HOME", "private-home-canary")
        monkeypatch.setenv("MODEL_API_KEY", "private-key-canary")
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "tcp:host=remote.invalid,port=1234")
        monkeypatch.setenv("DISPLAY", "remote.invalid:0")
        monkeypatch.setenv("WAYLAND_DISPLAY", "/tmp/foreign-socket")
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp/foreign-bus")
        monkeypatch.setattr(d.sys, "platform", "linux")
        monkeypatch.setattr(d.os, "geteuid", lambda: 1234, raising=False)
        owner, calls = Owner(), []
        backend = d.DesktopBackend(process_factory=factory(owner, calls=calls))
        try:
            await backend.probe()
            argv, configuration = calls[0]
            assert argv[1:] == ["-I", "-u", "-m", "omni.adapters.workers.desktop"]
            env = configuration["env"]
            assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"
            assert (
                not {
                    "PYTHONPATH",
                    "OMNI_HOME",
                    "MODEL_API_KEY",
                    "DBUS_SESSION_BUS_ADDRESS",
                    "DISPLAY",
                    "WAYLAND_DISPLAY",
                }
                & env.keys()
            )
        finally:
            assert await backend.close(1)

    asyncio.run(scenario())


def test_four_actual_calls_two_resident_spawns_survive_cancelled_waiters():
    async def scenario():
        release, owners = asyncio.Event(), []

        async def spawn(_argv, **_kwargs):
            owner = Owner()
            owners.append(owner)
            await release.wait()
            return owner

        backend = d.DesktopBackend(process_factory=spawn)
        callers = [asyncio.create_task(backend.probe()) for _ in range(4)]
        try:
            async with asyncio.timeout(5):
                while backend.health()["pending"] != 4 or len(owners) != 2:
                    await asyncio.sleep(0)
            with pytest.raises(BusinessError) as busy:
                await backend.probe()
            assert busy.value.code == "desktop_busy" and backend.health()["resident"] == 2
            for caller in callers:
                caller.cancel()
            await asyncio.gather(*callers, return_exceptions=True)
            assert backend.health()["pending"] == 4 and not await backend.close(0)
        finally:
            release.set()
            assert await backend.close(2)
        assert len(owners) == 2 and all(owner.reclaimed for owner in owners)
        assert backend.health()["resident"] == backend.health()["pending"] == 0

    asyncio.run(scenario())


def test_cancel_during_ipc_keeps_actual_helper_and_cleanup_until_confirmed():
    async def scenario():
        release = asyncio.Event()
        owner = Owner(hold=True, terminate_gate=release)
        backend = d.DesktopBackend(process_factory=factory(owner))
        caller = asyncio.create_task(backend.probe())
        try:
            await owner.process.received.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert backend.health()["pending"] == 1 and backend.health()["resident"] == 1
            assert not await backend.close(0)
            assert not owner.reclaimed and backend.health()["cleanup_pending"] == 1
        finally:
            release.set()
            assert await backend.close(2)
        assert owner.reclaimed and not backend.health()["resident"]

    asyncio.run(scenario())


def test_false_reclamation_keeps_poisoned_owner_and_retries_close():
    async def scenario():
        owner = Owner(outcomes=(False, False, True))
        backend = d.DesktopBackend(process_factory=factory(owner))
        with pytest.raises(BusinessError) as pending:
            await backend.probe()
        assert pending.value.code == "desktop_cleanup_pending"
        assert backend.health()["resident"] == backend.health()["cleanup_pending"] == 1
        assert not await backend.close(1) and not owner.reclaimed
        assert await backend.close(1) and owner.reclaimed
        assert owner.calls == 3 and not backend.health()["resident"]

    asyncio.run(scenario())


def test_five_second_deadline_does_not_discard_unreclaimed_helper():
    async def scenario():
        owner = Owner(hold=True, outcomes=(False, True))
        backend = d.DesktopBackend(process_factory=factory(owner))
        try:
            async with asyncio.timeout(8):
                with pytest.raises(BusinessError) as timed_out:
                    await backend.probe()
            assert timed_out.value.code == "desktop_timeout"
            assert backend.health()["resident"] == 1
            async with asyncio.timeout(2):
                while not owner.calls or any(
                    host.cleanup is not None and not host.cleanup.done() for host in backend._hosts
                ):
                    await asyncio.sleep(0)
        finally:
            assert await backend.close(2)
        assert owner.reclaimed and not backend.health()["resident"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["probe", "targets", "verify"])
def test_dispatch_gate_rechecks_revocation_after_actual_spawn_without_sending(operation):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        owner, authorized, checks = Owner(), True, []
        backend = d.DesktopBackend(process_factory=factory(owner, started=started, gate=release))

        async def gate():
            checks.append(authorized)
            if not authorized:
                raise BusinessError("desktop_view_changed", "private owner detail canary", status=409)

        args = () if operation == "probe" else (1,) if operation == "targets" else (target(),)
        caller = asyncio.create_task(getattr(backend, operation)(*args, before_dispatch=gate))
        try:
            await started.wait()
            authorized = False
            release.set()
            with pytest.raises(BusinessError) as denied:
                await caller
            assert denied.value.code == "desktop_view_changed" and denied.value.status == 409
            assert "canary" not in str(denied.value) and denied.value.__cause__ is None
            assert checks == [True, False]
            assert owner.process.stdin.frames == [] and owner.reclaimed
        finally:
            release.set()
            assert await backend.close(2)

    asyncio.run(scenario())


def test_dispatch_gate_rejects_queued_revoked_view_without_starting_another_helper():
    async def scenario():
        owners, authorized = [], True

        async def spawn(_argv, **_kwargs):
            owner = Owner(hold=True)
            owners.append(owner)
            return owner

        async def gate():
            if not authorized:
                raise BusinessError("desktop_view_changed", "View changed", status=409)

        backend = d.DesktopBackend(process_factory=spawn)
        first = [asyncio.create_task(backend.probe()) for _ in range(2)]
        queued = None
        try:
            async with asyncio.timeout(5):
                while len(owners) != 2 or not all(owner.process.received.is_set() for owner in owners):
                    await asyncio.sleep(0)
            queued = asyncio.create_task(backend.targets(1, before_dispatch=gate))
            async with asyncio.timeout(5):
                while backend.health()["pending"] != 3:
                    await asyncio.sleep(0)
            authorized = False
            for owner in owners:
                owner.process.complete()
            await asyncio.gather(*first)
            with pytest.raises(BusinessError) as denied:
                await queued
            assert denied.value.code == "desktop_view_changed"
            assert len(owners) == 2 and all(owner.reclaimed for owner in owners)
        finally:
            assert await backend.close(2)
            await asyncio.gather(*first, *((queued,) if queued else ()), return_exceptions=True)

    asyncio.run(scenario())


def test_stop_accepting_during_actual_spawn_prevents_native_dispatch_and_keeps_cleanup():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        owner = Owner()
        backend = d.DesktopBackend(process_factory=factory(owner, started=started, gate=release))
        caller = asyncio.create_task(backend.probe())
        try:
            await started.wait()
            assert not await backend.close(0)
            assert backend.health()["resident"] == backend.health()["cleanup_pending"] == 1
            release.set()
            with pytest.raises(BusinessError) as denied:
                await caller
            assert denied.value.code == "desktop_closing"
            assert owner.process.stdin.frames == []
        finally:
            release.set()
            assert await backend.close(2)
        assert owner.reclaimed and not backend.health()["pending"]

    asyncio.run(scenario())


def test_observe_uses_fixed_mta_worker_and_reclaims_after_revocation(monkeypatch):
    # The process is injected; this test never reads a real window.
    async def scenario():
        owner, calls = Owner(), []
        backend = d.DesktopBackend(process_factory=factory(owner, calls=calls))
        checks = 0

        async def gate():
            nonlocal checks
            checks += 1
            if checks == 2:
                raise BusinessError("desktop_view_changed", "revoked", status=409)

        try:
            if sys.platform != "win32":
                monkeypatch.setattr(d, "sys", SimpleNamespace(platform="freebsd"))
                with pytest.raises(BusinessError) as error:
                    await backend.observe(target())
                assert error.value.code == "desktop_observation_unavailable" and not calls
                return
            with pytest.raises(BusinessError) as error:
                await backend.observe(target(), before_dispatch=gate)
            assert error.value.code == "desktop_view_changed"
            argv = calls[0][0]
            assert argv[0].lower().endswith("windowspowershell\\v1.0\\powershell.exe")
            assert "-Mta" in argv and "-NoProfile" in argv and "-Command" not in argv
            assert argv[-1].endswith("windows_uia.ps1")
            assert checks == 2 and not owner.process.stdin.frames and owner.reclaimed
        finally:
            assert await backend.close()

    asyncio.run(scenario())
