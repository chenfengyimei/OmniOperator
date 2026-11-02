import copy
from pathlib import Path

import pytest

from omni.config import Desktop
from omni.core.session import ResourceLeases
from omni.memory.state import StateStore
from omni.safety.gates import AllowAllApprover, DenyApprover
from omni.tools.browser.budget import WriteBudget
from omni.tools.desktop.driver import DesktopDriver
from omni.tools.desktop.native import DesktopError
from omni.tools.desktop.tools import register_desktop_tools
from omni.tools.registry import SessionRef, ToolRegistry


class FakeDesktop:
    def __init__(self, exe):
        self.state = {
            "identity": {"hwnd": 101, "pid": 20, "created": "123", "exe": str(exe), "title": "Fixture"},
            "enabled": True,
            "minimized": False,
            "nodes": [
                {
                    "id": "edit-1",
                    "value": "before",
                    "set_value": True,
                    "invoke": False,
                    "enabled": True,
                    "password": False,
                    "offscreen": False,
                    "truncated": False,
                }
            ],
        }
        self.fingerprint = "first"
        self.actions = []
        self.unknown = False

    def request(self, command, **args):
        if command == "windows":
            return {"windows": [copy.deepcopy(self.state["identity"])]}
        if command == "action":
            self.actions.append(args)
            if self.unknown:
                raise DesktopError("回读失败", unknown=True)
            self.state["nodes"][0]["value"] = args["text"]
            self.fingerprint = "after"
        return {"ok": True, "state": copy.deepcopy(self.state), "fingerprint": self.fingerprint, "verified": True}


@pytest.fixture
def desktop(tmp_path):
    state = StateStore(tmp_path / "state.db")
    exe = tmp_path / "allowed.exe"
    backend = FakeDesktop(exe)
    time = [0.0]
    driver = DesktopDriver(
        Desktop(enabled=True, allowed_apps=[str(exe)]),
        backend,
        ResourceLeases(state),
        WriteBudget(state, max_per_day=2, scope="desktop_write"),
        clock=lambda: time[0],
    )
    yield driver, backend, time, state
    state.close()


def test_background_edit_is_verified_and_observation_single_use(desktop):
    driver, backend, _, _ = desktop
    observation = driver.observe(101, session_id=1)["observation"]
    result = driver.action(observation, "edit-1", "set_value", "after", session_id=1)
    assert result.status == "ok" and result.side_effect_state == "applied"
    assert driver.action(observation, "edit-1", "set_value", "again", session_id=1).status == "error"
    assert len(backend.actions) == 1


@pytest.mark.parametrize("change", ["tree", "identity", "session", "time", "revoked", "minimized", "password"])
def test_stale_or_wrong_target_never_writes(desktop, change):
    driver, backend, time, _ = desktop
    if change in {"minimized", "password"}:
        if change == "minimized":
            backend.state["minimized"] = True
        else:
            backend.state["nodes"][0]["password"] = True
    observation = driver.observe(101, session_id=1)["observation"]
    if change in {"tree", "identity"}:
        backend.fingerprint = "changed"
    if change == "time":
        time[0] = 121
    if change == "revoked":
        driver.config.allowed_apps = []
    result = driver.action(observation, "edit-1", "set_value", "no", session_id=2 if change == "session" else 1)
    assert result.status == "error" and result.side_effect_state == "none"
    assert not backend.actions


def test_write_timeout_is_unknown_and_cannot_replay(desktop):
    driver, backend, _, _ = desktop
    observation = driver.observe(101)["observation"]
    backend.unknown = True
    result = driver.action(observation, "edit-1", "set_value", "text")
    assert result.status == "unknown" and not result.retryable
    assert driver.action(observation, "edit-1", "set_value", "text").status == "error"
    assert len(backend.actions) == 1


