"""Real V25 snapshot/new-home migration, holds/objects and crash fences; immutable installations/native manager isolated."""

from __future__ import annotations

import asyncio
import copy
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.deployment_files import installation as installation
from omni_test_support.v2.deployment_files import plan_for
from omni_test_support.v2.releases import Manager
from omni_test_support.v2.v2_configuration_helpers import add_model_headers_column
from omni_test_support.v2.v2_legacy_seed import LegacySeedRepositories

from omni.adapters.object_store import FileObjects
from omni.adapters.persistence import schema_upgrade as migration
from omni.adapters.persistence.deployed_instance import verify_deployed_instance
from omni.adapters.persistence.maintenance import verify_snapshot
from omni.adapters.persistence.migration_config import configuration
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.releases import ReleaseData, database_witness
from omni.adapters.persistence.sqlalchemy_access import database_engine
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.releases.release_files import ReleaseFiles
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.adapters.platform.services import deployment_files as d
from omni.adapters.primitives import SystemClock, UUIDs
from omni.application.releases import Releases
from omni.domain.contracts import BusinessError
from omni.domain.releases import FENCE_FILES, make_release, validate_release
from omni.domain.schema_upgrade import schema_upgrade
from omni.domain.service_control import make_control
from omni.domain.service_deployment import make_plan
from omni.domain.service_exchange import make_exchange
from omni.domain.service_operations import make_operation


def test_engine_sets_wal_before_serving_concurrent_connections(tmp_path, monkeypatch):
    statements = []
    connect = sqlite3.dbapi2.connect

    def track_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3.dbapi2, "connect", track_connection)
    engine = database_engine(tmp_path / "wal.db")
    try:
        with engine.connect() as first:
            first.exec_driver_sql("CREATE TABLE sample (value TEXT)")
            first.commit()
            with first.begin():
                first.exec_driver_sql("INSERT INTO sample VALUES ('pending')")
                with engine.connect() as reader:
                    assert reader.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
                    assert reader.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
                    assert reader.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                    assert reader.exec_driver_sql("SELECT count(*) FROM sample").scalar_one() == 0
        assert statements.count("PRAGMA journal_mode=WAL") == 1
    finally:
        engine.dispose()


def test_missing_predecessor_is_reported_before_opening_a_write_connection(tmp_path, monkeypatch):
    directory = tmp_path / "migrations"
    (directory / "versions").mkdir(parents=True)
    (directory / "versions/v0026.py").write_text(
        "revision='v2_0026'\ndown_revision='v2_0025'\nbranch_labels=None\ndepends_on=None\n", encoding="utf-8"
    )
    config = Config()
    config.set_main_option("script_location", str(directory))
    monkeypatch.setattr(migration, "configuration", lambda: config)
    monkeypatch.setattr(migration, "database_engine", lambda *args: pytest.fail("No writes before route validation"))
    with pytest.warns(UserWarning, match="v2_0025"), pytest.raises(BusinessError) as caught:
        migration.route({"schema_head": "v2_0025", "files": []}, {"schema_head": "v2_0026", "files": []})
    assert caught.value.code == "release_schema_catalog_invalid"


