"""服务守护单测：期望状态推导、退避与上限、单实例互斥、隔离实例不动手。

全部用假 ServiceManager：真机上的 NapCat/QQ/心跳一律不碰（离线套件纪律）。
"""
import json
from pathlib import Path

from omni.system.services import ServiceError
from omni.system.supervisor import (
    Policy,
    ServiceSupervisor,
    backoff_delay,
    desired_state,
)


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeServices:
    """只实现守护用到的五个入口；进程动作全部记下来给测试断言。"""

    def __init__(self, *, napcat_dir: Path | None = None, uin: str | None = None) -> None:
        self.napcat_dir = napcat_dir
        self._uin = uin
        self.running: set[str] = set()
        self.start_calls: list[tuple[str, str]] = []
        self.reconcile_calls: list[str] = []
        self.stop_requests: list[str] = []
        self.blocked: set[str] = set()
        self.start_error: Exception | None = None

    def napcat_uin(self) -> str | None:
        return self._uin

    def status(self, name: str) -> dict:
        return {"name": name, "running": name in self.running}

    def reconcile(self, name: str) -> dict:
        self.reconcile_calls.append(name)
        state = "blocked_unverified" if name in self.blocked else "absent"
        return {"name": name, "state": state, "pid": None, "detail": ""}

    def start(self, name: str, qq: str = "") -> dict:
        self.start_calls.append((name, qq))
        if self.start_error is not None:
            raise self.start_error
        self.running.add(name)
        return {"name": name, "started": True, "pid": 111}

    def request_stop(self, name: str, *, wait_s: float | None = None) -> dict:
        self.stop_requests.append(name)
        self.running.discard(name)
        return {"name": name, "requested": True}


def _config(tmp_path: Path, *, autostart: bool = True, qq_enabled: bool = True,
            max_attempts: int = 3, keep: bool = True, napcat_qq: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "qq:\n"
        f"  enabled: {'true' if qq_enabled else 'false'}\n"
        "system:\n"
        f"  autostart: {'true' if autostart else 'false'}\n"
        f"  keep_running_after_exit: {'true' if keep else 'false'}\n"
        f"  restart_max_attempts: {max_attempts}\n"
        f"  napcat_qq: \"{napcat_qq}\"\n",
        encoding="utf-8",
    )
    return path


def _supervisor(tmp_path: Path, services: FakeServices, *, config_path=None,
                enabled: bool = True, clock=None) -> ServiceSupervisor:
    sup = ServiceSupervisor(
        services,
        run_dir=tmp_path / "run",
        config_path=config_path or _config(tmp_path),
        enabled=enabled,
        clock=clock or Clock(),
    )
    sup.start()
    return sup


def test_desired_state_skips_when_qq_disabled():
    """qq.enabled=false 时照着拉 omni-qq 会变成"起-退-起"死循环。"""
    policy = Policy(autostart=True, qq_enabled=False, napcat_available=True)
    assert desired_state("omni-qq", policy) == ("stopped", "qq.enabled=false")
    assert desired_state("omni-heartbeat", policy)[0] == "running"
    assert desired_state("napcat", policy)[0] == "running"


def test_desired_state_stops_when_autostart_off():
    policy = Policy(autostart=False, qq_enabled=True, napcat_available=True)
    for name in ("napcat", "omni-qq", "omni-heartbeat"):
        assert desired_state(name, policy)[0] == "stopped"


def test_desired_state_skips_missing_napcat():
    policy = Policy(autostart=True, qq_enabled=True, napcat_available=False)
    state, skipped = desired_state("napcat", policy)
    assert state == "stopped" and "napcat.bat" in skipped


def test_backoff_grows_and_caps():
    assert backoff_delay(0, 300) == 0.0
    assert backoff_delay(1, 300) == 5.0
    assert backoff_delay(2, 300) == 10.0
    assert backoff_delay(3, 300) == 20.0
    assert backoff_delay(20, 300) == 300.0


def test_starts_everything_once(tmp_path):
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    assert {name for name, _ in services.start_calls} == {"napcat", "omni-qq", "omni-heartbeat"}
    # 第二轮已经都在跑：不能重复拉起
    sup.reconcile_once()
    assert len(services.start_calls) == 3
    snap = sup.snapshot()
    assert snap["supervised"] is True and snap["services"]["omni-qq"]["desired"] == "running"
    sup.stop()


def test_no_respawn_when_record_verified(tmp_path):
    """已在跑（含工作台重启后接管上一个实例）不再 spawn。"""
    services = FakeServices(napcat_dir=tmp_path)
    services.running.update({"napcat", "omni-qq", "omni-heartbeat"})
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    assert services.start_calls == []
    sup.stop()


