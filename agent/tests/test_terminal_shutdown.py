"""Terminal exit confirmation and retained capacity with injected PTY and monotonic clock."""

from __future__ import annotations

import pytest

from omni.channels.terminal import TerminalManager, TerminalSession
from omni.channels.terminal import session as session_module


class Clock:
    def __init__(self) -> None:
        self.now = 10.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert 0 < seconds <= 0.05
        self.sleeps.append(seconds)
        self.now += seconds


class Pty:
    def __init__(
        self, clock: Clock, *, exit_after: float | None = 0.25, terminate_cost: float = 0.2, fault: str = ""
    ) -> None:
        self.clock = clock
        self.exit_after = exit_after
        self.terminate_cost = terminate_cost
        self.fault = fault
        self.exit_at: float | None = None
        self.terminate_calls = 0
        self.close_calls = 0

    def isalive(self) -> bool:
        if self.fault == "isalive":
            raise OSError("cannot inspect owned PTY")
        return self.exit_at is None or self.clock.now < self.exit_at

    def terminate(self, *, force: bool) -> bool:
        assert force
        self.terminate_calls += 1
        if self.fault == "terminate":
            raise OSError("cannot terminate owned PTY")
        self.clock.now += self.terminate_cost
        if self.exit_after is not None:
            self.exit_at = self.clock.now + self.exit_after
        # Sending termination is not proof that the process has exited.
        return False

    def close(self, *, force: bool) -> None:
        assert force and not self.isalive(), "Do not close the handle of a live process"
        self.close_calls += 1
        if self.fault == "close":
            raise OSError("cannot close owned PTY")


@pytest.fixture
def owned_pty(monkeypatch):
    clock = Clock()
    pty = Pty(clock)
    monkeypatch.setattr(session_module, "time", clock)
    monkeypatch.setattr(session_module.backend, "open_process", lambda *args, **kwargs: pty)
    return clock, pty


def test_delayed_exit_is_confirmed_before_close_and_success(owned_pty):
    clock, pty = owned_pty
    terminal = TerminalSession()
    started = clock.now

    assert terminal.kill()

    assert not terminal.isalive()
    assert pty.terminate_calls == 1 and pty.close_calls == 1
    assert clock.sleeps
    assert 0.45 <= clock.now - started < 2


def test_permanently_alive_pty_times_out_without_closing_or_releasing_capacity(owned_pty):
    clock, pty = owned_pty
    pty.exit_after = None
    manager = TerminalManager(max_sessions=1)
    terminal = manager.open()
    started = clock.now

    assert not manager.close(terminal)

    assert clock.now - started == pytest.approx(2)
    assert terminal.isalive() and pty.close_calls == 0
    assert manager.snapshot()["active"] == 1
    with pytest.raises(RuntimeError, match="上限"):
        manager.open()

    # A later confirmed exit allows a retry to reclaim the original session.
    pty.exit_at = clock.now
    assert manager.close(terminal)
    assert manager.snapshot()["active"] == 0
    assert pty.terminate_calls == 1 and pty.close_calls == 1


def test_terminate_time_is_included_in_total_exit_budget(owned_pty):
    clock, pty = owned_pty
    pty.terminate_cost = 0.9
    pty.exit_after = 1.4
    terminal = TerminalSession()
    started = clock.now

    assert not terminal.kill()

    assert clock.now - started == pytest.approx(2)
    assert terminal.isalive() and pty.close_calls == 0


@pytest.mark.parametrize("fault", ["isalive", "terminate", "close"])
def test_backend_failure_keeps_manager_ownership_and_allows_retry(owned_pty, fault):
    _, pty = owned_pty
    pty.fault = fault
    manager = TerminalManager(max_sessions=1)
    terminal = manager.open()

    assert not manager.close(terminal)
    assert manager.snapshot()["active"] == 1
    with pytest.raises(RuntimeError, match="上限"):
        manager.open()

    pty.fault = ""
    assert manager.close(terminal)
    assert manager.snapshot()["active"] == 0


def test_repeated_kill_does_not_terminate_or_wait_again_after_exit(owned_pty):
    clock, pty = owned_pty
    terminal = TerminalSession()

    assert terminal.kill()
    stopped = clock.now
    sleeps = list(clock.sleeps)

    assert terminal.kill()
    assert not terminal.isalive()
    assert pty.terminate_calls == 1
    assert clock.now == stopped and clock.sleeps == sleeps


def test_already_exited_pty_is_closed_without_termination_or_wait(owned_pty):
    clock, pty = owned_pty
    pty.exit_at = clock.now
    terminal = TerminalSession()

    assert terminal.kill()
    assert pty.terminate_calls == 0 and pty.close_calls == 1
    assert not clock.sleeps