@pytest.fixture
def upgrading(installation, tmp_path, monkeypatch):
    deployments, package, _assets, binary, _config = installation
    source_python = d._python
    actual = PRODUCT_ROOT / "src/omni"
    for name in FENCE_FILES:
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((actual / name).read_bytes())
    for path in (actual / "adapters/persistence/migrations").rglob("*.py"):
        if path.stem.startswith("v") and path.stem[1:].isdecimal() and int(path.stem[1:]) > 25:
            continue
        target = package / path.relative_to(actual)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    paths = InstancePaths(tmp_path / "source-home")
    paths.initialize()
    lock = InstanceLock(paths)
    lock.acquire()
    clock, ids = SystemClock(), UUIDs()
    engine = database_engine(paths.database)
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            with connection.begin():
                cfg = configuration()
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "v2_0025")
                add_model_headers_column(connection)
                unit = LegacySeedRepositories(connection, clock, ids, ids.new())
                identity = unit.initialize()["id"]
                agent = unit.query("agents", "")[0]["id"]
                unit.issue_token("a" * 64, "2099-01-01T00:00:00+00:00")
                received = unit.accept_turn(
                    agent, None, "pending-message", "preserve pending request", unit.settings(agent)
                )
                call = unit.reserve_call(agent, received["run_id"], 12345, 1000000, "offline-fixture")
                blob = FileObjects(paths.objects).put(agent, b"immutable registered evidence")
                unit.object_register(blob, "text/plain", "2099-01-01T00:00:00+00:00", True)
    finally:
        engine.dispose()
        lock.close()
    monkeypatch.setattr(d, "migration_head", lambda: "v2_0025")
    original = plan_for(deployments, tmp_path, bundle="source-bundle")
    source = make_plan(
        {**original["request"], "home": str(paths.home), "instance_id": identity},
        original["runtime"],
        ids.new(),
        clock.now(),
    )
    deployments.materialize(source, source["request"]["bundle_dir"])
    next_package = tmp_path / "next-program/omni"
    shutil.copytree(package, next_package)
    for path in (actual / "adapters/persistence/migrations/versions").glob("v[0-9]*.py"):
        if int(path.stem[1:]) > 25:
            (next_package / path.relative_to(actual)).write_bytes(path.read_bytes())
    prefix = tmp_path / "next-environment"
    shutil.copytree(binary.parent.parent, prefix)
    monkeypatch.setattr(d, "_installation", lambda: (next_package, next_package / "web_assets", False))
    monkeypatch.setattr(
        d,
        "_python",
        lambda: {**d.python_files(prefix / "bin/python.exe", prefix), "python_version": sys.version.replace("\n", " ")},
    )
    monkeypatch.setattr(d, "migration_head", migration_head)
    files = ReleaseFiles(deployments)
    data, manager = ReleaseData(files), Manager()
    service = Releases(deployments, files, data, manager, clock, ids)
    request = {
        "target": {
            **source["request"],
            "home": str(tmp_path / "next-home"),
            "bundle_dir": str(tmp_path / "next-bundle"),
        },
        "backup": str(tmp_path / "snapshot"),
        "journal": str(tmp_path / "journal"),
    }
    value = service.draft(source, request)
    verify_deployed_instance(paths.database, identity, schema_version="v2_0025")
    return SimpleNamespace(
        plan=value,
        service=service,
        files=files,
        data=data,
        source=paths,
        manager=manager,
        source_witness=database_witness(paths.home),
        agent=agent,
        call=call,
        blob=blob,
        package=next_package,
        request=request,
        deployments=deployments,
        source_package=package,
        source_python=source_python,
    )


