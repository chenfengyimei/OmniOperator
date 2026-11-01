"""服务汇总的聚合与告警判定（纯函数，不碰任何进程）。

前端只有两个消费者：左下角那行状态和顶部告警条。判错的代价是"以为在跑其实挂了"，
所以每条告警的触发条件都要钉死。
"""
from omni.system.service_overview import STARTUP_GRACE_S, service_summary

NOW = 10_000.0


class FakeServices:
    def __init__(self, rows, napcat=None):
        self._rows = rows
        self._napcat = napcat or {"installed": True, "logged_in": True, "uin": "123456"}

    def status(self, name):
        return self._rows[name]

    def napcat_status(self):
        return self._napcat


def _entries(rows):
    """把假行里"守护侧"的字段摊成 snapshot 的 services 记账。"""
    return {
        name: {
            "desired": row["desired"], "skipped": row["skipped"], "attempts": row["attempts"],
            "restarts_total": row["restarts"], "running_since": row["running_since"],
            "last_exit": row["last_exit"], "next_retry_at": row["next_retry_at"],
            "last_error": row["last_error"], "alert": row["supervisor_alert"],
        }
        for name, row in rows.items()
    }


class FakeSupervisor:
    def __init__(self, rows, *, mode="active", holder="", policy=None):
        self._services = _entries(rows)
        self._mode = mode
        self._holder = holder
        self._policy = policy

    def snapshot(self):
        return {"supervised": self._mode == "active", "mode": self._mode,
                "holder": self._holder, "policy": self._policy, "last_error": "",
                "services": self._services}


def _row(name, *, running=True, fresh=True, reason="ok", desired="running", state="verified",
         stats=None, running_since=NOW - 3600, alert=None, label=None):
    return {
        "name": name, "label": label or name, "running": running, "pid": 100 if running else None,
        "detail": "", "identity_verified": running, "can_stop": running, "state": state,
        "stats": stats or {}, "fresh": fresh, "health_reason": reason,
        "state_age_s": 1.0 if fresh else 120.0, "health": "ok" if fresh else "", "note": "",
        "desired": desired, "skipped": "", "attempts": 0, "restarts": 0,
        "running_since": running_since, "last_exit": {}, "next_retry_at": 0.0,
        "last_error": "", "supervisor_alert": alert,
    }


def _healthy():
    return {
        "omni-heartbeat": _row("omni-heartbeat", stats={"ticks": 12}),
        "omni-qq": _row("omni-qq", stats={"connected": True, "received": 3}),
        "napcat": _row("napcat", fresh=False, reason="not_applicable"),
    }


def _summary(rows, *, mode="active", holder="", napcat=None):
    supervisor = FakeSupervisor(rows, mode=mode, holder=holder)
    return service_summary(FakeServices(rows, napcat), supervisor, now=NOW)


def _codes(result):
    return [alert["code"] for alert in result["alerts"]]


def test_all_green_has_no_alerts():
    result = _summary(_healthy())
    assert result["ok"] is True and result["alerts"] == []
    assert result["counts"] == {"online": 3, "total": 3}


def test_heartbeat_stale_is_warned():
    rows = _healthy()
    rows["omni-heartbeat"] = _row("omni-heartbeat", fresh=False, reason="stale")
    result = _summary(rows)
    assert _codes(result) == ["heartbeat_stale"]
    assert result["ok"] is True  # 警告不算故障，但界面要显眼


def test_heartbeat_within_startup_grace_is_quiet():
    """刚拉起来还没写第一份快照，不该立刻报"卡住了"。"""
    rows = _healthy()
    rows["omni-heartbeat"] = _row("omni-heartbeat", fresh=False, reason="missing",
                                  running_since=NOW - STARTUP_GRACE_S / 2)
    assert _summary(rows)["alerts"] == []


def test_qq_disconnected_is_warned():
    rows = _healthy()
    rows["omni-qq"] = _row("omni-qq", stats={"connected": False})
    assert _codes(_summary(rows)) == ["qq_disconnected"]


def test_service_exited_is_error():
    rows = _healthy()
    rows["omni-heartbeat"] = _row("omni-heartbeat", running=False, fresh=False)
    result = _summary(rows)
    assert _codes(result) == ["service_exited"] and result["ok"] is False


def test_skipped_service_is_not_an_alert():
    """qq.enabled=false 属于"本来就不该起"，报成故障只会制造噪音。"""
    rows = _healthy()
    rows["omni-qq"] = {**_row("omni-qq", running=False, fresh=False), "skipped": "qq.enabled=false"}
    assert _summary(rows)["alerts"] == []


def test_blocked_unverified_is_error_with_cleanup_action():
    rows = _healthy()
    rows["omni-qq"] = _row("omni-qq", running=False, fresh=False, state="blocked_unverified")
    alerts = _summary(rows)["alerts"]
    assert [a["code"] for a in alerts] == ["blocked_unverified"]
    assert alerts[0]["level"] == "error" and alerts[0]["action"] == "cleanup"


def test_restart_given_up_is_error_and_replaces_exited():
    rows = _healthy()
    rows["omni-qq"] = _row("omni-qq", running=False, fresh=False,
                           alert={"code": "restart_given_up", "text": "连续 5 次未能启动"})
    alerts = _summary(rows)["alerts"]
    assert [a["code"] for a in alerts] == ["restart_given_up"]


def test_standby_instance_says_so():
    result = _summary(_healthy(), mode="standby", holder="pid:4242")
    assert _codes(result) == ["supervisor_standby"]
    assert "pid:4242" in result["alerts"][0]["text"]


def test_napcat_not_installed_says_nothing():
    """没装 NapCat 时提醒"未登录"是噪音：用户根本没这个东西。"""
    napcat = {"installed": False, "logged_in": False, "uin": None}
    rows = _healthy()
    rows["napcat"] = _row("napcat", running=False, fresh=False, reason="not_applicable",
                          desired="stopped")
    assert _summary(rows, napcat=napcat)["alerts"] == []


def test_napcat_needs_scan_is_info():
    napcat = {"installed": True, "logged_in": False, "uin": None}
    alerts = _summary(_healthy(), napcat=napcat)["alerts"]
    assert [a["code"] for a in alerts] == ["napcat_not_logged_in"]
    assert alerts[0]["action"] == "qr"


def test_summary_works_without_supervisor():
    """只读模式（守护没装配）也要能出汇总，不能 500。"""
    result = service_summary(FakeServices(_healthy()), None, now=NOW)
    assert result["supervisor"]["mode"] == "unknown"
    assert result["counts"]["online"] == 3
