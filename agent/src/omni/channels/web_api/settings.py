"""配对与设置HTTP适配；复用注入身份和Runtime，不承担应用生命周期。"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...config import REASONING_SLOTS, load_config
from ...llm.gateway import GatewayError
from ...system.config_io import EDITABLE, ConfigEditError, commit_config_updates, read_env_secrets, set_env_secret
from ...system.filelock import LockTimeout
from ..web_auth import COOKIE_NAME
from .schemas import BiliCookiesIn, ConfigUpdateIn, ModelKeyIn, ModelTestIn, PairIn

if TYPE_CHECKING:
    from ...bootstrap import Runtime

_SECRET_KEYS = {("qq", "access_token")}


def _set_session_cookie(response, value: str, max_age: int):
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=max_age,
        httponly=True,  # 前端脚本读不到，XSS 也偷不走
        samesite="strict",  # 跨站请求不携带 → CSRF 第一层
        path="/",
    )
    return response


def _normalize_secret_updates(updates: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], bool]:
    """密钥字段三态归一，返回 (过滤后的更新, 是否真的写入密钥明文)。

    - 键缺省 / 空串 → 保留旧值（前端拿到的是掩码，空值不能等于"清空"）
    - `null` → 显式清除（写空串）
    - 非空字符串 → 显式替换

    `touched` 只在**要写入非空明文**时为真：只有这种情形错误信息才可能带出密钥。
    以前只要请求里出现密钥键就置真，而前端每次都带一个空串的 `qq.access_token`，
    于是任何校验失败都被替换成"含密钥字段，详情已省略"，把真实原因彻底藏住。
    """
    cleaned: dict[str, dict[str, Any]] = {}
    touched = False
    for section, keys in (updates or {}).items():
        for key, value in (keys or {}).items():
            if (section, key) not in _SECRET_KEYS:
                cleaned.setdefault(section, {})[key] = value
                continue
            if value is None:
                cleaned.setdefault(section, {})[key] = ""  # 清除，写的是空串，不含明文
            elif isinstance(value, str) and not value.strip():
                continue  # 保留旧值
            else:
                cleaned.setdefault(section, {})[key] = value
                touched = True
    return {s: k for s, k in cleaned.items() if k}, touched


def create_settings_router(runtime: Runtime, auth) -> APIRouter:
    router = APIRouter(tags=["settings"])

    @router.get("/api/auth/status")
    def auth_status(request: Request) -> dict[str, Any]:
        payload = {
            "auth_required": True,
            "paired": auth.verify_session(request.cookies.get(COOKIE_NAME)),
            "session_days": auth.session_ttl // 86400,
        }
        if request.query_params.get("startup") in {"1", "true"}:
            payload.update(boot_id=auth.boot_id, pid=os.getpid(), ready=not runtime.updates.probe_mode)
        return payload

    @router.post("/api/auth/pair")
    def auth_pair(payload: PairIn, request: Request) -> JSONResponse:
        # 身份建立接口自己不能允许跨站配对：Origin 必须是本机
        if not auth.origin_ok(request.headers.get("origin")):
            return JSONResponse({"detail": "拒绝跨站配对", "code": "origin_rejected"}, status_code=403)
        result = auth.pair(payload.code)
        if not isinstance(result, str):
            return JSONResponse({"detail": result.detail, "code": result.code}, status_code=403)
        response = JSONResponse({"ok": True, "session_days": auth.session_ttl // 86400})
        return _set_session_cookie(response, result, auth.session_ttl)

    @router.post("/api/auth/logout")
    def auth_logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @router.get("/pair/{code}")
    def pair_link(code: str, request: Request):
        """控制台配对链接：设置会话 cookie 后跳回工作台（前端不需要改）。

        拒绝跨站导航（`Sec-Fetch-Site: cross-site`）——否则恶意页面可以把用户
        "导航"到这个链接来帮忙完成配对。
        """
        fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return HTMLResponse(
                "<h3>拒绝跨站配对</h3><p>请直接打开控制台输出的配对链接。</p>",
                status_code=403,
            )
        result = auth.pair(code)
        if not isinstance(result, str):
            # 旧链接和真·码不对要说清区别：前者只是点了聊天记录里更早那条，
            # 后者才可能是别人在猜。混成一句话只会让人以为"链接又坏了"。
            if result.code == "pair_stale":
                tip = "这条链接是之前那版，已经被换掉了。工作台窗口里有当前可用的那条。"
            elif result.code == "pair_expired":
                tip = "链接超时了。工作台窗口里打印了新的那条，直接打开即可。"
            else:
                tip = "这条不是当前有效的配对链接。工作台窗口里打印了当前那条。"
            return HTMLResponse(
                f"<h3>配对失败</h3><p>{result.detail}</p><p>{tip}</p>",
                status_code=403,
            )
        response = RedirectResponse("/", status_code=303)
        return _set_session_cookie(response, result, auth.session_ttl)

    # 注意：必须声明在 /api/skills/{name} 之前，否则 "pending" 会被当成技能名。

    def _config_from_disk() -> tuple[Any, bool]:
        """返回（磁盘上的配置, 是否需要重启才在运行期生效）。

        为什么要读磁盘：`runtime.config` 是**进程启动那一刻**的快照。保存成功后页面
        再打开，读到的还是旧值——用户看到"我明明选了，怎么又空了"，只能再选一遍，
        而这一遍又会把上一次的选择覆盖掉。页面显示的必须是文件里真实的值。
        读失败（文件被写坏/被占用）就退回启动快照，不让设置页整页打不开。
        """
        try:
            fresh = load_config(runtime.paths.config)
        except Exception:  # noqa: BLE001 —— 配置损坏时仍要看得到页面
            return runtime.config, False
        # 只比设置中心真的会写的段：其它字段在启动时被目录/身份覆盖过，
        # 拿来比较会永远显示"待重启"。
        watched = (
            "models",
            "reasoning",
            "mode_routing",
            "review",
            "approval",
            "asr",
            "qq",
            "browser",
            "vision",
            "desktop",
        )
        pending = any(getattr(fresh, name) != getattr(runtime.config, name) for name in watched)
        # 人格单独比：只有 system_prompt 与场景层进提示词，改了要重启才生效。
        # name 只用于显示（CLI 标题、会话元数据），而且 bootstrap 会用目录
        # profile 覆盖它——整段比较会让非默认 profile 永远误报"待重启"。
        # 以前 persona 不在比较范围里，改完人格前端显示"已生效"，
        # 但运行中的 Agent 用的还是启动时的快照——用户以为改了，其实没改。
        if fresh.persona.system_prompt != runtime.config.persona.system_prompt:
            pending = True
        # 场景层同样进提示词（D36），改了也要重启才生效。
        if (
            fresh.persona.group_prompt != runtime.config.persona.group_prompt
            or fresh.persona.private_prompt != runtime.config.persona.private_prompt
        ):
            pending = True
        return fresh, pending

    @router.get("/api/system/config")
    def system_config() -> dict[str, Any]:
        cfg, pending_restart = _config_from_disk()
        return {
            "persona": {
                "name": cfg.persona.name,
                "system_prompt": cfg.persona.system_prompt,
                # 场景层（D36）：群聊/私聊各一段，留空 = 没有这一层。
                "group_prompt": cfg.persona.group_prompt,
                "private_prompt": cfg.persona.private_prompt,
            },
            "pending_restart": pending_restart,
            "models": {
                "chat": cfg.models.chat,
                "vision": cfg.models.vision,
                "review": cfg.models.review,
                # 能力档位（D28）：各档模型与路由判定模型
                "router": cfg.models.router,
                "chat_mode": cfg.models.chat_mode,
                "retrieval_mode": cfg.models.retrieval_mode,
                "agent_mode": cfg.models.agent_mode,
            },
            # 推理开关与强度（D30）：每档一套，auto = 沿用供应商默认
            "reasoning": {slot: getattr(cfg.reasoning, slot) for slot in REASONING_SLOTS},
            "mode_routing": {
                "default_mode": cfg.mode_routing.default_mode,
                "allow_escalation": cfg.mode_routing.allow_escalation,
            },
            "limits": {
                "max_history_messages": cfg.limits.max_history_messages,
                "max_agent_steps": cfg.limits.max_agent_steps,
            },
            "review": {
                "enabled": cfg.review.enabled,
                "max_messages": cfg.review.max_messages,
                "steps": cfg.review.steps,
            },
            "journal": {
                "enabled": cfg.journal.enabled,
                "daily_time": cfg.journal.daily_time,
                "steps": cfg.journal.steps,
                "max_catchup": cfg.journal.max_catchup,
                "max_input_chars": cfg.journal.max_input_chars,
                "max_note_chars": cfg.journal.max_note_chars,
                "daily_prompt": cfg.journal.daily_prompt,
                "weekly_prompt": cfg.journal.weekly_prompt,
                "monthly_prompt": cfg.journal.monthly_prompt,
            },
            "transcript": {
                "max_entry_chars": cfg.transcript.max_entry_chars,
                "max_tool_args_chars": cfg.transcript.max_tool_args_chars,
                "max_tool_result_chars": cfg.transcript.max_tool_result_chars,
                "retention_days": cfg.transcript.retention_days,
                "dialogue_retention_days": cfg.transcript.dialogue_retention_days,
            },
            "people": {
                "person_card_chars": cfg.people.person_card_chars,
                "archive_max_chars": cfg.people.archive_max_chars,
                "resident_max_chars": cfg.people.resident_max_chars,
                "event_max_chars": cfg.people.event_max_chars,
            },
            "approval": {"mode": cfg.approval.mode},
            "skills": {
                "history_keep": cfg.skills.history_keep,
                "max_write_chars": cfg.skills.max_write_chars,
            },
            "asr": {
                "model": cfg.asr.model,
                "device": cfg.asr.device,
                "compute_type": cfg.asr.compute_type,
            },
            "qq": {
                "enabled": cfg.qq.enabled,
                "ws_url": cfg.qq.ws_url,
                # 不回显明文：前端只能知道"是否已配置"，修改见 system_config_save 的三态规则
                "access_token": "",
                "access_token_configured": bool(cfg.qq.access_token),
                "allowed_users": cfg.qq.allowed_users,
                "allowed_groups": cfg.qq.allowed_groups,
                "reply_delay_s": cfg.qq.reply_delay_s,
                **{
                    key: getattr(cfg.qq, key)
                    for key in (
                        "proactive_enabled",
                        "owner_qq",
                        "proactive_daily_limit",
                        "quiet_start",
                        "quiet_end",
                        "timezone",
                        "group_reply_to_bot",
                    )
                },
            },
            "browser": {
                "headless": cfg.browser.headless,
                "slow_mo_ms": cfg.browser.slow_mo_ms,
                "max_writes_per_day": cfg.browser.max_writes_per_day,
                "approval_timeout_s": cfg.browser.approval_timeout_s,
            },
            "vision": {"send_to_cloud": cfg.vision.send_to_cloud},
            "desktop": {
                "enabled": cfg.desktop.enabled,
                "foreground_enabled": cfg.desktop.foreground_enabled,
                "allowed_apps": cfg.desktop.allowed_apps,
            },
            # 服务守护开关：守护线程每轮热读这几个键，所以保存后立刻生效
            "system": {
                "napcat_dir": cfg.system.napcat_dir,
                "autostart": cfg.system.autostart,
                "keep_running_after_exit": cfg.system.keep_running_after_exit,
                "restart_max_attempts": cfg.system.restart_max_attempts,
                "napcat_qq": cfg.system.napcat_qq,
            },
            "editable": sorted(f"{s}.{k}" for s, k in EDITABLE),
        }

    @router.post("/api/system/config")
    def system_config_save(payload: ConfigUpdateIn) -> dict[str, Any]:
        """改配置：密钥字段三态——缺省/空串=保留旧值，null=显式清除，非空=替换。"""
        updates, touched_secret = _normalize_secret_updates(payload.updates)
        try:
            config_file = runtime.paths.config  # 与 bootstrap 读取的是同一个文件
            commit_config_updates(config_file, updates)
        except (ValueError, OSError, yaml.YAMLError, LockTimeout) as exc:
            # 含密钥字段时只回笼统原因，避免把密钥值回显到错误信息/日志里
            detail = "配置更新被拒绝（含密钥字段，详情已省略）" if touched_secret else str(exc)
            raise HTTPException(status_code=400, detail=detail)
        # 写完再读一遍盘：让界面知道"这次改动到底要不要重启才生效"，
        # 而不是每次都甩一句"重启后生效"让人自己去猜。
        _, pending = _config_from_disk()
        return {
            "ok": True,
            "pending_restart": pending,
            "hint": (
                "已写入 config.yaml；正在运行的服务重启后生效（见服务面板）"
                if pending
                else "已写入 config.yaml，与当前运行配置一致"
            ),
        }

    @router.get("/api/system/qq-policy")
    def qq_policy_status() -> dict[str, Any]:
        import time

        from ...core.qq_policy import proactive_gate, runtime_last_inbound_at

        policy = runtime.qq_policy()
        last = runtime_last_inbound_at(runtime, policy.owner_qq)
        remaining = max(0.0, policy.proactive_cooldown_minutes * 60 - (time.time() - last)) if last else 0.0
        decision = proactive_gate(policy, policy.owner_qq, last_inbound_at=last)
        return {
            "enabled": policy.proactive_enabled,
            "owner_qq": policy.owner_qq,
            "decision": decision.state,
            "reason": decision.reason,
            "cooldown": {"active": remaining > 0, "remaining_s": remaining},
            "used": runtime.state.budget_used("qq_proactive", decision.day) if decision.day else None,
            "limit": policy.proactive_daily_limit,
            "events": runtime.qq_messages.status() if runtime.qq_messages else {},
        }

    @router.post("/api/system/qq-policy/stop")
    def qq_policy_stop() -> dict[str, Any]:
        system_config_save(ConfigUpdateIn(updates={"qq": {"proactive_enabled": False}}))
        if runtime.outbox:
            for item in runtime.outbox.pending():
                runtime.outbox.discard(item["id"], note="用户一键停止主动提醒")
        return {"ok": True, "hint": "主动提醒已停用，尚未开始的投递已丢弃"}

    @router.get("/api/system/bili-cookies")
    def bili_cookies_status() -> dict[str, Any]:
        keys = {"SESSDATA": False, "bili_jct": False, "buvid3": False}
        cookies_path = runtime.paths.cookies  # 注入路径：不碰真机 data/
        try:
            data = json.loads(cookies_path.read_text(encoding="utf-8"))
            for k in keys:
                keys[k] = bool(str(data.get(k, "")).strip())
        except (OSError, json.JSONDecodeError):
            pass
        return {"configured": any(keys.values()), "keys": keys, "path": str(cookies_path)}

    @router.post("/api/system/bili-cookies")
    def save_bili_cookies(payload: BiliCookiesIn) -> dict[str, Any]:
        data: dict[str, str] = {}
        for name, value in (
            ("SESSDATA", payload.sessdata),
            ("bili_jct", payload.bili_jct),
            ("buvid3", payload.buvid3),
        ):
            value = value.strip()
            if value:
                data[name] = value
        if not data:
            raise HTTPException(status_code=400, detail="至少要填一项 Cookie")
        cookies_path = runtime.paths.cookies
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "saved": list(data), "path": str(cookies_path)}

    @router.get("/api/system/model-keys")
    def model_keys() -> dict[str, Any]:
        """各供应商密钥是否已配置 + 脱敏值；前端只用来显示"已填 / 未填"。"""
        return {"keys": read_env_secrets(runtime.paths.env)}

    @router.post("/api/system/model-key")
    def model_key_set(payload: ModelKeyIn) -> dict[str, Any]:
        """写一个密钥进 .env 并注入当前进程环境；空值等于清除该行。"""
        try:
            masked = set_env_secret(runtime.paths.env, payload.name, payload.value)
        except ConfigEditError as exc:
            # 只有配置类错误才回具体原因；这里不涉及密钥值本身
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"写入 .env 失败：{exc}") from exc
        return {
            "ok": True,
            "name": payload.name,
            "masked": masked,
            "configured": bool(masked),
        }

    @router.post("/api/system/model-test")
    def model_test(payload: ModelTestIn) -> dict[str, Any]:
        """连通性测试：错误码映射成可分辨的 HTTP 状态，错误文本已脱敏。"""
        try:
            reply = runtime.gateway.complete(
                [{"role": "user", "content": "这是一次连通性测试，请只回复：OK"}],
                model=payload.model,
                purpose="test",
            )
        except GatewayError as exc:
            status = {
                "auth": 401,
                "permission": 403,
                "rate_limit": 429,
                "budget_exhausted": 402,
                "price_unknown": 400,
                "unsupported_capability": 400,
                "cancelled": 409,
            }.get(exc.code, 400)
            raise HTTPException(
                status_code=status,
                detail={"code": exc.code, "message": str(exc), "hint": exc.hint},
            )
        return {
            "ok": True,
            "reply": reply.text[:120],
            "cost_micro": reply.cost_micro,
            "cost_source": reply.cost_source,
        }

    return router