@pytest.mark.parametrize("newer_data", [False, True])
def test_schema_recovery_uses_original_installation_snapshot_and_rejects_newer_failed_data(
    upgrading, monkeypatch, newer_data
):
    r = upgrading
    r.service.prepare(r.plan, r.plan["sha256"])
    assert asyncio.run(r.service.select(r.plan, r.plan["sha256"]))["state"] == "selected"
    failed = r.files.homes(r.plan)[1]
    original = r.service.prepared(r.files.history(r.plan))
    if newer_data:
        with sqlite_connection(failed.database, read_only=False) as connection:
            connection.execute("UPDATE messages SET content='newer business data; must survive'")
    verify_deployed_instance(
        failed.database, r.plan["target"]["request"]["instance_id"], schema_version=migration_head()
    )
    witness = database_witness(failed.home)
    monkeypatch.setattr(d, "_installation", lambda: (r.source_package, r.source_package / "web_assets", False))
    monkeypatch.setattr(d, "_python", r.source_python)
    monkeypatch.setattr(d, "migration_head", lambda: "v2_0025")
    assert r.deployments.capture() == r.plan["source"]["runtime"]
    parent = r.source.home.parent
    request = {
        "target": {
            **r.plan["source"]["request"],
            "home": str(parent / "rollback-home"),
            "bundle_dir": str(parent / "rollback-bundle"),
        },
        "backup": str(parent / "failed-snapshot"),
        "journal": str(parent / "rollback-journal"),
    }
    recovery = {
        "release_sha256": r.plan["sha256"],
        "snapshot": original["backup"],
        "manifest_sha256": original["backup_manifest_sha256"],
        "isolation": None,
        "restore_policy": "original_snapshot",
        "expected_source_witness": original["target_witness"],
    }
    service = Releases(r.deployments, r.files, r.data, r.manager, SystemClock(), UUIDs())
    plan = service.draft(r.plan["target"], request, recovery)
    assert plan["schema_upgrade"] is None and plan["target"]["runtime"]["schema_head"] == "v2_0025"
    if newer_data:
        with pytest.raises(BusinessError) as caught:
            service.prepare(plan, plan["sha256"])
        assert caught.value.code == "release_recovery_source_changed"
        assert database_witness(failed.home) == witness
        return
    result = service.prepare(plan, plan["sha256"])
    assert result["state"] == "prepared" and result["restores_schema_from_snapshot"]
    target = r.files.homes(plan)[1]
    with sqlite_connection(target.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [("v2_0025",)]
        assert connection.execute("SELECT held_micro FROM budget_accounts").fetchone() == (12345,)
    assert not (target.control / "schema-upgrade-intent.json").exists()
    assert asyncio.run(service.select(plan, plan["sha256"]))["state"] == "selected"
    assert database_witness(failed.home) == witness and failed.database.exists()


def test_verified_new_home_upgrade_preserves_source_holds_objects_and_fences_selection(upgrading):
    r = upgrading
    assert validate_release(r.plan) == r.plan and r.plan["format"] == "omni-release-plan-3"
    assert r.plan["schema_upgrade"]["source"] == "v2_0025" and r.plan["schema_upgrade"]["target"] == migration_head()
    result = r.service.prepare(r.plan, r.plan["sha256"])
    assert result["state"] == "prepared" and result["upgrades_schema"] and result["ready"] is False
    _, target = r.files.homes(r.plan)
    assert database_witness(r.source.home) == r.source_witness
    backup = verify_snapshot(Path(r.plan["paths"]["backup"]))
    assert backup["database"]["schema"] == "v2_0025" and backup["database"]["unknown_model_calls"] == 1
    with sqlite_connection(target.database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(migration_head(),)]
        assert connection.execute("SELECT state,reserved_micro FROM model_calls WHERE id=?", (r.call,)).fetchone() == (
            "unknown",
            12345,
        )
        assert connection.execute("SELECT held_micro,spent_micro FROM budget_accounts").fetchall() == [(12345, 0)]
        assert connection.execute("SELECT state FROM work_items WHERE kind='turn'").fetchall() == [("unknown",)]
        assert connection.execute("SELECT revoked_at FROM auth_sessions").fetchone()[0] is not None
        assert connection.execute("SELECT count(*) FROM terminal_reviews").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM audit WHERE kind='schema.upgraded'").fetchone() == (1,)
    assert FileObjects(target.objects).read(r.blob) == b"immutable registered evidence"
    assert not (target.control / "owner.token").exists()
    with pytest.raises(BusinessError):
        InstanceLock(target).acquire()
    assert asyncio.run(r.service.select(r.plan, r.plan["sha256"]))["state"] == "selected"
    assert database_witness(r.source.home) == r.source_witness
    with pytest.raises(BusinessError):
        InstanceLock(r.source).acquire()
    lock = InstanceLock(target)
    lock.acquire()
    lock.close()
    assert r.manager.calls == 1


@pytest.mark.parametrize("fault", ["transaction", "post_commit_report"])
def test_interrupted_upgrade_never_replays_or_opens_target_and_keeps_verified_source(upgrading, monkeypatch, fault):
    r = upgrading
    if fault == "transaction":

        def interrupted(config, revision):
            connection = config.attributes["connection"]
            connection.exec_driver_sql("CREATE TABLE should_rollback (value INTEGER)")
            raise OSError("injected migration termination")

        monkeypatch.setattr(migration.command, "upgrade", interrupted)
    else:
        original = d.CurrentDeploymentFiles._publish_json

        def failed_report(self, path, data):
            if path.name == "schema-upgrade-report.json":
                raise OSError("injected report publication failure")
            return original(self, path, data)

        monkeypatch.setattr(d.CurrentDeploymentFiles, "_publish_json", failed_report)
    with pytest.raises(OSError):
        r.service.prepare(r.plan, r.plan["sha256"])
    _, target = r.files.homes(r.plan)
    assert database_witness(r.source.home) == r.source_witness and r.manager.calls == 0
    assert verify_snapshot(Path(r.plan["paths"]["backup"]))["database"]["schema"] == "v2_0025"
    assert (target.control / "schema-upgrade-intent.json").exists() and not (
        target.control / "schema-upgrade-report.json"
    ).exists()
    with sqlite_connection(target.database) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchall() == []
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "v2_0025" if fault == "transaction" else migration_head()
        )
    with pytest.raises(BusinessError):
        InstanceLock(target).acquire()
    with pytest.raises(BusinessError):
        r.service.prepare(r.plan, r.plan["sha256"])
    assert r.service.abandon(r.plan, r.plan["sha256"])["state"] == "abandoned"
    assert target.database.exists()


