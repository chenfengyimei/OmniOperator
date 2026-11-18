"""Real approvals, SQLite and managed Python with a paired fake IME; no device or model calls."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from omni_test_support.v2.android import SERIAL, FakeAdb, approve
from omni_test_support.v2.android_model import AndroidModel, approve_tool
from omni_test_support.v2.credentials import Backend
from omni_test_support.v2.execution import wait_state
from sqlalchemy import update

from omni.adapters.android import AndroidBackend
from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.android import ACCESS_SPEC, MODEL_SPEC
from omni.domain.android_unicode import COMPONENT, SOCKET, key_value, packet, profile, text_value, unpack
from omni.domain.contracts import (
    ActionResult,
    BusinessError,
    Effect,
    Invocation,
    InvocationContext,
    ModelReply,
    Outcome,
)

KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()
APK, CERT = "a" * 64, "b" * 64
ORIGINAL = "org.fixture.keyboard/.Keyboard"


class UnicodeAdb(FakeAdb):
    def __init__(self):
        super().__init__()
        self.ime, self.subtype = ORIGINAL, -1
        self.enabled = ORIGINAL + ":" + COMPONENT
        self.apk, self.certificate, self.key = APK, CERT, key_value(KEY)
        self.intervene = self.drop_commit = self.restore_failure = False
        self.commits = 0
        self.on_ready = None

    async def extra_shell(self, command):
        if command.startswith("settings --user 0 get secure "):
            setting = command.rsplit(" ", 1)[1]
            return {
                "default_input_method": self.ime,
                "enabled_input_methods": self.enabled,
                "selected_input_method_subtype": str(self.subtype),
            }[setting].encode()
        if command == "pm path --user 0 org.omnioperator.inputhelper":
            return b"package:/data/app/fixture/base.apk\n"
        if command == "sha256sum '/data/app/fixture/base.apk'":
            return (self.apk + "  /data/app/fixture/base.apk\n").encode()
        if command.startswith("ime set --user 0 "):
            self.writes.append(command)
            chosen = command.removeprefix("ime set --user 0 ").strip("'")
            if chosen == COMPONENT or not self.restore_failure:
                self.ime = chosen
            return b"Input method selected\n"
        return await super().extra_shell(command)

    async def helper(self, reader, writer, service):
        assert service == "localabstract:" + SOCKET + "0"
        original = None
        before = None
        token = None
        nonces = set()
        try:
            for _ in range(4):
                size = int.from_bytes(await reader.readexactly(4), "big")
                assert 1 <= size <= 16384
                body, signature = await reader.readexactly(size), await reader.readexactly(32)
                if not hmac.compare_digest(
                    signature, hmac.digest(self.key, b"omni-input-request-1\n" + body, "sha256")
                ):
                    return
                request = json.loads(body)
                assert request["nonce"] not in nonces
                nonces.add(request["nonce"])
                data = request["data"]
                reply = {
                    "version": 1,
                    "nonce": request["nonce"],
                    "certificate_sha256": self.certificate,
                    "user": self.user,
                }
                if request["method"] == "prepare":
                    assert (
                        before is None
                        and data["application"] == self.package
                        and data["resource"] == "org.fixture:id/input"
                    )
                    assert data["before_hash"] == hashlib.sha256(self.text.encode()).hexdigest()
                    original, before = data["original_ime"], self.text
                    token = str(uuid.uuid4())
                    reply.update(status="ready", token=token)
                    if self.intervene:
                        self.text += "manual"
                    if self.on_ready:
                        await self.on_ready()
                elif request["method"] == "commit":
                    if token is None or data["token"] != token or before != self.text:
                        reply.update(status="denied")
                    else:
                        token = None
                        self.text += data["text"]
                        self.commits += 1
                        if self.drop_commit:
                            return
                        reply.update(
                            status="verified",
                            after_hash=hashlib.sha256(self.text.encode()).hexdigest(),
                            utf16_length=len(self.text.encode("utf-16-le")) // 2,
                        )
                else:
                    raise AssertionError(request)
                writer.write(packet(reply, self.key, response_to=request["nonce"]))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if original is not None and self.ime == COMPONENT and not self.restore_failure:
                self.ime = original


@asynccontextmanager
async def prepared_unicode(tmp_path, model=None):
    fake = UnicodeAdb()
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    backend = Backend()
    options = {"capabilities": ()} if model is None else {"model": model}
    instance = await open_instance(tmp_path / "home", credential_backend=backend, **options)
    agent = (await instance.application.query("agents", ""))[0]["id"]
    try:
        port = server.sockets[0].getsockname()[1]

        def setup(u):
            config = u.configuration_create(
                agent,
                "channel",
                "Unicode fixture",
                True,
                {"driver": "android", "endpoint": f"http://127.0.0.1:{port}", "account": SERIAL},
            )
            session = u._record(s.sessions, {"title": "Unicode fixture", "revision": 1, "next_seq": 0}, agent)
            return config, session["id"]

        cfg, session = await instance._database.write(setup)
        probe = await instance.android.probe(agent, cfg["id"], True)
        grant = await instance.android.grant(agent, probe["probe_id"], True)
        await instance.credentials.change("fixture/android-pairing", KEY)
        saved = await instance.android.save_unicode_profile(
            agent,
            grant["id"],
            {
                "enabled": True,
                "credential_ref": "fixture/android-pairing",
                "apk_sha256": APK,
                "certificate_sha256": CERT,
            },
            0,
        )
        yield instance, fake, agent, session, grant, saved
    finally:
        fake.allow_dump.set()
        assert (await instance.close())["complete"]
        server.close()
        await server.wait_closed()
        if fake.clients:
            await asyncio.wait(fake.clients, timeout=2)
        assert not fake.errors and not fake.clients


async def request_unicode(instance, agent, session, grant):
    view = await instance.android.observe(agent, grant["id"])
    assert "credential_ref" not in json.dumps(view) and KEY not in json.dumps(view)
    node = next(n for n in view["nodes"] if "append_unicode" in n["actions"])
    return {
        "consent": True,
        "grant_id": grant["id"],
        "session_id": session,
        "request_id": str(uuid.uuid4()),
        "observation_id": view["observation_id"],
        "node_id": node["node_id"],
        "kind": "append_unicode",
        "text": "中文🙂",
        "params": {"helper_revision": view["unicode_helper"]["revision"], "restore_ime": True},
    }


async def execute(instance, agent, request):
    receipt = await instance.android.submit(agent, request)
    instance.application.notify()
    await approve(instance, agent, receipt)
    run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed", "cancelled"})
    async with asyncio.timeout(15):
        while not instance.android.quiescent():
            await asyncio.sleep(0.01)
    command = await instance._database.read(lambda u: u.owner_command(agent, receipt["run_id"]))
    action = await instance._database.read(lambda u: u.action(agent, command["action_id"]))
    return run, action


def test_unicode_real_worker_uses_one_input_ledger_and_persists_restoration_before_selection(tmp_path):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, _):
            original_extra = fake.extra_shell

            async def checked(command):
                if command == "ime set --user 0 " + COMPONENT:
                    rows = (await instance.android.environments(agent))["items"]
                    assert len(rows) == 1 and rows[0]["state"] == "pending" and rows[0]["result_code"] == "prepared"
                return await original_extra(command)

            fake.extra_shell = checked
            request = await request_unicode(instance, agent, session, grant)
            run, action = await execute(instance, agent, request)
            assert run["state"] == "succeeded", (run, action)
            assert action["result"]["verification"] == "unicode_text_appended"
            assert fake.text == "hello中文🙂" and fake.commits == 1 and fake.ime == ORIGINAL
            rows = (await instance.android.environments(agent))["items"]
            assert len(rows) == 1 and rows[0]["state"] == "restored" and rows[0]["action_id"] == action["id"]
            replay = await instance.android.submit(agent, request)
            assert replay["duplicate"] and fake.commits == 1
            assert not any("enable" in command or "input text" in command for command in fake.writes)
            logs = "".join(p.read_text(encoding="utf-8") for p in instance.paths.logs.glob("*.jsonl"))
            assert KEY not in logs and "中文" not in logs and "hello" not in logs
            assert (
                await instance._database.read(lambda u: u.connection.exec_driver_sql("PRAGMA foreign_key_check").all())
                == []
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "fault", ["apk", "certificate", "pairing_key", "disabled_helper", "intervene", "drop_commit", "restore_failure"]
)
def test_unicode_failure_is_explicit_never_replays_and_retains_unfinished_environment(tmp_path, fault):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, _):
            if fault == "apk":
                fake.apk = "c" * 64
            elif fault == "certificate":
                fake.certificate = "c" * 64
            elif fault == "pairing_key":
                fake.key = b"x" * 32
            elif fault == "disabled_helper":
                fake.enabled = ORIGINAL
            else:
                setattr(fake, fault, True)
            run, action = await execute(instance, agent, await request_unicode(instance, agent, session, grant))
            assert run["state"] == "failed", (run, action)
            assert action["state"] in {"denied", "failed", "unknown"}
            assert fake.commits == (1 if fault in {"drop_commit", "restore_failure"} else 0)
            rows = (await instance.android.environments(agent))["items"]
            if fault in {"apk", "disabled_helper"}:
                assert not rows and not fake.writes
            elif fault == "restore_failure":
                assert rows[0]["state"] == "pending" and action["state"] == "unknown"
                with pytest.raises(BusinessError, match="recovery"):
                    await instance._database.read(lambda u: u.android_dispatch(agent, grant["id"], str(uuid.uuid4())))
            if fault == "drop_commit":
                assert action["state"] == "unknown" and rows[0]["state"] == "restored"

    asyncio.run(scenario())


def test_unicode_profile_is_cas_immutable_and_drift_invalidates_observation(tmp_path):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, saved):
            request = await request_unicode(instance, agent, session, grant)
            config = {k: saved[k] for k in ("enabled", "credential_ref", "apk_sha256", "certificate_sha256")}
            with pytest.raises(BusinessError, match="changed"):
                await instance.android.save_unicode_profile(agent, grant["id"], config, 0)
            await instance.android.save_unicode_profile(agent, grant["id"], config, 1)
            with pytest.raises(BusinessError, match="observe again"):
                await instance.android.submit(agent, request)
            with pytest.raises(BusinessError, match="constraint"):
                await instance._database.write(
                    lambda u: u.connection.execute(update(s.android_unicode_profiles).values(enabled=False))
                )
            assert not fake.writes

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["restore", "manual_original", "manual_other"])
def test_unicode_recovery_survives_revoked_grant_and_does_not_replay_input_or_overwrite_user_settings(tmp_path, mode):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, _):
            fake.restore_failure = True
            _, original_action = await execute(instance, agent, await request_unicode(instance, agent, session, grant))
            environment = (await instance.android.environments(agent))["items"][0]
            assert environment["state"] == "pending"
            await instance.android.revoke(agent, grant["id"], grant["revision"])
            fake.restore_failure = False
            if mode == "manual_original":
                fake.ime = ORIGINAL
            if mode == "manual_other":
                fake.ime = "org.other/.Keyboard"
                fake.enabled += ":" + fake.ime
            writes = len(fake.writes)
            receipt = await instance.android.restore_ime(
                agent,
                environment["id"],
                {
                    "consent": True,
                    "session_id": session,
                    "request_id": str(uuid.uuid4()),
                    "revision": environment["revision"],
                },
            )
            instance.application.notify()
            run = await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval", "failed"})
            assert run["state"] == "waiting_approval", run
            pending = next(a for a in await instance.application.approvals(agent) if a["run_id"] == run["id"])
            assert pending["tool"] == "android_restore_ime" and "text" not in pending["arguments"]
            await instance.application.approve(agent, pending["id"], True, pending["policy_revision"])
            run = await wait_state(instance.application, agent, run["id"], {"succeeded", "failed"})
            assert run["state"] == ("failed" if mode == "manual_other" else "succeeded"), run
            observed = (await instance.android.environments(agent))["items"][0]
            assert observed["state"] == ("pending" if mode == "manual_other" else "restored")
            if mode != "restore":
                assert len(fake.writes) == writes
            assert fake.commits == 1
            assert (await instance._database.read(lambda u: u.action(agent, original_action["id"])))[
                "state"
            ] == "unknown"

    asyncio.run(scenario())


def test_unicode_approval_revocation_between_helper_ready_and_commit_still_owns_cleanup(tmp_path):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, _):

            async def revoke():
                await instance.android.revoke(agent, grant["id"], grant["revision"])

            fake.on_ready = revoke
            run, action = await execute(instance, agent, await request_unicode(instance, agent, session, grant))
            assert run["state"] == "failed" and action["state"] != "succeeded"
            assert fake.commits == 0 and fake.ime == ORIGINAL
            assert (await instance.android.environments(agent))["items"][0]["state"] == "restored"

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", ["", "a\n", "a\x00", "\ud800", "a" * 257])
def test_unicode_text_rejects_controls_and_broken_surrogates(bad):
    with pytest.raises(BusinessError):
        text_value(bad)


def test_unicode_protocol_binds_signature_nonce_and_direction():
    nonce = "a" * 32
    encoded = packet({"version": 1, "nonce": nonce, "status": "ready"}, key_value(KEY), response_to=nonce)
    assert unpack(encoded[4:-32], encoded[-32:], key_value(KEY), nonce)["status"] == "ready"
    for other in ("b" * 32, ""):
        with pytest.raises(BusinessError):
            unpack(encoded[4:-32], encoded[-32:], key_value(KEY), other)
    with pytest.raises(BusinessError):
        unpack(encoded[4:-32] + b"x", encoded[-32:], key_value(KEY), nonce)
    assert text_value("中文🙂") == "中文🙂"
    with pytest.raises(BusinessError):
        profile({"enabled": 1})


def test_model_unicode_observation_and_append_keep_two_independent_approvals(tmp_path):
    class UnicodeModel(AndroidModel):
        async def complete(self, messages, tools, settings):
            reply = await super().complete(messages, tools, settings)
            if self.step == 3:
                arguments = {
                    **reply.calls[0].arguments,
                    "kind": "append_unicode",
                    "text": "中文🙂",
                    "params": {"helper_revision": self.view["unicode_helper"]["revision"], "restore_ime": True},
                }
                return ModelReply("", (Invocation("unicode-input", MODEL_SPEC.name, arguments),), cost_micro=0)
            return reply

    async def scenario():
        model = UnicodeModel()
        async with prepared_unicode(tmp_path, model) as (instance, fake, agent, _, grant, _):
            configured = await instance.configurations.get(
                agent, (await instance.application.settings(agent))["value"]["model_profile_id"]
            )
            await instance.configurations.save(
                agent,
                configured["id"],
                configured["name"],
                True,
                {**configured["value"], "model": "fixture"},
                configured["revision"],
            )
            model.request = {
                "grant_id": grant["id"],
                "target_serial": SERIAL,
                "target_user": 0,
                "packages": ["org.fixture"],
                "kinds": ["append_unicode"],
                "ttl_seconds": 600,
            }
            receipt = await instance.application.submit_turn(
                agent, None, "unicode-model", "Use this approved device reference"
            )
            access = await approve_tool(instance, agent, receipt["run_id"], ACCESS_SPEC.name)
            action = await approve_tool(instance, agent, receipt["run_id"], MODEL_SPEC.name)
            assert access["action_id"] != action["action_id"] and fake.commits == 0
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert fake.text == "hello中文🙂" and fake.commits == 1 and fake.ime == ORIGINAL
            assert "android_restore_ime" not in model.catalogs[0]
            assert (await instance.android.environments(agent))["items"][0]["state"] == "restored"

    asyncio.run(scenario())


def test_cancelled_waiter_retains_unknown_ime_owner_and_only_observes_after_reclamation(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        events = []
        snapshot = {
            "prepare_nonce": str(uuid.uuid4()),
            "original_ime": ORIGINAL,
            "original_subtype": -1,
            "enabled_sha256": "e" * 64,
        }

        class Worker:
            async def invoke(self, method, values, *, operation_id, context, before_dispatch=None):
                if before_dispatch:
                    await before_dispatch()
                events.append(method)
                if method == "android_ready":
                    return ActionResult(Outcome.OK, "ready")
                if method == "android_unicode_prepare":
                    return ActionResult(Outcome.OK, "prepared", data=snapshot)
                if method == "android_unicode_begin":
                    entered.set()
                    await release.wait()
                    return ActionResult(Outcome.UNKNOWN, "lost acknowledgement", Effect.UNKNOWN)
                assert method == "android_unicode_environment", method
                return ActionResult(
                    Outcome.OK,
                    "observed",
                    data={
                        "current_ime": COMPONENT,
                        "current_subtype": -1,
                        "enabled_sha256": "e" * 64,
                        "result_code": "environment_changed",
                    },
                )

            async def close(self, timeout):
                events.append("reclaimed")
                return True

        class Secrets:
            async def get(self, reference):
                return KEY

        backend = AndroidBackend(tmp_path, worker_factory=Worker, secrets=Secrets())

        async def guard():
            pass

        async def environment(phase, value):
            events.append(phase)

        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1)
        args = {
            "kind": "append_unicode",
            "connection": {},
            "device": {},
            "unicode_profile": {"profile": {"credential_ref": "fixture/android-pairing"}},
        }
        task = asyncio.create_task(
            backend.execute("android_ready", args, context, before_dispatch=guard, action=args, environment=environment)
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not backend.quiescent() and events.index("planned") < events.index("android_unicode_begin")
        release.set()
        async with asyncio.timeout(5):
            while not backend.quiescent():
                await asyncio.sleep(0.01)
        assert events.index("reclaimed") < events.index("android_unicode_environment")
        assert (
            events.count("android_unicode_begin") == 1
            and "android_unicode_commit" not in events
            and "android_unicode_restore" not in events
        )
        assert events[-2:] == ["reclaimed", "reclaimed"] and "observed" in events
        assert await backend.close(1)

    asyncio.run(scenario())


def test_unrestored_ime_obligation_and_unknown_input_survive_a_new_boot(tmp_path):
    async def scenario():
        async with prepared_unicode(tmp_path) as (instance, fake, agent, session, grant, _):
            fake.restore_failure = True
            _, action = await execute(instance, agent, await request_unicode(instance, agent, session, grant))
            before = (await instance.android.environments(agent))["items"][0]
            assert (await instance.close())["complete"]
            restarted = await open_instance(instance.paths.home, capabilities=(), credential_backend=Backend())
            try:
                after = (await restarted.android.environments(agent))["items"][0]
                assert before == after and after["state"] == "pending"
                with pytest.raises(BusinessError) as error:
                    await restarted._database.read(lambda u: u.android_dispatch(agent, grant["id"], str(uuid.uuid4())))
                assert error.value.code == "android_ime_cleanup_pending"
                assert (await restarted._database.read(lambda u: u.action(agent, action["id"])))["state"] == "unknown"
                assert fake.commits == 1
            finally:
                assert (await restarted.close())["complete"]

    asyncio.run(scenario())


def test_restoration_receipt_must_be_durable_before_reporting_success(tmp_path):
    async def scenario():
        snapshot = {
            "prepare_nonce": str(uuid.uuid4()),
            "original_ime": ORIGINAL,
            "original_subtype": -1,
            "enabled_sha256": "e" * 64,
        }
        recorded = []

        class Worker:
            async def invoke(self, method, values, **kwargs):
                if kwargs.get("before_dispatch"):
                    await kwargs["before_dispatch"]()
                if method == "android_unicode_prepare":
                    return ActionResult(Outcome.OK, "prepared", data=snapshot)
                if method == "android_unicode_restore":
                    return ActionResult(
                        Outcome.OK,
                        "restored",
                        data={
                            "current_ime": ORIGINAL,
                            "current_subtype": -1,
                            "enabled_sha256": "e" * 64,
                            "result_code": "restored",
                        },
                    )
                return ActionResult(
                    Outcome.OK, "verified", Effect.APPLIED if method == "android_unicode_commit" else Effect.NONE
                )

            async def close(self, timeout):
                return True

        class Secrets:
            async def get(self, reference):
                return KEY

        async def guard():
            pass

        async def environment(phase, value):
            if phase == "observed" and value["result_code"] == "restored":
                raise BusinessError("database_unavailable", "Injected write failure")
            recorded.append((phase, value))

        backend = AndroidBackend(tmp_path, worker_factory=Worker, secrets=Secrets())
        args = {
            "kind": "append_unicode",
            "connection": {},
            "device": {},
            "unicode_profile": {"profile": {"credential_ref": "fixture/android-pairing"}},
        }
        context = InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1)
        result = await backend.execute(
            "android_ready", args, context, before_dispatch=guard, action=args, environment=environment
        )
        assert (
            result.status == Outcome.UNKNOWN
            and result.effect == Effect.UNKNOWN
            and result.data["ime_restored"] is False
        )
        assert recorded[-1] == ("observed", {"result_code": "cleanup_observation_unknown"})
        assert await backend.close(1)

    asyncio.run(scenario())
