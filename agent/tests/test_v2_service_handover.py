"""Two actual framed administrator clients and exchange kernel, injected native/peer IO; never root or service writes."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_v2_darwin_service_exchange import Commands as DarwinCommands
from test_v2_system_service_exchange import Commands as LinuxCommands
from test_v2_system_service_exchange import Policy as BasePolicy
from test_v2_system_service_exchange import Writer, kernel
from test_v2_system_service_exchange import plan as base_plan

from omni.adapters.platform import service_admin_client as transport
from omni.adapters.platform import service_handover as module
from omni.adapters.platform.darwin_service_admin import DarwinAdministrator
from omni.adapters.platform.service_admin import SystemServiceAdministrator
from omni.adapters.platform.service_templates import render
from omni.adapters.primitives import SystemClock, UUIDs
from omni.adapters.workers.service_admin_boot import validate_sources
from omni.domain.contracts import BusinessError
from omni.domain.releases import make_release
from omni.domain.service_admin import admin_scope, bound_operation, request, scoped_operations, socket_path
from omni.domain.service_deployment import make_plan
from omni.domain.service_exchange import (
    ADMIN_PROTOCOL,
    administrator_approval,
    make_exchange,
    progress,
    stage_identity,
    stages,
)


def plan(target="linux-system", schema_change=False):
    initial = base_plan(target)
    release = initial["release"]
    source, destination = release["source"], copy.deepcopy(release["target"])
    transition = None
    if schema_change:
        from omni.adapters.persistence.schema_upgrade import route

        source = copy.deepcopy(source)
        source["runtime"]["schema_head"] = "v2_0025"
        from omni.adapters.persistence.migration_config import head as migration_head

        destination["runtime"]["schema_head"] = migration_head()
        root = Path(__file__).resolve().parents[1] / "src/omni"
        import hashlib

        for path in sorted((root / "adapters/persistence/migrations/versions").glob("v[0-9]*.py")):
            row = {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            destination["runtime"]["files"].append(row)
            if not (path.stem.startswith("v") and path.stem[1:].isdecimal() and int(path.stem[1:]) > 25):
                source["runtime"]["files"].append(row)
        for runtime in (source["runtime"], destination["runtime"]):
            runtime["files"].sort(key=lambda row: row["path"])
        source = make_plan(source["request"], source["runtime"], source["id"], source["created_at"])
        transition = route(source["runtime"], destination["runtime"])
    destination["runtime"]["files"][0]["sha256"] = "f" * 64
    destination = make_plan(
        destination["request"], destination["runtime"], destination["id"], destination["created_at"]
    )
    updated = make_release(
        source, destination, release["paths"], release["id"], release["created_at"], migration=transition
    )
    from omni.domain.service_control import make_control
    from omni.domain.service_operations import make_operation

    install = make_operation(destination, "install", initial["install_target"]["id"], initial["created_at"])
    control = make_control(
        destination, "start", initial["start_target"]["id"], initial["created_at"], initial["start_target"]["boot_id"]
    )
    return make_exchange(
        updated,
        make_operation(source, "uninstall", initial["remove_source"]["id"], initial["created_at"]),
        install,
        control,
        initial["journal"],
        initial["id"],
        initial["created_at"],
        {
            role: {"id": UUIDs().new(), "protocol": ADMIN_PROTOCOL, "bundle_sha256": char * 64}
            for role, char in (("source", "a"), ("target", "b"))
        },
    )


class Policy(BasePolicy):
    def __init__(self, approval, world):
        self.approval, self.world = admin_scope(approval), world
        self.audit, self.records, self.registry = world.audit, world.records, world.registry
        self.select(next(iter(scoped_operations(self.approval))))

    @property
    def enabled(self):
        return self.world.enabled

    @enabled.setter
    def enabled(self, value):
        self.world.enabled = value

    @property
    def loaded(self):
        return self.world.loaded

    @loaded.setter
    def loaded(self, value):
        self.world.loaded = value

    def program(self):
        return "/bin/launchctl" if self.plan["request"]["target"] == "macos-system" else "/fixed/root/systemctl"

    def definition(self):
        value = self.registry.get(self.manager["name"])
        return value is not None, None if value is None else render(value)[self.manager["artifact"]]

    def probe_argv(self, nonce):
        return ["/root/python", "-I", "-S", "/root/probe", self.manager["name"], nonce]

    def close(self):
        if self.world.active == self.approval["role"]:
            self.world.events.append(("released", self.approval["role"]))
            self.world.active = None


def build(monkeypatch, value, world=None, files=None, journals=None, wrong_boot=False):
    from omni.domain.service_deployment import manager_identity

    source = value["release"]["source"]
    world = world or SimpleNamespace(
        audit={},
        records=[],
        registry={manager_identity(source)["name"]: source},
        enabled=True,
        loaded=source,
        running=False,
        active=None,
        events=[],
        target_available=True,
        fail_enable=False,
    )
    original = transport.ExchangeAdministrator
    writers, commands = {}, {}

    def factory(approval, endpoint, fingerprint):
        parent = original(approval, endpoint, fingerprint)
        policy = Policy(approval, world)
        native_base = DarwinCommands if source["request"]["target"] == "macos-system" else LinuxCommands

        class Commands(native_base):
            def __init__(self):
                self.policy, self.calls = policy, []
                self.cleanup, self.fail_enable, self.control_files = True, world.fail_enable, None

            @property
            def running(self):
                return world.running

            @running.setter
            def running(self, value):
                world.running = value

        native = Commands()
        role = approval["role"]
        backend = (
            DarwinAdministrator(policy, native)
            if native_base is DarwinCommands
            else SystemServiceAdministrator(policy, native)
        )
        reader = asyncio.StreamReader()
        writer = Writer(reader, backend, fingerprint)

        async def connect():
            if (role == "target" and not world.target_available) or world.active not in (None, role):
                raise OSError("isolated administrator not admitted")
            assert not (role == "target" and "source" not in [r for event, r in world.events if event == "released"])
            world.active = role
            world.events.append(("connected", role))
            parent._reader, parent._writer = reader, writer

        monkeypatch.setattr(parent, "_connect", connect)
        writers[role], commands[role] = writer, native
        return parent

    monkeypatch.setattr(module, "ExchangeAdministrator", factory)
    handover = module.AdministratorHandover(value)
    handover.prepare_resume(0 if files is None else progress(value, files.rows)[0])

    class Ports:
        def lease(self, key, *, read_only=False):
            return handover.parents["source" if key == "remove_source" else "target"].lease(key, read_only=read_only)

        async def close(self, timeout):
            return await handover.close(timeout)

    service, files, journals, boot = kernel(value, Ports(), commands["target"], files, journals, wrong_boot)
    service.steps.handover = handover
    return SimpleNamespace(
        service=service,
        files=files,
        journals=journals,
        boot=boot,
        world=world,
        writers=writers,
        commands=commands,
        handover=handover,
    )


@pytest.mark.parametrize("target", ["linux-system", "macos-system"])
@pytest.mark.parametrize("fault", ["none", "install_unknown", "wrong_boot", "target_missing"])
def test_two_independent_bundles_complete_in_order_or_resume_without_native_replay(monkeypatch, target, fault):
    async def scenario():
        value = plan(target)
        r = build(monkeypatch, value, wrong_boot=fault == "wrong_boot")
        r.commands["target"].fail_enable = fault == "install_unknown"
        r.world.target_available = fault != "target_missing"
        result = await r.service.apply(value, value["sha256"])
        if fault != "none":
            assert result["state"] == ("awaiting_administrator" if fault == "target_missing" else "blocked")
            if fault == "target_missing":
                assert result["next_stage"] == "install_target" and result["active_stage"] == ""
                assert not any(row["state"] == "dispatch" and row["stage"] == "install_target" for row in r.files.rows)
                assert r.files.active
            assert await r.service.close(1) and not r.files.owned
            r.world.target_available = True
            r = build(monkeypatch, value, r.world, r.files, r.journals)
            result = await r.service.apply(value, value["sha256"], resume=True)
            assert r.writers["source"].requests == []
        assert result["state"] == "finished" and result["ready_at_completion"]
        assert result["completed_stages"] == list(stages(value))
        assert not r.files.active and not r.files.owned and r.world.active is None
        assert set(r.world.audit) == {value[key]["id"] for key in ("remove_source", "install_target", "start_target")}
        assert r.world.events.index(("released", "source")) < r.world.events.index(("connected", "target"))
        assert all(
            row["scope_sha256"] == administrator_approval(value, role)["sha256"]
            for role, writer in r.writers.items()
            for row in writer.requests
        )
        assert await r.service.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["linux-system", "macos-system"])
def test_cross_schema_exchange_uses_separate_administrators_and_rejects_one_parent(monkeypatch, target):
    async def scenario():
        value = plan(target, schema_change=True)
        assert value["release"]["schema_upgrade"]["source"] == "v2_0025"
        with pytest.raises(BusinessError) as caught:
            make_exchange(
                value["release"],
                value["remove_source"],
                value["install_target"],
                value["start_target"],
                value["journal"],
                value["id"],
                value["created_at"],
            )
        assert caught.value.code == "service_exchange_requires_schema_maintenance"
        r = build(monkeypatch, value)
        assert (await r.service.apply(value, value["sha256"]))["state"] == "finished"
        assert r.world.active is None and not r.files.active and not r.files.owned

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["source", "target"])
def test_cancelled_waiter_and_late_close_ack_retain_phase_and_activation_guard(monkeypatch, role):
    async def scenario():
        value = plan()
        r = build(monkeypatch, value)
        writer = r.writers[role]
        writer.hold = "close"
        caller = asyncio.create_task(r.service.apply(value, value["sha256"]))
        await writer.held.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert r.files.owned and r.files.active and not await r.service.close(0)
        assert progress(value, r.files.rows)[1] == "close_" + role
        if role == "source":
            assert ("connected", "target") not in r.world.events
        writer.hold = None
        writer.reply()
        assert (await r.service._pending)["state"] == "finished"
        assert await r.service.close(1) and not r.files.active and not r.files.owned

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["source", "target"])
def test_phase_limits_native_authority_and_source_proof_to_its_own_immutable_installation(role):
    value = plan()
    approval = administrator_approval(value, role)
    allowed = {"remove_source"} if role == "source" else {"install_target", "start_target"}
    assert set(scoped_operations(approval)) == allowed and admin_scope(approval) == approval
    own = value["release"][role]["runtime"]["files"][0]
    manifest = {"files": [{"source": own["path"], "sha256": own["sha256"], "bytes": own["bytes"]}]}
    validate_sources(manifest, approval)
    foreign = copy.deepcopy(approval)
    foreign["exchange"]["release"]["target" if role == "source" else "source"]["runtime"]["files"][0]["sha256"] = (
        "d" * 64
    )
    validate_sources(manifest, foreign)  # Static source ownership check ignores the non-executed side.
    with pytest.raises(BusinessError):
        admin_scope(foreign)
    for key in {"remove_source", "install_target", "start_target"} - allowed:
        with pytest.raises(BusinessError):
            request(approval, "change", 0, UUIDs().new(), approval["broker_sha256"], operation_key=key)
    with pytest.raises(BusinessError):
        request(approval, "inspect", 0, UUIDs().new(), "c" * 64, operation_key=next(iter(allowed)))
    with pytest.raises(BusinessError):
        admin_scope(value)
    assert Path(socket_path(approval)).name == approval["id"] + ".sock"
    assert bound_operation(approval, next(iter(allowed)))["id"] in {value[key]["id"] for key in allowed}


@pytest.mark.parametrize("stage", ["remove_source", "close_source"])
def test_new_root_session_cannot_replace_missing_original_cleanup_or_close_proof(monkeypatch, stage):
    async def scenario():
        value = plan()
        r = build(monkeypatch, value)
        at = SystemClock().now()
        rows = [{"state": "intent", "stage": "", "recorded_at": at, "proof": {}}]
        for name in stages(value):
            rows.append({"state": "dispatch", "stage": name, "recorded_at": at, "proof": {}})
            if name == stage:
                break
            identity, sha = stage_identity(value, name)
            rows.append(
                {
                    "state": "complete",
                    "stage": name,
                    "recorded_at": at,
                    "proof": {
                        "stage": name,
                        "operation_id": identity,
                        "operation_sha256": sha,
                        "succeeded": True,
                        "cleanup_complete": True,
                        "error_code": "",
                        "verification": "previous_confirmed_phase",
                    },
                }
            )
        r.files.rows = rows
        if stage == "remove_source":
            r.journals[stage].rows = [{"state": "dispatch", "cleanup_complete": False}]
            r.journals[stage].active = True
        with pytest.raises(BusinessError) as caught:
            await r.service.apply(value, value["sha256"], resume=True)
        assert caught.value.code == (
            "service_exchange_administrator_cleanup_unresolved"
            if stage == "remove_source"
            else "service_exchange_administrator_close_unresolved"
        )
        assert all(writer.requests == [] for writer in r.writers.values())
        assert r.files.rows == rows and r.files.active and not r.files.owned
        assert await r.service.close(1)

    asyncio.run(scenario())


def test_phase_export_is_private_read_only_and_cli_uses_reviewed_participant_file(tmp_path, monkeypatch):
    import json

    from omni.adapters.platform.deployment_files import CurrentDeploymentFiles
    from omni.adapters.platform.permissions import assert_private
    from omni.composition.cli import parser
    from omni.composition.service_exchange import run_exchange

    value = plan()
    path = tmp_path / "exchange.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "source-phase.json"
    # This entry publishes only an approval; it must not connect either broker.
    monkeypatch.setattr(
        transport.ExchangeAdministrator, "_connect", lambda *args: pytest.fail("Phase export is read-only")
    )
    result = asyncio.run(run_exchange("admin-phase", path, output=output, role="source"))
    approval = CurrentDeploymentFiles().read(output)
    assert result["executed"] is False and result["sha256"] == approval["sha256"]
    assert admin_scope(approval) == administrator_approval(value, "source")
    assert_private(output)
    parsed = parser().parse_args(
        ["exchange", "plan", "release.json", "journal", "plan.json", "--administrators", "participants.json"]
    )
    assert parsed.administrators == Path("participants.json")
    with pytest.raises(BusinessError):
        asyncio.run(run_exchange("admin-phase", path, output=output, role="source"))


@pytest.mark.parametrize("cleanup", [True, False])
def test_protocol4_close_ack_requires_command_cleanup_and_native_lock_release(monkeypatch, cleanup):
    from test_v2_service_admin import Commands, Policy
    from test_v2_service_admin_ownership import Connection

    from omni.adapters.platform import service_admin as server
    from omni.domain.service_admin import encode

    policy = Policy()
    commands = Commands(policy)
    commands.cleanup = cleanup
    events = []
    monkeypatch.setattr(policy, "close", lambda: events.append("lock_released"))
    packet = request(policy.approval, "close", 0, UUIDs().new(), "a" * 64)
    connection = Connection([encode(packet)])
    original = connection.sendall

    def send(data):
        assert events == ["lock_released"]
        original(data)

    monkeypatch.setattr(connection, "sendall", send)
    monkeypatch.setattr(server.select, "select", lambda descriptors, *args: (descriptors, [], []))
    monkeypatch.setattr(server, "connected", lambda _: False)
    server.serve_connection(connection, SystemServiceAdministrator(policy, commands), "a" * 64)
    assert events == (["lock_released"] if cleanup else [])
    assert len(connection.replies) == (1 if cleanup else 0)
    if cleanup:
        assert connection.replies[0]["report"] == {"closed": True, "cleanup_pending": False}