@pytest.mark.parametrize("damage", ["intent", "report", "program"])
def test_changed_migration_proof_or_program_cannot_select_target(upgrading, damage):
    r = upgrading
    r.service.prepare(r.plan, r.plan["sha256"])
    _, target = r.files.homes(r.plan)
    if damage == "program":
        path = r.package / "adapters/persistence/migrations/versions/v0026.py"
    else:
        path = target.control / ("schema-upgrade-" + damage + ".json")
    path.write_bytes(b"changed; preserve for review")
    with pytest.raises((BusinessError, ValueError)):
        asyncio.run(r.service.select(r.plan, r.plan["sha256"]))
    assert r.service.inspect(r.plan)["state"] == "prepared" and r.manager.calls == 0
    assert not (r.source.control / "release-fence.json").exists()


def test_cross_schema_user_exchange_requires_full_route_and_retains_upgrade_proof(upgrading):
    r = upgrading
    for change in ("missing", "baseline", "parent", "fingerprint", "backwards"):
        old, new, transition = (
            copy.deepcopy(r.plan["source"]),
            copy.deepcopy(r.plan["target"]),
            copy.deepcopy(r.plan["schema_upgrade"]),
        )
        if change == "missing":
            transition["revisions"].pop(3)
        elif change == "baseline":
            transition["revisions"][0]["sha256"] = "f" * 64
        elif change == "parent":
            transition["revisions"][-1]["parent"] = "v2_0001"
        elif change == "fingerprint":
            transition["revisions"][-1]["sha256"] = "f" * 64
        else:
            old, new = new, old
        with pytest.raises(BusinessError):
            schema_upgrade(old["runtime"], new["runtime"], transition)
    with pytest.raises(BusinessError):
        make_release(r.plan["source"], r.plan["target"], r.plan["paths"], r.plan["id"], r.plan["created_at"])
    ids, clock = UUIDs(), SystemClock()
    exchange = make_exchange(
        r.plan,
        make_operation(r.plan["source"], "uninstall", ids.new(), clock.now()),
        make_operation(r.plan["target"], "install", ids.new(), clock.now()),
        make_control(r.plan["target"], "start", ids.new(), clock.now(), ids.new()),
        str(r.source.home.parent / "exchange-journal"),
        ids.new(),
        clock.now(),
    )
    assert exchange["release"]["schema_upgrade"] == r.plan["schema_upgrade"]
    assert exchange["administrators"] is None and exchange["automatic_rollback"] is False