def test_qq_disabled_never_spawns_it(tmp_path):
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, config_path=_config(tmp_path, qq_enabled=False))
    sup.reconcile_once()
    assert "omni-qq" not in {name for name, _ in services.start_calls}
    assert sup.snapshot()["services"]["omni-qq"]["skipped"] == "qq.enabled=false"
    sup.stop()


def test_backoff_holds_off_retry(tmp_path):
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    services.start_error = ServiceError("spawn 失败")
    sup = _supervisor(tmp_path, services, clock=clock)
    sup.reconcile_once()
    attempts_first = len(services.start_calls)
    assert attempts_first == 3  # 三个服务各试一次
    snap = sup.snapshot()["services"]["omni-qq"]
    assert snap["attempts"] == 1 and snap["next_retry_at"] > clock.t
    # 退避窗口内不重试
    sup.reconcile_once()
    assert len(services.start_calls) == attempts_first
    # 过了窗口才再来一次
    clock.advance(60)
    sup.reconcile_once()
    assert len(services.start_calls) > attempts_first
    sup.stop()


def test_gives_up_after_max_attempts_keeps_alert(tmp_path):
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    services.start_error = ServiceError("spawn 失败")
    sup = _supervisor(tmp_path, services, config_path=_config(tmp_path, max_attempts=2), clock=clock)
    for _ in range(6):
        sup.reconcile_once()
        clock.advance(600)
    entry = sup.snapshot()["services"]["omni-heartbeat"]
    assert entry["attempts"] == 2, "达到上限后必须停止累加，不能无限重试"
    alert = entry["alert"]
    assert alert and alert["code"] == "restart_given_up"
    sup.stop()


def test_attempts_reset_after_stable_run(tmp_path):
    """稳定跑满 5 分钟才算恢复：刚起来就崩不该被当成恢复正常。"""
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, clock=clock)
    sup.reconcile_once()
    entry = sup._entries["omni-qq"]
    entry.attempts = 2  # 模拟历史上失败过两次，但现在在跑
    sup.reconcile_once()
    assert entry.attempts == 2, "刚起来还不算稳"
    clock.advance(301)
    sup.reconcile_once()
    assert entry.attempts == 0
    sup.stop()


def test_crash_loop_accumulates_attempts_and_backs_off(tmp_path):
    """spawn 成功但进程随即死掉：attempts 必须累加，否则退避与上限都形同虚设。

    真机踩过：守护每 10 秒重起一次，spawn 成功就把 attempts 清零，
    于是 restart_max_attempts=5 永远达不到、restart_given_up 永不出现。
    """
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, config_path=_config(tmp_path, max_attempts=3), clock=clock)
    sup.reconcile_once()
    entry = sup.snapshot()["services"]["omni-qq"]
    assert entry["attempts"] == 1, "起一次算一次尝试，不该立刻清零"
    assert len(services.start_calls) == 3

    # 进程随即死了：下一轮看到没在跑 → 先退避，不立刻重试
    services.running.clear()
    clock.advance(10)
    sup.reconcile_once()
    assert len(services.start_calls) == 3, "退避窗口内不该再拉起"
    entry = sup.snapshot()["services"]["omni-qq"]
    assert entry["attempts"] == 1 and entry["next_retry_at"] > clock.t
    assert entry["alert"]["code"] == "restart_backoff"

    # 窗口过了才重试，attempts 继续累加
    clock.advance(60)
    sup.reconcile_once()
    assert len(services.start_calls) == 6
    assert sup.snapshot()["services"]["omni-qq"]["attempts"] == 2
    sup.stop()


def test_crash_loop_eventually_gives_up(tmp_path):
    """连续起不来 → 达到上限后停止重试，并给出 restart_given_up 告警。"""
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, config_path=_config(tmp_path, max_attempts=2), clock=clock)
    for _ in range(6):
        sup.reconcile_once()
        services.running.clear()  # 每次起来就死
        clock.advance(60)
    entry = sup.snapshot()["services"]["omni-qq"]
    assert entry["attempts"] == 2, "达到上限后必须停止累加，不能无限重试"
    assert entry["alert"]["code"] == "restart_given_up"
    before = len(services.start_calls)
    clock.advance(600)
    sup.reconcile_once()
    assert len(services.start_calls) == before, "放弃之后不许再拉起"
    sup.stop()


