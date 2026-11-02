"""把服务状态、守护记账与运行态快照拼成前端要的一份汇总。

工作台左下角那行状态、顶部告警条、设置中心「服务与运行」页读的都是这里。
放在 `system/` 而不是路由里：路由只做 HTTP 适配，聚合规则属于平台内部知识。

职责：纯聚合 + 告警判定，结果只读。
明确不做：不启停任何服务、不评估"该不该重启"（守护的事）、不读二维码图片
（那是一次 base64，不能塞进 5 秒轮询）。
"""

from __future__ import annotations

import time
from typing import Any

from .services import ALL_SERVICES, ServiceManager
from .supervisor import ServiceSupervisor

# 运行态刚建立时允许的沉默窗口：进程刚起来还没写第一份快照，不该立刻报"没响应"
STARTUP_GRACE_S = 30.0


def service_summary(
    services: ServiceManager, supervisor: ServiceSupervisor | None = None, *, now: float | None = None
) -> dict[str, Any]:
    now = time.time() if now is None else now
    statuses = {name: services.status(name) for name in ALL_SERVICES}
    snap = supervisor.snapshot() if supervisor is not None else {}
    entries = snap.get("services") or {}
    rows: list[dict[str, Any]] = []
    for name in ALL_SERVICES:
        entry = entries.get(name) or {}
        rows.append(
            {
                **statuses[name],
                "desired": entry.get("desired", "unknown"),
                "skipped": entry.get("skipped", ""),
                "attempts": entry.get("attempts", 0),
                "restarts": entry.get("restarts_total", 0),
                "running_since": entry.get("running_since", 0.0),
                "last_exit": entry.get("last_exit") or {},
                "next_retry_at": entry.get("next_retry_at", 0.0),
                "last_error": entry.get("last_error", ""),
                "supervisor_alert": entry.get("alert"),
            }
        )
    napcat = services.napcat_status()
    alerts = _alerts(rows, snap, napcat, now)
    return {
        "ok": not any(alert["level"] == "error" for alert in alerts),
        "counts": {"online": sum(1 for row in rows if row["running"]), "total": len(rows)},
        "services": rows,
        "alerts": alerts,
        "supervisor": {
            "supervised": snap.get("supervised", False),
            "mode": snap.get("mode", "unknown"),
            "holder": snap.get("holder", ""),
            "policy": snap.get("policy"),
            "last_error": snap.get("last_error", ""),
        },
        "napcat": napcat,
        "updated": now,
    }


def _alerts(
    rows: list[dict[str, Any]], snap: dict[str, Any], napcat: dict[str, Any], now: float
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    by_name = {row["name"]: row for row in rows}
    if snap.get("mode") == "standby":
        alerts.append(
            {
                "level": "info",
                "code": "supervisor_standby",
                "action": "",
                "text": f"另一个工作台实例正在守护服务（{snap.get('holder') or '未知'}），本实例只读",
            }
        )
    for row in rows:
        if row.get("state") == "blocked_unverified":
            alerts.append(
                {
                    "level": "error",
                    "code": "blocked_unverified",
                    "action": "cleanup",
                    "text": f"{row['label']} 有不可核验的旧进程 pid={row.get('pid')}，未接管",
                }
            )
        alert = row.get("supervisor_alert") or {}
        if alert.get("code") == "restart_given_up":
            alerts.append(
                {"level": "error", "code": "restart_given_up", "action": "log", "text": alert.get("text", "")}
            )
        elif (
            row.get("desired") == "running"
            and not row["running"]
            and not row.get("skipped")
            and row.get("state") != "blocked_unverified"
        ):
            # 不可核验的记录已经单独报过：再加一条"未在运行 + 去重启"只会引导用户点错按钮
            suffix = f"（{row['last_error']}）" if row.get("last_error") else ""
            alerts.append(
                {
                    "level": "error",
                    "code": "service_exited",
                    "action": "restart",
                    "text": f"{row['label']} 未在运行{suffix}",
                }
            )

    heartbeat = by_name.get("omni-heartbeat", {})
    if heartbeat.get("running") and not heartbeat.get("fresh"):
        age = heartbeat.get("running_since") or 0
        if not age or now - age >= STARTUP_GRACE_S:
            reason = heartbeat.get("health_reason")
            text = (
                "心跳调度在跑，但已经很久没上报（可能卡住了）"
                if reason == "stale"
                else "心跳调度在跑，但还没有运行态快照（可能刚启动或写入失败）"
            )
            alerts.append({"level": "warn", "code": "heartbeat_stale", "action": "log", "text": text})

    qq = by_name.get("omni-qq", {})
    if qq.get("running") and qq.get("fresh") and (qq.get("stats") or {}).get("connected") is False:
        alerts.append(
            {"level": "warn", "code": "qq_disconnected", "action": "qr", "text": "QQ 通道在跑，但没连上 NapCat"}
        )

    if napcat.get("installed") and not napcat.get("logged_in"):
        alerts.append(
            {"level": "info", "code": "napcat_not_logged_in", "action": "qr", "text": "NapCat 未登录 QQ，需要扫码"}
        )
    return alerts