def test_real_cross_schema_user_exchange_reuses_prepared_data_and_once_only_launch(upgrading):
    from omni.adapters.platform.services import service_launch
    from omni.adapters.platform.services.exchange_deployments import ExchangeSourceDeployment
    from omni.adapters.platform.services.exchange_files import ExchangeFiles
    from omni.adapters.platform.services.service_control_files import ServiceControlFiles
    from omni.adapters.platform.services.service_operation_files import ServiceOperationFiles
    from omni.application.exchange_steps import ExchangeSteps
    from omni.application.service_control import ServiceControl
    from omni.application.service_exchange import ServiceExchange
    from omni.application.service_operations import ServiceOperations
    from omni.domain.service_deployment import startup
    from omni.domain.service_exchange import marker

    r = upgrading
    r.service.prepare(r.plan, r.plan["sha256"])
    clock, ids = SystemClock(), UUIDs()
    value = make_exchange(
        r.plan,
        make_operation(r.plan["source"], "uninstall", ids.new(), clock.now()),
        make_operation(r.plan["target"], "install", ids.new(), clock.now()),
        make_control(r.plan["target"], "start", ids.new(), clock.now(), ids.new()),
        str(r.source.home.parent / "exchange-journal"),
        ids.new(),
        clock.now(),
    )
    reservation = marker(value)
    files = ReleaseFiles(r.deployments, exchange=reservation)
    target = files.homes(r.plan)[1]
    world = SimpleNamespace(installed={"source": True, "target": False}, boot=None, lock=None, calls=[])

    class Native:
        def __init__(self, role):
            self.role = role

        async def inspect(self, plan):
            present = world.installed[self.role]
            return {
                "available": True,
                "installed": present,
                "definition_present": present,
                "definition_matches": present,
                "enabled": present,
                "state": "running" if self.role == "target" and world.boot else "stopped",
                "pid": 42 if self.role == "target" and world.boot else None,
            }

        async def change(self, plan, action):
            world.calls.append((self.role, action))
            with pytest.raises(BusinessError):
                InstanceLock(r.source if self.role == "source" else target).acquire()
            world.installed[self.role] = action == "install"
            return {"effect": "applied"}

        async def start(self, plan):
            world.calls.append((self.role, "start"))
            world.lock = InstanceLock(target)
            world.lock.acquire()
            world.boot = service_launch.consume_launch(target.control, startup(plan))
            return {"effect": "applied"}

        async def close(self, timeout):
            return True

    class Boot:
        async def probe(self, plan):
            return {"ready": world.boot is not None, "pid": 42, "boot_id": world.boot}

        async def close(self, timeout):
            return True

    selection = Releases(r.deployments, files, ReleaseData(files), Native("source"), clock, ids)
    source = ServiceOperations(
        ExchangeSourceDeployment(r.plan, files),
        ServiceOperationFiles(exchange=reservation),
        Native("source"),
        clock,
        ids,
    )
    destination = ServiceOperations(
        r.deployments, ServiceOperationFiles(exchange=reservation), Native("target"), clock, ids
    )
    control = ServiceControl(
        r.deployments, ServiceControlFiles(exchange=reservation), Native("target"), Boot(), clock, ids
    )
    service = ServiceExchange(
        ExchangeFiles(r.deployments), ExchangeSteps(source, destination, selection, control), clock, ids
    )

    async def scenario():
        try:
            result = await service.apply(value, value["sha256"])
            assert result["state"] == "finished" and result["ready_at_completion"]
            assert world.boot == value["start_target"]["boot_id"]
            assert world.calls == [("source", "uninstall"), ("target", "install"), ("target", "start")]
            assert not (target.control / "service-exchange.json").exists()
            with sqlite_connection(target.database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (migration_head(),)
                assert connection.execute("SELECT held_micro FROM budget_accounts").fetchone() == (12345,)
        finally:
            assert await service.close(1)
            if world.lock is not None:
                world.lock.close()

    asyncio.run(scenario())