def test_healthy_service_keeps_attempts_at_zero(tmp_path):
    """服务一直健康：attempts 不会累加，退避与告警都不该出现。"""
    clock = Clock()
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, clock=clock)
    for _ in range(4):
        sup.reconcile_once()
        clock.advance(10)
    entry = sup.snapshot()["services"]["omni-qq"]
    assert entry["attempts"] == 1, "只起过一次；稳定后清零"
    clock.advance(301)
    sup.reconcile_once()
    entry = sup.snapshot()["services"]["omni-qq"]
    assert entry["attempts"] == 0 and entry["alert"] is None
    sup.stop()


def test_blocked_unverified_is_skipped_not_counted(tmp_path):
    """旧的不可核验进程占着：不拉起、不计失败、不给重启告警。"""
    services = FakeServices(napcat_dir=tmp_path)
    services.blocked.add("omni-qq")
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    assert "omni-qq" not in {name for name, _ in services.start_calls}
    entry = sup.snapshot()["services"]["omni-qq"]
    assert "不可核验" in entry["skipped"] and entry["attempts"] == 0
    sup.stop()


def test_second_instance_goes_standby_and_touches_nothing(tmp_path):
    services = FakeServices(napcat_dir=tmp_path)
    first = _supervisor(tmp_path, services)
    second = ServiceSupervisor(services, run_dir=tmp_path / "run",
                               config_path=_config(tmp_path), clock=Clock())
    result = second.start()
    assert result["mode"] == "standby" and result["supervised"] is False
    before = len(services.start_calls)
    second.reconcile_once()
    assert len(services.start_calls) == before, "standby 只读，不许动手"
    first.stop()
    # 锁释放后第二个实例可以接替守护（工作台重启的正常路径）
    assert second.start()["mode"] == "active"
    second.stop()


def test_disabled_supervisor_never_touches_services(tmp_path):
    """隔离实例（注入 data_dir）绝不能拉起真机服务。"""
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, enabled=False)
    assert sup.start()["mode"] == "disabled"
    sup.reconcile_once()
    assert services.start_calls == []
    assert sup.snapshot()["supervised"] is False


def test_unreadable_config_freezes_round(tmp_path):
    """配置读到一半/写坏时本轮什么都不做，但守护不能退出。"""
    services = FakeServices(napcat_dir=tmp_path)
    config_path = _config(tmp_path)
    sup = _supervisor(tmp_path, services, config_path=config_path)
    sup.reconcile_once()
    assert len(services.start_calls) == 3
    services.running.clear()
    config_path.write_text("qq: [", encoding="utf-8")  # 语法错
    sup.reconcile_once()
    assert len(services.start_calls) == 3, "配置不可读时不许拿默认值去起服务"
    assert "配置读取失败" in sup.snapshot()["last_error"]
    sup.stop()


def test_stop_with_stop_services_requests_all(tmp_path):
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    result = sup.stop(stop_services=True, wait_s=1.0)
    assert set(result["requested"]) == {"napcat", "omni-qq", "omni-heartbeat"}
    assert set(services.stop_requests) == {"napcat", "omni-qq", "omni-heartbeat"}
    assert result["mode"] == "idle"


def test_stop_keeps_services_by_default(tmp_path):
    """默认 keep_running_after_exit：关工作台只释放守护权，不打断定时任务。"""
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    result = sup.stop()
    assert result["requested"] == [] and services.stop_requests == []
    assert services.running == {"napcat", "omni-qq", "omni-heartbeat"}


def test_persists_record(tmp_path):
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    sup.stop()  # 先停线程再读，避免和守护轮次抢文件
    payload = json.loads((tmp_path / "run" / "supervisor.json").read_text(encoding="utf-8"))
    assert payload["mode"] == "active"
    assert payload["services"]["omni-heartbeat"]["restarts_total"] == 1
    assert payload["policy"]["autostart"] is True


def test_keep_running_follows_hot_read_config(tmp_path):
    """关工作台要不要连带停服务：取磁盘上最新的设置，不是启动快照。"""
    services = FakeServices(napcat_dir=tmp_path)
    sup = _supervisor(tmp_path, services, config_path=_config(tmp_path, keep=True))
    sup.reconcile_once()
    assert sup.keep_running_after_exit() is True
    _config(tmp_path, keep=False)
    sup.reconcile_once()
    assert sup.keep_running_after_exit() is False
    sup.stop()


def test_napcat_qq_falls_back_to_detected_uin(tmp_path):
    services = FakeServices(napcat_dir=tmp_path, uin="123456")
    sup = _supervisor(tmp_path, services)
    sup.reconcile_once()
    qq = dict(services.start_calls)["napcat"]
    assert qq == "123456"
    sup.stop()
