"""Read-only capability projection over the instance health snapshot; no I/O and no acceptance claims."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

FORMAT = "omni-capability-report-1"
DEFAULT_ENTRYPOINT = "v1"
ACCEPTED = False

# Bounded, owner-facing facts only. Paths, identifiers, credentials and free text never cross.
AREAS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("model_streaming", "模型流式调用", ("enabled", "owned", "active", "cleanup_pending"), "无真实供应商验收"),
    ("database", "数据库", ("closed", "pending_reads", "pending_writes"), "单 SQLite 实例"),
    ("logs", "诊断日志", ("writer_alive", "queued", "dropped", "written", "failures"), "仅元数据与脱敏文本"),
    ("documents", "来源文档与向量", ("pending",), "向量后端需真实环境验收"),
    ("memory_semantics", "记忆语义", ("model_calls_enabled", "pending"), "语义整理依赖模型配置"),
    ("memory_organization", "记忆整理", ("model_calls_enabled", "pending"), "预算与模型配置受限"),
    ("skills", "文本技能", ("executable_packages", "pending", "platform"), "平台执行未验收"),
    ("schedules", "持久调度", ("ready", "ticks", "enqueued", "tzdb_version"), "真实时区/长跑未验收"),
    ("deliveries", "持久投递", ("ready", "active", "stalled", "installed_transports"), "真实通道未验收"),
    ("qq", "QQ 通道", ("listeners", "worker_alive", "capacity"), "真实账号未验收"),
    ("bilibili", "B站通道", ("active_reads", "publication_approval_required"), "真实账号未验收"),
    ("media", "媒体处理", ("pending", "processor"), "真实素材与编解码未验收"),
    ("mcp", "MCP 运行适配器", ("transports",), "真实服务与 OAuth 供应商未验收"),
    ("extensions", "可信插件", ("hosts", "pending", "unavailable"), "真实第三方插件未验收"),
    ("android", "安卓设备", ("backend", "leases", "observations", "pending"), "真实设备未验收"),
    ("credentials", "凭据", ("backend", "pending_changes"), "仅回环与隔离后端"),
    ("action_reviews", "动作核查", ("pending",), "审批语义已离线验证"),
    ("terminals", "所有者终端", ("backend", "pending", "active", "cleanup_pending"), "真实终端未验收"),
    ("terminal_reviews", "终端核查", ("pending",), "审批语义已离线验证"),
    ("desktop", "桌面能力", ("backend", "pending", "cached_views"), "三平台原生未验收"),
    ("desktop_portal", "桌面门户", ("backend", "pending", "grants"), "真实桌面未验收"),
    ("desktop_actions", "桌面动作", ("bindings", "leases", "pending"), "真实桌面未验收"),
    ("maintenance", "维护与回收", ("ready", "active", "pending", "settlement_pending"), "长跑回收未验收"),
    ("images", "图片处理", ("pending",), "真实图片流程未验收"),
)

NOTES = (
    "默认正式入口仍是 V1；本报告不表示 V2 已切换或已验收。",
    "真实 MCP/OAuth、真实市场源与第三方插件、真实账号/设备与三平台原生仍未验收。",
    "unknown 结果不自动重放；能力状态只反映当前实例快照。",
)
MAX_FACT_TEXT = 80


def _fact(value: object) -> int | bool | str | None:
    if type(value) is bool or type(value) is int:
        return value
    if isinstance(value, str) and len(value) <= MAX_FACT_TEXT:
        return value
    if isinstance(value, (list, tuple, dict)):
        return len(cast(list[object] | tuple[object, ...] | dict[object, object], value))
    return None


def _state(section: Mapping[str, object]) -> str:
    if section.get("closing") is True:
        return "attention"
    for key in ("error_code", "last_error"):
        value = section.get(key)
        if isinstance(value, str) and value:
            return "attention"
    if section.get("ready") is False:
        return "attention"
    return "available"


def _integer(value: object) -> int:
    return value if type(value) is int else 0


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def capability_report(health: Mapping[str, object]) -> dict[str, object]:
    """Bounded projection; missing sections stay absent rather than being guessed."""
    areas: list[dict[str, object]] = []
    for key, name, fields, limits in AREAS:
        section = health.get(key)
        if not isinstance(section, Mapping):
            continue
        section = cast(Mapping[str, object], section)
        facts: dict[str, object] = {}
        for field in fields:
            value = _fact(section.get(field))
            if value is not None:
                facts[field] = value
        areas.append(
            {
                "id": key,
                "name": name,
                "state": _state(section),
                "facts": facts,
                "limits": limits,
            }
        )
    return {
        "format": FORMAT,
        "api_version": _integer(health.get("api_version")),
        "schema_version": _text(health.get("schema_version")),
        "build_id": _text(health.get("build_id")),
        "ready": health.get("ready") is True,
        "closing": health.get("closing") is True,
        "platform_acceptance": _text(health.get("platform_acceptance")) or "unknown",
        "entrypoint": DEFAULT_ENTRYPOINT,
        "v2_accepted": ACCEPTED,
        "areas": areas,
        "counts": {
            "areas": len(areas),
            "attention": sum(1 for area in areas if area["state"] == "attention"),
        },
        "notes": list(NOTES),
    }