def test_desktop_registry_is_human_only_and_budgeted(desktop):
    driver, backend, _, _ = desktop
    registry = ToolRegistry()
    ref = SessionRef()
    ref.set(7)
    register_desktop_tools(registry, driver, session_ref=ref)
    assert registry.get("desktop_action").requires_human
    observation = driver.observe(101, session_id=7)["observation"]
    args = {"observation": observation, "element": "edit-1", "action": "set_value", "text": "value"}
    args.update(window_title="Fixture", application=backend.state["identity"]["exe"])
    assert registry.execute("desktop_action", args, approver=DenyApprover()).status == "denied"
    assert not backend.actions
    for i in range(3):
        args["observation"] = driver.observe(101, session_id=7)["observation"]
        result = registry.execute("desktop_action", args, approver=AllowAllApprover())
        assert result.status == ("ok" if i < 2 else "error")
    assert len(backend.actions) == 2


def test_desktop_lease_contention_prevents_second_writer(desktop):
    driver, backend, _, state = desktop
    observation = driver.observe(101)["observation"]
    lease = state.acquire_lease("desktop:101", "another-process")
    try:
        result = driver.action(observation, "edit-1", "set_value", "blocked")
        assert result.status == "error" and not backend.actions
    finally:
        state.release_lease(lease.token)


def test_disabled_desktop_does_not_start_native_backend(desktop):
    driver, backend, _, _ = desktop
    driver.config.enabled = False
    with pytest.raises(DesktopError, match="未启用"):
        driver.windows()


def test_approval_preview_must_match_observed_application(desktop):
    driver, backend, _, _ = desktop
    observation = driver.observe(101)["observation"]
    result = driver.action(
        observation,
        "edit-1",
        "set_value",
        "content",
        preview={"window_title": "fake harmless app", "application": "different.exe"},
    )
    assert result.status == "error" and not backend.actions


def test_desktop_tools_not_available_in_group_scope():
    from omni.core.agent import GROUP_DENIED_TOOLS

    assert {"desktop_windows", "desktop_observe", "desktop_screenshot", "desktop_action"} <= GROUP_DENIED_TOOLS


def test_foreground_takeover_is_opt_in_and_bound_to_run(desktop):
    driver, backend, _, _ = desktop
    observation = driver.observe(101, session_id=1)["observation"]
    assert (
        driver.action(observation, "edit-1", "set_value", "after", session_id=1, foreground=True, run_id="run").status
        == "error"
    )
    assert not backend.actions
    driver.config.foreground_enabled = True
    assert driver.action(observation, "edit-1", "set_value", "after", session_id=1, foreground=True).status == "error"
    calls = []
    original = backend.request

    def request(command, **arguments):
        if command == "foreground_action":
            calls.append(arguments)
            return original("action", **arguments)
        return original(command, **arguments)

    backend.request = request
    result = driver.action(
        observation, "edit-1", "set_value", "after", session_id=1, foreground=True, run_id="approved-run"
    )
    assert result.status == "ok"
    assert calls[0]["run_id"] == "approved-run"
    assert calls[0]["expected_identity"]["hwnd"] == 101


def test_foreground_contention_rejects_and_input_interruption_is_not_replayed(desktop):
    driver, backend, _, _ = desktop
    driver.config.foreground_enabled = True
    observed = driver.observe(101, session_id=1)["observation"]
    lease = driver.leases.acquire("desktop:foreground", ttl_s=120)
    try:
        assert (
            driver.action(observed, "edit-1", "set_value", "after", session_id=1, foreground=True, run_id="run").status
            == "error"
        )
        assert not backend.actions
    finally:
        driver.leases.release(lease)
    calls = []
    original = backend.request

    def interrupted(command, **arguments):
        if command == "foreground_action":
            calls.append(command)
            raise DesktopError("用户输入，已暂停；效果需人工核查", unknown=True)
        return original(command, **arguments)

    backend.request = interrupted
    result = driver.action(observed, "edit-1", "set_value", "after", session_id=1, foreground=True, run_id="run")
    assert result.status == result.side_effect_state == "unknown"
    assert (
        driver.action(observed, "edit-1", "set_value", "after", session_id=1, foreground=True, run_id="run").status
        == "error"
    )
    assert calls == ["foreground_action"]
    # 中断释放接管租约；后续用户仍可发起新的独立授权。
    fresh = driver.leases.acquire("desktop:foreground", ttl_s=120)
    driver.leases.release(fresh)
