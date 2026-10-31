"""市场 HTTP 适配：人设卡与插件列表/安装/激活，全部消费注入 Runtime。

边界（与技能中心同一条纪律线）：
- 本模块不构造 CommunityClient、PluginHost，也不自己发 HTTP 到社区源：
  读取一律走 `runtime.community.fetch`（唯一出口），装/激活/卸载一律走
  `runtime.plugins`（隔离区 + 防篡改复核 + 人工确认）；
- **只读接口不改本机任何状态**：market/status、索引、详情都是纯查询；
- 安装只是把 zip 落到隔离区（不执行代码）；激活（会执行插件代码）必须由用户
  在工作台弹窗里明确点「激活」——那一次点击就是人工确认，与技能包激活同构；
- 人设卡是纯文本，导入只把内容交给界面（前端存进本机预设库），后端不写配置。

源：`data/plugins/community.json` 的 server_url，未配置时回落到
`OMNI_MARKET_URL`（官方默认源）。改源是普通字段写入，不需要重启。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...plugins.community.persona import normalize, normalize_many

if TYPE_CHECKING:
    from ...bootstrap import Runtime
    from ...plugins.community import CommunityClient

LIMIT_MAX = 50
TAGS_MAX = 6
TEXT_MAX = 300          # 作者自填文本的默认收窄长度（描述类字段）


class MarketSourceIn(BaseModel):
    """市场源：空串 = 清除显式配置（回落到默认源）。"""

    server_url: str = Field(default="", max_length=300)


class MarketInstallIn(BaseModel):
    version: str = Field(default="", max_length=40)


def _community(runtime: Runtime) -> CommunityClient:
    client = runtime.community
    if client is None:
        raise HTTPException(status_code=503, detail="市场客户端不可用（本入口未装配社区源）")
    return client


def _host(runtime: Runtime):
    host = runtime.plugins
    if host is None:
        raise HTTPException(status_code=503, detail="插件宿主不可用")
    return host


def _clip(value: Any, limit: int = TEXT_MAX) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _string_list(raw: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            item = item.strip()[:item_limit]
            if item and item not in out:
                out.append(item)
        if len(out) >= limit:
            break
    return out


def _plugin_card(raw: Any) -> dict[str, Any] | None:
    """把一条插件索引/详情收窄成界面契约（作者字段来自网络，一律当不可信）。"""
    if not isinstance(raw, dict):
        return None
    plugin_id = _clip(raw.get("id"), 64)
    if not plugin_id or "/" in plugin_id:
        return None
    files = []
    if isinstance(raw.get("files"), list):
        for entry in raw["files"][:40]:
            if isinstance(entry, dict):
                path = _clip(entry.get("path"), 120)
                if path:
                    size = entry.get("size")
                    files.append({"path": path, "size": size if isinstance(size, int) else 0})
    size = raw.get("size") or raw.get("latest_size") or 0
    downloads = raw.get("downloads") or 0
    homepage = _clip(raw.get("homepage"), 200)
    return {
        "id": plugin_id,
        "name": _clip(raw.get("name"), 60) or plugin_id,
        "version": _clip(raw.get("latest_version") or raw.get("version"), 40),
        "versions": _string_list(raw.get("versions"), limit=20, item_limit=40),
        "description": _clip(raw.get("description"), 300),
        "author": _clip(raw.get("author"), 60),
        "homepage": homepage if homepage.startswith(("http://", "https://")) else "",
        "tags": _string_list(raw.get("tags"), limit=TAGS_MAX, item_limit=12),
        "downloads": downloads if isinstance(downloads, int) and downloads >= 0 else 0,
        "updated_at": _clip(raw.get("updated_at"), 40),
        "sha256": _clip(raw.get("sha256") or raw.get("latest_sha256"), 64),
        "size": size if isinstance(size, int) and size >= 0 else 0,
        "files": files,
    }


def _plugin_cards(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [card for card in (_plugin_card(item) for item in raw) if card is not None]


def _state(runtime: Runtime) -> dict[str, Any]:
    """本机状态：已装 + 待激活 + 收件箱（界面与插件工具看的是同一份）。"""
    host = _host(runtime)
    return {
        "installed": host.installed(),
        "quarantine": host.pending(),
        "inbox": host.inbox_files(),
    }


def create_market_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    # ---------------------------------------------------------------- 源

    @router.get("/api/market/status")
    def market_status() -> dict[str, Any]:
        """源与连通性：只读，不触发安装，不改配置。"""
        client = _community(runtime)
        configured = client.config()
        effective = client.effective_server_url()
        payload: dict[str, Any] = {
            "configured": bool(configured["server_url"]),
            "server_url": effective,
            "token_set": bool(configured["token"]),
            "default_source": not configured["server_url"] and bool(effective),
            "hint": (
                "这里显示的是市场源地址。装插件仍要走本机隔离安装，激活前会再问你一次。"
            ),
        }
        if effective:
            data, problem = client.fetch("/api/health")
            payload["reachable"] = data is not None
            payload["totals"] = (
                {
                    "plugins": int(data.get("plugins", 0) or 0),
                    "personas": int(data.get("personas", 0) or 0),
                }
                if data
                else {"plugins": 0, "personas": 0}
            )
            payload["error"] = "" if data is not None else problem
        else:
            payload["reachable"] = False
            payload["totals"] = {"plugins": 0, "personas": 0}
            payload["error"] = "还没有设置市场源"
        return payload

    @router.put("/api/market/source")
    def market_source_save(payload: MarketSourceIn) -> dict[str, Any]:
        client = _community(runtime)
        ok, message = client.configure(server_url=payload.server_url.strip())
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message, "server_url": client.effective_server_url()}

    # ---------------------------------------------------------------- 人设

    @router.get("/api/market/personas")
    def market_personas(query: str = "", limit: int = LIMIT_MAX) -> dict[str, Any]:
        """人设索引：逐字段收窄后返回（含 system_prompt，界面要原样给用户看全文）。"""
        limit = max(1, min(int(limit), LIMIT_MAX))
        data, problem = _community(runtime).persona_list(query.strip()[:60], limit)
        if data is None:
            raise HTTPException(status_code=502, detail=problem)
        items = normalize_many(data.get("items"))
        total = data.get("total")
        return {"items": items, "total": total if isinstance(total, int) else len(items)}

    @router.get("/api/market/personas/{persona_id}")
    def market_persona_detail(persona_id: str) -> dict[str, Any]:
        data, problem = _community(runtime).persona_detail(persona_id)
        if data is None:
            raise HTTPException(status_code=502, detail=problem)
        card = normalize({**data, "latest_version": data.get("version")})
        if card is None:
            raise HTTPException(status_code=422, detail="这个人设卡缺少必要字段（id/名称/行为设定）")
        return card

    # ---------------------------------------------------------------- 插件

    @router.get("/api/market/plugins")
    def market_plugins(query: str = "", limit: int = LIMIT_MAX) -> dict[str, Any]:
        limit = max(1, min(int(limit), LIMIT_MAX))
        data, problem = _community(runtime).plugin_list(query.strip()[:60], limit)
        if data is None:
            raise HTTPException(status_code=502, detail=problem)
        items = _plugin_cards(data.get("items"))
        total = data.get("total")
        return {"items": items, "total": total if isinstance(total, int) else len(items)}

    @router.get("/api/market/plugins/{plugin_id}")
    def market_plugin_detail(plugin_id: str) -> dict[str, Any]:
        data, problem = _community(runtime).plugin_detail(plugin_id)
        if data is None:
            raise HTTPException(status_code=502, detail=problem)
        card = _plugin_card(data)
        if card is None:
            raise HTTPException(status_code=422, detail="这个插件卡片缺少 id")
        return card

    # ---------------------------------------------------------------- 安装与激活

    @router.get("/api/market/installed")
    def market_installed() -> dict[str, Any]:
        return _state(runtime)

    @router.post("/api/market/plugins/{plugin_id}/install")
    def market_plugin_install(plugin_id: str, payload: MarketInstallIn | None = None) -> dict[str, Any]:
        """下载 → sha256 校验 → 落收件箱 → 隔离安装。**不执行插件代码**。"""
        client = _community(runtime)
        host = _host(runtime)
        plugin_id = plugin_id.strip()
        # 重复安装没有意义，而且会覆盖隔离区里还没决定的包：先拒绝，把选择权交回用户
        for item in host.pending():
            if item.get("id") == plugin_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"插件 {plugin_id} 已经下载好了，在等你在下面确认激活（或丢弃重下）",
                )
        for item in host.installed():
            if item.get("id") == plugin_id:
                raise HTTPException(
                    status_code=409, detail=f"插件 {plugin_id} 已安装；要换版本先卸载再装"
                )
        version = (payload.version if payload else "").strip()
        ok, message = client.install(plugin_id, version)
        if not ok:
            lowered = message
            status = 404 if "404" in lowered else (
                409 if "已有" in lowered or "已安装" in lowered else 502
            )
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    @router.post("/api/market/quarantine/{plugin_id}/activate")
    def market_plugin_activate(plugin_id: str) -> dict[str, Any]:
        """激活 = 同意执行该插件代码（界面必须已明确提示过）。"""
        ok, message = _host(runtime).activate(plugin_id)
        if not ok:
            status = 404 if "没有待激活" in message else (
                409 if ("复核" in message or "缺失" in message or "已安装" in message) else 400
            )
            raise HTTPException(status_code=status, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    @router.post("/api/market/quarantine/{plugin_id}/discard")
    def market_plugin_discard(plugin_id: str) -> dict[str, Any]:
        ok, message = _host(runtime).remove(plugin_id)
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    @router.post("/api/market/plugins/{plugin_id}/enable")
    def market_plugin_enable(plugin_id: str) -> dict[str, Any]:
        ok, message = _host(runtime).enable(plugin_id)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    @router.post("/api/market/plugins/{plugin_id}/disable")
    def market_plugin_disable(plugin_id: str) -> dict[str, Any]:
        ok, message = _host(runtime).disable(plugin_id)
        if not ok:
            raise HTTPException(status_code=400, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    @router.delete("/api/market/plugins/{plugin_id}")
    def market_plugin_remove(plugin_id: str) -> dict[str, Any]:
        ok, message = _host(runtime).remove(plugin_id)
        if not ok:
            raise HTTPException(status_code=404, detail=message)
        return {"ok": True, "message": message, **_state(runtime)}

    return router
