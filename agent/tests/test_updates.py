"""更新协议与真实文件事务回归；HTTP、命令、进程和时钟均隔离注入。"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import stat
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from omni.system.updates.archive import extract, verify
from omni.system.updates.common import UpdateError, digest, read_json, write_json
from omni.system.updates.gate import MaintenanceGate
from omni.system.updates.gitee import GiteeClient, TrustedRedirect
from omni.system.updates.manager import UpdateManager
from omni.system.updates.snapshot import capture, restore
from omni.system.updates.version import identify, inventory, semver
from omni.system.updates.worker import Transaction

OLD, NEW = "a" * 40, "b" * 40


def test_inventory_excludes_local_document_archive_from_both_scan_modes(tmp_path):
    guide = tmp_path / "docs/v/HANDOFF.md"
    note = tmp_path / "docs/temp/v2/old.md"
    for file in (guide, note):
        file.parent.mkdir(parents=True)
        file.write_text("example", encoding="utf-8")
    expected = {"docs/v/HANDOFF.md": digest(guide)}
    assert inventory(tmp_path) == expected
    assert inventory(tmp_path, ["docs/v/HANDOFF.md", "docs/temp/v2/old.md"]) == expected


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def source(root, version="0.1.0", commit=OLD):
    files = {
        "pyproject.toml": '[project]\nname="workspace"\n',
        "uv.lock": "version = 1\n",
        "agent/pyproject.toml": '[project]\nname="omnioperator"\n',
        "agent/src/omni/__init__.py": f'__version__ = "{version}"\n',
        "agent/src/omni/system/updates/worker.py": "PROTOCOL_VERSION = 1\n",
        "web/index.html": f"<!doctype html><title>{version}</title><main>界面{version}</main>",
        "web/ui/app/main.js": f"export const version = '{version}';\n",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    receipt = {"schema": 1, "version": version, "commit": commit, "files": inventory(root)}
    write_json(root / "omni-build.json", receipt)
    return receipt


def package(root, target):
    with zipfile.ZipFile(target, "w") as archive:
        for name in inventory(root):
            archive.write(root / name, "project/" + name)
        archive.write(root / "omni-build.json", "project/omni-build.json")


@pytest.mark.parametrize(
    "lower,higher",
    [("0.9.0", "0.10.0"), ("1.0.0-alpha.2", "1.0.0-alpha.10"), ("1.0.0-rc.1", "1.0.0"), ("1.0.0-1", "1.0.0-alpha")],
)
def test_semver_order(lower, higher):
    assert semver(lower) < semver(higher)
    assert semver("v1.2.3+build.4") == semver("1.2.3")


@pytest.mark.parametrize("value", ["1.2", "01.2.3", "1.2.3-01", "1.2.3-alpha..1"])
def test_invalid_semver(value):
    with pytest.raises(UpdateError):
        semver(value)


def test_archive_identity_detects_changed_deleted_and_extra_files(tmp_path):
    source(tmp_path)
    assert not identify(tmp_path)["dirty"]
    extra = tmp_path / "agent/local.py"
    extra.write_text("local = True")
    assert "agent/local.py" in identify(tmp_path)["dirty"]
    (tmp_path / "uv.lock").unlink()
    assert "uv.lock" in identify(tmp_path)["dirty"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://gitee.com/chenfengloveyuri/OmniOperator.git",
        "git@gitee.com:chenfengloveyuri/OmniOperator.git",
        "ssh://git@gitee.com/chenfengloveyuri/OmniOperator.git",
        "ssh://git@gitee.com:22/chenfengloveyuri/OmniOperator.git",
    ],
)
def test_official_https_and_ssh_git_origins(tmp_path, monkeypatch, origin):
    from omni.system.updates import version

    source(tmp_path)
    (tmp_path / ".git").mkdir()

    def command(root, *args):
        return OLD if args[0] == "rev-parse" else origin if args[0] == "remote" else ""

    monkeypatch.setattr(version, "git", command)
    assert identify(tmp_path)["installation"] == "git"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.invalid/chenfengloveyuri/OmniOperator.git",
        "git@gitee.com:someone/OmniOperator.git",
        "file:///local/repository",
    ],
)
def test_other_git_repositories_are_rejected(tmp_path, monkeypatch, origin):
    from omni.system.updates import version

    source(tmp_path)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        version, "git", lambda root, *args: OLD if args[0] == "rev-parse" else origin if args[0] == "remote" else ""
    )
    with pytest.raises(UpdateError, match="官方 Gitee"):
        identify(tmp_path)


def test_archive_pinned_commit_and_protected_user_directories(tmp_path):
    original = tmp_path / "origin"
    receipt = source(original, "0.2.0", NEW)
    archive = tmp_path / "source.zip"
    package(original, archive)
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("project/data/state.db", "not allowed to install")
        output.writestr("project/.env", "SECRET=payload")
        output.writestr("project/skills/local/SKILL.md", "do not overwrite")
    target = tmp_path / "target"
    assert extract(archive, target, NEW) == receipt
    assert not (target / "data").exists() and not (target / ".env").exists()
    assert not (target / "skills").exists()
    verify(target, receipt)
    (target / "uv.lock").write_text("tampered")
    with pytest.raises(UpdateError, match="变化"):
        verify(target, receipt)


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "a/../../b", "a\\b", "C:/x", "folder/file:stream", "folder/CON.txt", "folder/name."],
)
def test_archive_rejects_unsafe_paths_before_writing(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as output:
        entry = zipfile.ZipInfo()
        entry.filename = name  # 保留传入 ZIP 的原始反斜杠，避免 Windows 写包时自动规范化。
        output.writestr(entry, "bad")
    with pytest.raises(UpdateError):
        extract(archive, tmp_path / "target", NEW)
    assert not (tmp_path / "target").exists()


def test_archive_rejects_symlink_and_case_collision(tmp_path):
    for index, symlink in enumerate((True, False)):
        archive = tmp_path / f"{index}.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("project/pyproject.toml", "test")
            if symlink:
                entry = zipfile.ZipInfo("project/agent/link")
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                output.writestr(entry, "outside")
            else:
                output.writestr("project/PYPROJECT.toml", "collision")
        with pytest.raises(UpdateError):
            extract(archive, tmp_path / f"target{index}", NEW)


def test_gitee_chooses_semver_not_edit_time_and_ignores_previews():
    client = GiteeClient()
    paths = []

    def response(path):
        paths.append(path)
        if path.startswith("/releases"):
            return [
                {"tag_name": "v0.9.0"},
                {"tag_name": "v0.10.0"},
                {"tag_name": "v8.0.0", "prerelease": True},
                {"tag_name": "invalid"},
                {"tag_name": "v9.0.0-beta.1"},
            ]
        return {"sha": NEW}

    client.json = response
    candidate = client.candidate("stable")
    assert candidate["version"] == "0.10.0" and candidate["commit"] == NEW
    assert paths[-1] == "/commits/v0.10.0"
    client.json = lambda path: []
    assert client.candidate("stable") is None


@pytest.mark.parametrize(
    "ancestor,expected", [(OLD, "behind"), (NEW, "ahead"), ("c" * 40, "diverged"), ("", "unknown")]
)
def test_gitee_commit_relationship(ancestor, expected):
    client = GiteeClient()
    client.json = lambda _: {"merge_base_commit": {"sha": ancestor}}
    assert client.relation(OLD, NEW) == expected
    assert client.relation(NEW, NEW) == "equal"


def test_gitee_download_is_pinned_bounded_and_redirects_are_restricted(tmp_path):
    urls = []

    class Opener:
        def open(self, request, timeout):
            urls.append(request.full_url)
            return io.BytesIO(b"PK\x03\x04archive")

    client = GiteeClient(opener=Opener())
    client.download(NEW, tmp_path / "out.zip")
    assert urls == [f"https://gitee.com/api/v5/repos/chenfengloveyuri/OmniOperator/zipball?ref={NEW}"]
    with pytest.raises(UpdateError):
        client._read(urls[0], limit=2)
    with pytest.raises(UpdateError):
        client.download("master", tmp_path / "other.zip")
    with pytest.raises(UpdateError):
        TrustedRedirect().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/secret")


def test_gate_serializes_entrance_and_preserves_admitted_work(tmp_path):
    gate = MaintenanceGate(tmp_path)
    gate.enter("web")
    try:
        with gate.work():
            gate.begin({"phase": "draining", "probe_token": "token"})
            assert gate.busy == 1 and gate.accepting()
            with gate.work():
                assert gate.busy == 2
        assert not gate.accepting()
        with pytest.raises(UpdateError):
            with gate.work():
                pass
        with pytest.raises(UpdateError):
            MaintenanceGate(tmp_path).enter("cli")
        assert len(gate.peers()) == 1
    finally:
        gate.leave()
    assert gate.peers() == []


def test_private_snapshot_preserves_credentials_and_external_database(tmp_path):
    external = tmp_path / "external.db"
    with database(external) as db:
        db.execute("CREATE TABLE evidence(value)")
        db.execute("INSERT INTO evidence VALUES ('记忆')")
    secret = tmp_path / ".env"
    secret.write_text("KEY=private-value")
    sources = [{"path": str(external), "kind": "database"}, {"path": str(secret), "kind": "file"}]
    point = tmp_path / "snapshot"
    capture(point, sources)
    secret.write_text("changed")
    with database(external) as db:
        db.execute("DELETE FROM evidence")
    restore(point, sources)
    assert secret.read_text() == "KEY=private-value"
    with database(external) as db:
        assert db.execute("SELECT value FROM evidence").fetchone()[0] == "记忆"
    restore(point, sources)  # 恢复本身可在崩溃后重复。


def test_corrupt_snapshot_is_rejected_before_overwriting_any_file(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("original")
    sources = [{"path": str(secret), "kind": "file"}]
    point = tmp_path / "point"
    capture(point, sources)
    secret.write_text("keep this")
    (point / "0/file").write_text("corrupted")
    with pytest.raises(UpdateError):
        restore(point, sources)
    assert secret.read_text() == "keep this"


def test_atomic_state_retries_windows_sharing_violation(tmp_path, monkeypatch):
    from omni.system.updates import common

    original = common.os.replace
    target = tmp_path / "state.json"
    calls = []

    def replace(source, destination):
        if Path(destination) == target:
            calls.append(1)
            if len(calls) == 1:
                error = PermissionError("sharing violation")
                error.winerror = 32
                raise error
        return original(source, destination)

    monkeypatch.setattr(common.os, "replace", replace)
    write_json(target, {"phase": "ready"})
    assert len(calls) == 2 and read_json(target) == {"phase": "ready"}


def test_atomic_state_read_write_concurrency(tmp_path):
    import threading

    target = tmp_path / "state.json"
    write_json(target, {"value": 0})
    failures = []

    def writer():
        try:
            for index in range(50):
                write_json(target, {"value": index})
        except Exception as exc:
            failures.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    for _ in range(100):
        assert 0 <= read_json(target)["value"] < 50
    thread.join(10)
    assert not thread.is_alive() and not failures


def test_gate_only_clears_its_own_operation(tmp_path):
    gate = MaintenanceGate(tmp_path)
    gate.begin({"operation": "a" * 32, "phase": "stopping"})
    with pytest.raises(UpdateError):
        gate.clear("b" * 32)
    assert gate.active()
    gate.clear("a" * 32)
    assert not gate.active()


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / "installed"
    source(root)
    config = tmp_path / "config.yaml"
    config.write_text("updates:\n  channel: development\n")
    future = tmp_path / "future"
    source(future, "0.2.0", NEW)
    archive = tmp_path / "future.zip"
    package(future, archive)

    class Client:
        calls = 0

        def candidate(self, channel):
            self.calls += 1
            return {"version": "0.2.0", "commit": NEW, "notes": "new", "url": "https://gitee.com", "channel": channel}

        def relation(self, current, target):
            return "behind"

        def download(self, commit, destination):
            shutil.copy2(archive, destination)

    manager = UpdateManager(root=root, control=tmp_path / "control", config_path=config, enabled=False, client=Client())
    yield manager
    manager.close()


def wait(manager, identifier, phase):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = manager.operation(identifier)
        if result["phase"] == phase:
            return result
        if result["phase"] == "failed":
            pytest.fail(str(result))
        time.sleep(0.01)
    pytest.fail("update operation did not finish")


def test_manager_cache_prepare_idempotence_and_stale_baseline(manager):
    first = manager.check()
    manager.check(manual=False)
    assert manager.client.calls == 1
    operation = manager.prepare(first["candidate"]["id"])
    ready = wait(manager, operation["id"], "ready")
    assert manager.prepare(first["candidate"]["id"])["id"] == ready["id"]
    assert identify(manager.root)["version"] == "0.1.0"
    plan = read_json(manager.control / "operations" / operation["id"] / "plan.json")
    assert Path(plan["runner"]).is_file() and Path(plan["base_python"]).is_file()
    assert plan["receipt"]["commit"] == NEW
    with pytest.raises(UpdateError):
        manager.install(operation["id"], confirm=False)
    with pytest.raises(UpdateError):
        manager.install(operation["id"], confirm=True)


def test_manager_local_changes_and_network_failures_are_explicit(manager):
    result = manager.check()
    (manager.root / "uv.lock").write_text("my local edit")
    with pytest.raises(UpdateError, match="本地修改"):
        manager.prepare(result["candidate"]["id"])

    def fail(_):
        raise UpdateError("network_error", "offline")

    manager.client.candidate = fail
    assert manager.check()["error_code"] == "network_error"


def transaction_fixture(tmp_path):
    root = tmp_path / "installed"
    baseline = source(root)
    stage = tmp_path / "stage"
    receipt = source(stage, "0.2.0", NEW)
    env = root / ".venv"
    env.mkdir()
    (env / "old-marker").write_text("old environment")
    memory = tmp_path / "external.db"
    with database(memory) as db:
        db.execute("CREATE TABLE original(value)")
        db.execute("INSERT INTO original VALUES (42)")
    control = tmp_path / "control"
    operation = control / "operations" / ("d" * 32)
    plan = {
        "root": str(root),
        "control": str(control),
        "stage": str(stage),
        "receipt": receipt,
        "installation": "archive",
        "baseline_commit": OLD,
        "baseline_files": baseline["files"],
        "sources": [{"path": str(memory), "kind": "database"}],
        "uv": "fake-uv",
        "probe_token": "test",
        "restart": False,
    }
    write_json(operation / "plan.json", plan)
    write_json(operation / "state.json", {"id": operation.name, "phase": "stopping"})
    write_json(control / "maintenance.json", {"operation": operation.name, "probe_token": "test"})
    return root, memory, operation


@pytest.mark.parametrize("failure", ["sync", "rehearsal", "startup", "success"])
def test_transaction_restores_source_environment_and_migrated_data(tmp_path, failure):
    root, memory, operation = transaction_fixture(tmp_path)

    def execute(argv, **kwargs):
        if argv[0] == "fake-uv":
            (root / ".venv").mkdir()
            (root / ".venv/new-marker").write_text("new environment")
            if failure == "sync":
                raise UpdateError("sync_failed", "dependency failure")
        elif failure == "rehearsal":
            raise UpdateError("migration_failed", "migration failure")

    transaction = Transaction(operation, execute=execute)
    transaction.start_web = lambda **kwargs: {}

    def ready(_):
        with database(memory) as db:
            db.execute("ALTER TABLE original ADD COLUMN migrated")
        if failure == "startup":
            raise UpdateError("startup_failed", "health failure")

    transaction.wait_ready = ready
    transaction.run()
    assert not (operation.parents[1] / "maintenance.json").exists()
    if failure == "success":
        assert transaction.state["phase"] == "succeeded"
        assert identify(root)["version"] == "0.2.0" and (root / ".venv/new-marker").exists()
        assert "0.2.0" in (root / "web/index.html").read_text(encoding="utf-8")
        assert "0.2.0" in (root / "web/ui/app/main.js").read_text(encoding="utf-8")
    else:
        assert transaction.state["phase"] == "rolled_back", transaction.state
        assert transaction.state["error_message"] and transaction.state["failed_phase"] in {
            "environment",
            "rehearsing",
            "starting",
        }
        assert identify(root)["version"] == "0.1.0" and (root / ".venv/old-marker").exists()
        assert "0.1.0" in (root / "web/index.html").read_text(encoding="utf-8")
        assert "0.1.0" in (root / "web/ui/app/main.js").read_text(encoding="utf-8")
        with database(memory) as db:
            assert [row[1] for row in db.execute("PRAGMA table_info(original)")] == ["value"]
            assert db.execute("SELECT value FROM original").fetchone()[0] == 42


def test_recovery_does_not_revert_committed_data(tmp_path):
    root, memory, operation = transaction_fixture(tmp_path)
    write_json(operation / "state.json", {"phase": "succeeded", "committed": True})
    transaction = Transaction(operation)
    transaction.run(recover=True)
    assert transaction.state["phase"] == "succeeded" and memory.exists()


def test_failure_diagnostics_never_echo_raw_secrets():
    from omni.system.updates.common import error_message

    assert "PRIVATE_KEY" not in error_message(PermissionError("PRIVATE_KEY=do-not-expose"))
    assert "PRIVATE_KEY" not in error_message(RuntimeError("PRIVATE_KEY=do-not-expose"))


def test_gitee_requires_login_falls_back_to_isolated_git_without_token(tmp_path):
    calls = []

    def execute(argv, **kwargs):
        calls.append(argv)
        if "rev-parse" in argv:
            return NEW
        if "archive" in argv:
            Path(next(arg[9:] for arg in argv if arg.startswith("--output="))).write_bytes(b"PK\x03\x04")
        return ""

    client = GiteeClient(execute=execute)

    def login(*args, **kwargs):
        raise UpdateError("authentication_required", "login")

    client._read = login
    client.download(NEW, tmp_path / "source.zip")
    assert len(calls) == 4
    assert NEW in calls[1] and "master" not in " ".join(calls[1])
    assert Path(calls[0][-1]).is_relative_to(tmp_path)


def test_channel_change_invalidates_prepared_candidate(manager):
    operation = manager.prepare(manager.check()["candidate"]["id"])
    wait(manager, operation["id"], "ready")
    manager.save_policy({"channel": "stable", "auto_check": False})
    assert manager.operation(operation["id"])["phase"] == "cancelled"
    assert manager.status()["check"]["candidate"] is None


def test_services_from_other_agent_control_roots_block_install(manager):
    operation = manager.prepare(manager.check()["candidate"]["id"])
    wait(manager, operation["id"], "ready")
    manager.enabled = True
    manager.shutdown = lambda: None
    manager.services = SimpleNamespace(status=lambda name: {"running": False})
    manager.gate.enter("web")
    other = MaintenanceGate(manager.control)
    other.enter("qq")
    try:
        with pytest.raises(UpdateError, match="其他 Agent"):
            manager.install(operation["id"], confirm=True)
        assert not manager.gate.active()
    finally:
        other.leave()


def test_frozen_recovery_runs_with_base_python_and_no_application_import(manager):
    from omni.system.updates.common import run

    operation = manager.prepare(manager.check()["candidate"]["id"])
    wait(manager, operation["id"], "ready")
    folder = manager.control / "operations" / operation["id"]
    plan = read_json(folder / "plan.json")
    run([plan["base_python"], plan["runner"], str(folder), "--recover"], cwd=folder)
    assert read_json(folder / "state.json")["phase"] == "rolled_back"
    assert identify(manager.root)["version"] == "0.1.0"


def test_crash_after_migration_recovery_is_repeatable(tmp_path):
    root, memory, operation = transaction_fixture(tmp_path)

    def execute(argv, **kwargs):
        if argv[0] == "fake-uv":
            (root / ".venv").mkdir()

    transaction = Transaction(operation, execute=execute)
    transaction.start_web = lambda **kwargs: {}

    def crash(_):
        with database(memory) as db:
            db.execute("ALTER TABLE original ADD COLUMN migrated")
        raise KeyboardInterrupt("simulated power loss")

    transaction.wait_ready = crash
    with pytest.raises(KeyboardInterrupt):
        transaction.run()
    assert identify(root)["version"] == "0.2.0"
    # 源码恢复被中断时连包入口都可能暂时缺失，冻结执行器仍必须能继续恢复。
    (root / "agent/src/omni/__init__.py").unlink()
    recovered = Transaction(operation, execute=execute)
    recovered.run(recover=True)
    recovered.run(recover=True)
    assert identify(root)["version"] == "0.1.0"
    with database(memory) as db:
        assert len(db.execute("PRAGMA table_info(original)").fetchall()) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_git_fast_forward_and_recovery_preserve_crlf_and_head(tmp_path, failure):
    from omni.system.updates.common import git, run

    root, memory, operation = transaction_fixture(tmp_path)
    git(root, "init")
    for key, value in [
        ("user.name", "Update Test"),
        ("user.email", "update@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.autocrlf", "true"),
    ]:
        git(root, "config", key, value)
    (root / ".gitignore").write_text(".venv/\nomni-build.json\n")
    git(root, "remote", "add", "origin", "https://gitee.com/chenfengloveyuri/OmniOperator.git")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    old = git(root, "rev-parse", "HEAD")
    old_files = inventory(root)
    source(root, "0.2.0", NEW)
    git(root, "add", ".")
    git(root, "commit", "-m", "target")
    new = git(root, "rev-parse", "HEAD")
    archive = tmp_path / "git-source.zip"
    git(root, "archive", "--format=zip", "--output=" + str(archive), new)
    stage = tmp_path / "git-stage"
    receipt = extract(archive, stage, new)
    git(root, "reset", "--hard", old)
    # baseline 快照必须记录当前 checkout 的实际换行字节。
    old_files = inventory(root)
    plan = read_json(operation / "plan.json")
    plan.update(installation="git", baseline_commit=old, baseline_files=old_files, stage=str(stage), receipt=receipt)
    write_json(operation / "plan.json", plan)

    def execute(argv, **kwargs):
        if argv[0] == "git":
            if "fetch" in argv:
                return ""  # 两个固定对象已在隔离仓库中；禁止网络。
            return run(argv, **kwargs)
        if argv[0] == "fake-uv":
            (root / ".venv").mkdir()
            if failure:
                raise UpdateError("sync_failed", "dependency failure")
        return ""

    transaction = Transaction(operation, execute=execute)
    transaction.start_web = lambda **kwargs: {}
    transaction.wait_ready = lambda child: None
    transaction.run()
    assert transaction.state["phase"] == ("rolled_back" if failure else "succeeded"), transaction.state
    assert git(root, "rev-parse", "HEAD") == (old if failure else new)
    assert not git(root, "status", "--porcelain")
    if failure:
        assert inventory(root) == old_files
    else:
        verify(root, receipt)


def test_idle_timeout_releases_maintenance_without_changing_source(manager):
    operation = manager.prepare(manager.check()["candidate"]["id"])
    wait(manager, operation["id"], "ready")
    manager.gate.begin({"operation": operation["id"], "phase": "draining"})
    manager.drain_timeout = 0
    manager.busy_count = lambda: 1
    folder = manager.control / "operations" / operation["id"]
    manager._drain(folder, read_json(folder / "plan.json"))
    assert manager.operation(operation["id"])["phase"] == "cancelled"
    assert not manager.gate.active() and identify(manager.root)["version"] == "0.1.0"


def test_upgrade_control_paths_are_private_and_cannot_be_public(tmp_path):
    from omni.config import RuntimePaths
    from omni.system.agent_spaces.inventory import private_paths, storage_inventory
    from omni.system.agent_spaces.layout import AgentProfile, resolve_layout

    paths = RuntimePaths.create(
        tmp_path / "data", config_path=tmp_path / "config/config.yaml", skills_root=tmp_path / "skills"
    )
    assert paths.updates in private_paths(paths)
    assert any(row["id"] == "updates" and row["category"] == "固定控制区" for row in storage_inventory(paths))
    profile = AgentProfile(id="default", name="默认", directories={"files": str(paths.updates)})
    with pytest.raises(ValueError, match="固定控制"):
        resolve_layout(profile, paths, catalog=tmp_path / "config/agents.json", isolated=True)


def test_interrupted_preparation_can_be_prepared_again(manager, monkeypatch):
    check = manager.check()
    identifier = "e" * 32
    write_json(
        manager.control / "operations" / identifier / "state.json",
        {
            "id": identifier,
            "phase": "preparing",
            "owner": {"pid": 99999999, "created": "old", "executable": "old.exe"},
            "candidate_id": check["candidate"]["id"],
        },
    )
    write_json(manager.control / "last-operation.json", {"id": identifier})
    assert manager.operation(identifier)["error_code"] == "preparation_interrupted"
    operation = manager.prepare(check["candidate"]["id"])
    wait(manager, operation["id"], "ready")
    assert operation["id"] != identifier


def test_rehearsal_migrates_only_copies_and_preserves_original_memory(tmp_path):
    from omni.channels.update_probe import rehearse
    from omni.memory.store import MemoryStore
    from omni.memory.state import StateStore

    memory = tmp_path / "memory.db"
    state = tmp_path / "state.db"
    store = MemoryStore(memory)
    store.save_fact("原始记忆必须保留")
    store.close()
    database_state = StateStore(state)
    database_state.close()
    before = {path.name: digest(path) for path in (state, memory)}
    plan = tmp_path / "operation/plan.json"
    write_json(plan, {"agents": [{"state": str(state), "memory": str(memory)}]})
    rehearse(plan)
    assert {path.name: digest(path) for path in (state, memory)} == before
    with database(plan.parent / "rehearsal/0/memory.db") as copy:
        assert copy.execute("SELECT content FROM facts").fetchone()[0] == "原始记忆必须保留"


def test_release_builder_includes_unicode_paths_and_checksum(tmp_path):
    import importlib.util
    from omni.system.updates.common import git

    root = tmp_path / "source"
    source(root)
    extra = root / "agent/src/omni/中文模块.py"
    extra.write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".gitignore").write_text("omni-build.json\n.env\n")
    git(root, "init")
    for key, value in [
        ("user.name", "Update Test"),
        ("user.email", "update@example.invalid"),
        ("commit.gpgsign", "false"),
    ]:
        git(root, "config", key, value)
    git(root, "remote", "add", "origin", "https://gitee.com/chenfengloveyuri/OmniOperator.git")
    git(root, "add", ".")
    git(root, "commit", "-m", "release fixture")
    (root / ".env").write_text("PRIVATE=do-not-package")
    script = Path(__file__).resolve().parents[1] / "scripts/build_release.py"
    spec = importlib.util.spec_from_file_location("release_builder", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = module.build(root, tmp_path / "dist")
    with zipfile.ZipFile(output) as archive:
        receipt = json.loads(archive.read("omni-build.json"))
        assert "agent/src/omni/中文模块.py" in receipt["files"]
        assert ".env" not in archive.namelist()
        assert receipt["commit"] == git(root, "rev-parse", "HEAD")
    assert output.with_suffix(".zip.sha256").read_text().split()[0] == digest(output)


def test_unpublished_git_history_detects_ahead_and_same_version_updates(manager):
    from omni.system.updates.common import git

    root = manager.root
    git(root, "init")
    for key, value in [
        ("user.name", "Update Test"),
        ("user.email", "update@example.invalid"),
        ("commit.gpgsign", "false"),
    ]:
        git(root, "config", key, value)
    (root / ".gitignore").write_text("omni-build.json\n")
    git(root, "remote", "add", "origin", "https://gitee.com/chenfengloveyuri/OmniOperator.git")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    old = git(root, "rev-parse", "HEAD")
    (root / "README.md").write_text("new development commit")
    git(root, "add", ".")
    git(root, "commit", "-m", "same version development")
    new = git(root, "rev-parse", "HEAD")
    target = [old]
    manager.client.candidate = lambda channel: {
        "version": "0.1.0",
        "commit": target[0],
        "channel": channel,
        "notes": "",
    }
    manager.client.relation = lambda current, target: "unknown"
    assert manager.check()["status"] == "ahead"
    git(root, "reset", "--hard", old)
    target[0] = new
    assert manager.check()["status"] == "available"


def test_frozen_worker_rolls_back_failed_install_in_a_real_child(tmp_path):
    import sys
    from omni.system.updates.common import run

    root, memory, operation = transaction_fixture(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    manager = UpdateManager(root=root, control=operation.parents[1], config_path=config, enabled=False)
    plan = read_json(operation / "plan.json")
    manager._freeze_runner(operation, plan)
    # 基础 Python 不是 uv：真实子进程会在环境替换后确定失败，随后必须独立恢复。
    plan["uv"] = str(Path(sys._base_executable).resolve())
    write_json(operation / "plan.json", plan)
    try:
        run([plan["base_python"], plan["runner"], str(operation)], cwd=operation, timeout=60)
        assert read_json(operation / "state.json")["phase"] == "rolled_back"
        assert identify(root)["version"] == "0.1.0" and (root / ".venv/old-marker").exists()
        with database(memory) as db:
            assert db.execute("SELECT value FROM original").fetchone()[0] == 42
    finally:
        manager.close()
