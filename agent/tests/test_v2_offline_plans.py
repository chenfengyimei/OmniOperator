"""Shared maintenance infrastructure owns bounded publication and actor lifetime, independent of target domain."""

import copy
import json
import os
import stat
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from omni.adapters.persistence import offline_plans as plans
from omni.adapters.platform.runtime.paths import InstanceLock, InstancePaths
from omni.domain.contracts import BusinessError


def fixture_plan():
    return {"format": "fixture-reference-plan", "id": str(uuid.uuid4()), "targets": [{"object_id": str(uuid.uuid4())}]}


def unsupported(*args):
    raise AssertionError("This operation must not be invoked")


def operations(plan):
    def validate(value, now, allow_future):
        assert value == plan and now
        return "a" * 64

    return plans.PlanOperations(
        validate=validate,
        make=lambda unit, after, limit: copy.deepcopy(plan),
        validate_current=unsupported,
        get_batch=lambda unit, identity: None,
        prepare=unsupported,
        apply_target=unsupported,
        complete=unsupported,
        cancel=unsupported,
    )


def read_without_database(path, clock, ids, query):
    return {**query(object()), "verification": {"integrity_ok": True, "foreign_key_errors": 0}}


def test_domain_independent_plan_publication_holds_ownership_and_private_permissions(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    plan = fixture_plan()
    observed = []

    def reader(path, clock, ids, query):
        assert path == paths.database
        contender = InstanceLock(paths)
        with pytest.raises(BusinessError) as error:
            contender.acquire()
        assert error.value.code == "instance_busy"
        observed.append(True)
        return read_without_database(path, clock, ids, query)

    monkeypatch.setattr(plans, "offline_read", reader)
    output = tmp_path / "review.json"
    report = plans.create_plan(paths, output, operations(plan))
    assert observed == [True] and report["plan_id"] == plan["id"] and not report["applied"]
    assert json.loads(output.read_text(encoding="utf-8")) == plan
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert plans.read_plan(output, operations(plan)) == plan
    lock = InstanceLock(paths)
    lock.acquire()
    lock.close()


def test_generated_oversize_plan_is_refused_before_publication(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    plan = fixture_plan()
    plan["targets"][0]["metadata"] = "x" * plans.MAX_PLAN_BYTES
    monkeypatch.setattr(plans, "offline_read", read_without_database)
    output = tmp_path / "oversize.json"
    with pytest.raises(BusinessError) as error:
        plans.create_plan(paths, output, operations(plan))
    assert error.value.code == "invalid_retention_plan" and not output.exists()
    assert not list(tmp_path.glob("*.partial"))
    lock = InstanceLock(paths)
    lock.acquire()
    lock.close()


@pytest.mark.parametrize(
    "content",
    [b"{", b"\xff", b"[" * 2000 + b"]" * 2000, b"x" * (plans.MAX_PLAN_BYTES + 1)],
    ids=["incomplete", "invalid_utf8", "nested_non_object", "oversize"],
)
def test_plan_reader_bounds_untrusted_bytes_and_reports_invalid_json(tmp_path, content):
    path = tmp_path / "untrusted.json"
    path.write_bytes(content)
    with pytest.raises(BusinessError) as error:
        plans.read_plan(path, operations(fixture_plan()))
    assert error.value.code == "invalid_retention_plan" and error.value.status == 422


def test_plan_reader_passes_clock_rollback_option_to_domain_validator(tmp_path):
    plan = fixture_plan()
    observed = []

    def validate(value, now, allow_future):
        observed.append(allow_future)
        if not allow_future:
            raise BusinessError("invalid_retention_plan", "Clock precedes the persisted plan", status=422)
        return "a" * 64

    ops = replace(operations(plan), validate=validate)
    path = tmp_path / "future.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(BusinessError):
        plans.read_plan(path, ops)
    assert plans.read_plan(path, ops, allow_future=True) == plan
    assert observed == [False, True]


def test_private_file_cleanup_failure_always_releases_instance_lock(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    plan = fixture_plan()
    monkeypatch.setattr(plans, "offline_read", read_without_database)
    original = Path.unlink

    def deny_permissions(*args, **kwargs):
        raise PermissionError("Cannot establish private permissions")

    def deny_cleanup(path, *args, **kwargs):
        if path.suffix == ".partial":
            raise PermissionError("Cannot remove the partial file")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(plans, "private_path", deny_permissions)
        patch.setattr(Path, "unlink", deny_cleanup)
        with pytest.raises(PermissionError, match="partial"):
            plans.create_plan(paths, tmp_path / "failed.json", operations(plan))
    lock = InstanceLock(paths)
    lock.acquire()
    lock.close()
    assert not (tmp_path / "failed.json").exists()


def test_cancellation_uses_one_actor_without_checking_current_target_eligibility(tmp_path, monkeypatch):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    plan = fixture_plan()
    observed = []
    actors = []
    base = operations(plan)

    def validate(value, now, allow_future):
        assert allow_future
        observed.append("validate")
        return "a" * 64

    def cancel(unit, value, reason):
        assert value == plan and reason == "New provenance protects this target"
        observed.append("cancel")
        return {"cancelled": True}

    class Actor:
        def __init__(self, *args, **kwargs):
            actors.append(self)
            self.closed = False

        async def write(self, operation):
            return operation(object())

        async def close(self):
            self.closed = True

    monkeypatch.setattr(plans, "offline_read", read_without_database)
    monkeypatch.setattr(plans, "Database", Actor)
    ops = replace(base, validate=validate, cancel=cancel)
    result = plans.cancel_plan(paths, plan, "New provenance protects this target", ops)
    assert result == {"cancelled": True} and observed == ["validate", "cancel"]
    assert len(actors) == 1 and actors[0].closed
    lock = InstanceLock(paths)
    lock.acquire()
    lock.close()


@pytest.mark.parametrize("failure", [False, True])
def test_apply_uses_domain_callbacks_one_actor_and_closes_after_target_failure(tmp_path, monkeypatch, failure):
    paths = InstancePaths.resolve(tmp_path / "home")
    paths.initialize()
    plan = fixture_plan()
    plan["targets"].append({"object_id": str(uuid.uuid4())})
    backup = tmp_path / "snapshot"
    observed, actors = [], []

    def snapshot(database, objects, destination):
        assert not actors
        destination.mkdir()
        (destination / "manifest.json").write_text("{}", encoding="utf-8")
        observed.append("snapshot")

    def current(unit, value):
        assert value == plan
        observed.append("current")

    def prepare(unit, value, path, sha):
        assert path == str(backup) and len(sha) == 64
        observed.append("prepare")
        return {}

    def target(unit, value, item):
        assert value == plan and item in plan["targets"]
        observed.append(item["object_id"])
        if failure and item == plan["targets"][1]:
            raise BusinessError("fixture_target_protected", "Target became protected")

    def complete(unit, identity):
        assert identity == plan["id"]
        observed.append("complete")
        return {"complete": True}

    class Actor:
        def __init__(self, *args, **kwargs):
            actors.append(self)
            self.closed = False
            self.writes = 0

        async def write(self, operation):
            self.writes += 1
            return operation(object())

        async def close(self):
            self.closed = True

    monkeypatch.setattr(plans, "offline_read", read_without_database)
    monkeypatch.setattr(plans, "create_snapshot", snapshot)
    monkeypatch.setattr(plans, "verify_snapshot", lambda source: {})
    monkeypatch.setattr(plans, "Database", Actor)
    ops = replace(operations(plan), validate_current=current, prepare=prepare, apply_target=target, complete=complete)
    if failure:
        with pytest.raises(BusinessError) as error:
            plans.apply_plan(paths, plan, backup, ops)
        assert error.value.code == "fixture_target_protected"
    else:
        assert plans.apply_plan(paths, plan, backup, ops) == {"complete": True}
    assert observed[:4] == ["current", "snapshot", "current", "prepare"]
    assert observed[4:6] == [target["object_id"] for target in plan["targets"]]
    assert ("complete" in observed) is not failure
    assert len(actors) == 1 and actors[0].closed and actors[0].writes == (3 if failure else 4)
    lock = InstanceLock(paths)
    lock.acquire()
    lock.close()
