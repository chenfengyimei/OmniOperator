"""Crash recovery keeps test identities and supplies the worker's required lookahead."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from crash_safe_scheduler import CrashSafeLoadFileScheduling
from xdist.workermanage import WorkerController


class Config:
    def __init__(self, distribution: str = "loadfile") -> None:
        self.distribution = distribution

    def getvalue(self, name: str) -> list[str]:
        assert name == "tx"
        return ["popen", "popen"]

    def getoption(self, name: str) -> str:
        assert name == "dist"
        return self.distribution


class Node:
    def __init__(self, identity: str) -> None:
        self.gateway = SimpleNamespace(id=identity)
        self.shutting_down = False
        self.sent: list[int] = []
        self.queued: list[int] = []

    def send_runtest_some(self, indices: list[int]) -> None:
        self.sent.extend(indices)
        self.queued.extend(indices)

    def shutdown(self) -> None:
        self.shutting_down = True


def make_scheduler(collection: list[str], *nodes: Node) -> CrashSafeLoadFileScheduling:
    scheduler = CrashSafeLoadFileScheduling(cast(pytest.Config, Config()))
    scheduler.collection = collection
    for node in nodes:
        worker = cast(WorkerController, node)
        scheduler.assigned_work[worker] = {}
        scheduler.registered_collections[worker] = collection
    return scheduler


def pending(scheduler: CrashSafeLoadFileScheduling) -> set[str]:
    return {item for unit in scheduler.workqueue.values() for item, done in unit.items() if not done} | {
        item
        for workload in scheduler.assigned_work.values()
        for unit in workload.values()
        for item, done in unit.items()
        if not done
    }


def test_completed_scopes_cannot_starve_an_unfinished_file() -> None:
    node = Node("live")
    collection = ["done.py::test_done", "queued.py::test_one", "queued.py::test_two", "active.py::test_last"]
    scheduler = make_scheduler(collection, node)
    scheduler.assigned_work[cast(WorkerController, node)] = {"active.py": {collection[3]: False}}
    scheduler.workqueue.update(
        {"done.py": {collection[0]: True}, "queued.py": {collection[1]: False, collection[2]: False}}
    )
    before = pending(scheduler)
    scheduler._reschedule(cast(WorkerController, node))
    assert node.sent == [1, 2]
    assert node.shutting_down
    assert pending(scheduler) == before


def test_completed_only_queue_releases_the_last_current_test() -> None:
    node = Node("live")
    collection = ["done.py::test_done", "active.py::test_last"]
    scheduler = make_scheduler(collection, node)
    scheduler.assigned_work[cast(WorkerController, node)] = {"active.py": {collection[1]: False}}
    scheduler.workqueue["done.py"] = {collection[0]: True}
    scheduler._reschedule(cast(WorkerController, node))
    assert node.shutting_down and not node.sent
    assert pending(scheduler) == {collection[1]}


@pytest.mark.parametrize("file_count", [1, 2, 4])
def test_replacement_prefetches_single_test_files_or_receives_shutdown(file_count: int) -> None:
    node = Node("replacement")
    collection = [f"file_{i}.py::test_case" for i in range(file_count)]
    scheduler = make_scheduler(collection, node)
    scheduler.workqueue.update({item.split("::")[0]: {item: False} for item in collection})
    scheduler._reschedule(cast(WorkerController, node))
    assert len(node.sent) >= 2 or node.shutting_down
    assert pending(scheduler) == set(collection)
    assert len(node.sent) == len(set(node.sent))


def test_crash_requeues_unfinished_tests_without_replaying_completed_scopes() -> None:
    dead, live = Node("dead"), Node("live")
    collection = ["done.py::test_done", "crash.py::test_current", "crash.py::test_next", "live.py::test_last"]
    scheduler = make_scheduler(collection, dead, live)
    scheduler.assigned_work[cast(WorkerController, dead)] = {
        "done.py": {collection[0]: True},
        "crash.py": {collection[1]: False, collection[2]: False},
    }
    scheduler.assigned_work[cast(WorkerController, live)] = {"live.py": {collection[3]: False}}
    before = pending(scheduler)
    assert scheduler.remove_node(cast(WorkerController, dead)) == collection[1]
    assert live.sent == [1, 2]
    assert pending(scheduler) == before
    assert "crash.py" in scheduler.assigned_work[cast(WorkerController, live)]


def test_shutting_down_node_never_receives_more_work() -> None:
    node = Node("stopping")
    node.shutting_down = True
    collection = ["pending.py::test_case"]
    scheduler = make_scheduler(collection, node)
    scheduler.workqueue["pending.py"] = {collection[0]: False}
    scheduler._reschedule(cast(WorkerController, node))
    assert not node.sent and pending(scheduler) == set(collection)


def test_both_idle_workers_finish_all_pending_tests_after_completed_scopes_are_requeued() -> None:
    nodes = [Node("one"), Node("two")]
    collection = [
        "old.py::test_done",
        "one.py::test_current",
        "two.py::test_current",
        "a.py::test_first",
        "a.py::test_second",
        "b.py::test_only",
    ]
    scheduler = make_scheduler(collection, *nodes)
    for i, node in enumerate(nodes, start=1):
        scheduler.assigned_work[cast(WorkerController, node)] = {collection[i].split("::")[0]: {collection[i]: False}}
        node.queued = [i]
    scheduler.workqueue.update(
        {
            "old.py": {collection[0]: True},
            "a.py": {collection[3]: False, collection[4]: False},
            "b.py": {collection[5]: False},
        }
    )
    expected = pending(scheduler)
    for node in nodes:
        scheduler._reschedule(cast(WorkerController, node))
    executed: list[str] = []
    while any(node.queued for node in nodes):
        progressed = False
        for node in nodes:
            if node.queued and (len(node.queued) > 1 or node.shutting_down):
                index = node.queued.pop(0)
                executed.append(collection[index])
                scheduler.mark_test_complete(cast(WorkerController, node), index)
                progressed = True
        assert progressed, "Workers must receive lookahead or shutdown before waiting for a completion event"
    assert set(executed) == expected and len(executed) == len(expected)
    assert not pending(scheduler)
    assert sum("a.py" in workload for workload in scheduler.assigned_work.values()) == 1


def test_hook_respects_an_explicit_distribution_choice() -> None:
    from conftest import pytest_xdist_make_scheduler

    for distribution in ("load", "loadscope", "worksteal", "no"):
        assert pytest_xdist_make_scheduler(cast(pytest.Config, Config(distribution)), None) is None
    assert isinstance(pytest_xdist_make_scheduler(cast(pytest.Config, Config()), None), CrashSafeLoadFileScheduling)
