"""QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。

职责纪律：只做「事件路由 + 白名单过滤 + 会话绑定 + 拟人延迟 + 回发」；
领域操作全部经 Runtime 门面。安装 NapCat 见 docs/QQ_SETUP.md。

安全边界（A03/A05 修复后的语义）：
- 私聊：`allowed_users` **为空时谁都不回**（空名单不等于不限）；名单外静默忽略（不暴露存在）；
- 群聊：`allowed_groups` 内的群 + 消息里 @机器人 才生效；群聊**不注入**所有者档案/常驻笔记，
  并禁用个人记忆类工具（他人可见的上下文不该带出私人信息）；
- 会话按场景分开绑定：私聊 `dm:<user>`、群聊 `group:<group>`，同一发送者在两个群不会串历史；
- 策略每次从配置源重读：撤销白名单在下一条消息就生效，不必重启进程；
- 工具：QQ 上下文 DenyApprover（fail-closed），safe 级直通。
"""
from __future__ import annotations

import asyncio
import os
import random

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..llm.gateway import GatewayError
from ..safety.gates import DenyApprover, private_target_allowed
from ..tools.qq.client import OneBotClient, clean_cq_message


def _resolve_scope(
    runtime: Runtime, client: OneBotClient, event: dict
) -> tuple[str, int, bool] | None:
    """判定本条消息是否该处理：返回 (作用域键, 会话 key, 是否带个人上下文)。

    拒绝路径一律返回 None（静默），不区分"没授权"和"不存在"，避免探测。
    """
    policy = runtime.qq_policy()
    message_type = event.get("message_type")
    user_id = int(event.get("user_id") or 0)
    if user_id <= 0:
        return None  # 伪造/异常事件
    if message_type == "private":
        if not private_target_allowed(policy.allowed_users, user_id).allowed:
            return None
        return "dm", user_id, True
    if message_type == "group":
        group_id = int(event.get("group_id") or 0)
        if group_id <= 0 or group_id not in set(policy.allowed_groups):
            return None
        raw = str(event.get("raw_message") or "")
        if client.self_id is None or f"[CQ:at,qq={client.self_id}" not in raw:
            return None  # 群里只回应被 @ 的消息
        return "group", group_id, False
    return None


def _session_for(runtime: Runtime, scope: str, key: int) -> int:
    """场景内绑定会话；私聊兼容旧键（旧键只可能来自私聊，绝不迁进群）。"""
    if scope == "group":
        return runtime.store.get_or_create_binding("qq", f"group:{key}")
    binding_key = f"dm:{key}"
    existing = runtime.store.get_binding("qq", binding_key)
    if existing is not None:
        return existing
    legacy = runtime.store.get_binding("qq", str(key))
    if legacy is not None:
        runtime.store.set_binding("qq", binding_key, legacy)
        return legacy
    return runtime.store.get_or_create_binding("qq", binding_key)


async def _handle_message(
    runtime: Runtime, client: OneBotClient, event: dict, locks: dict[str, asyncio.Lock]
) -> None:
    scope = _resolve_scope(runtime, client, event)
    if scope is None:
        return
    scope_name, target_id, personal_context = scope
    raw = str(event.get("raw_message") or "")
    text = clean_cq_message(raw, self_id=client.self_id)
    if not text:
        return

    user_id = int(event.get("user_id") or 0)
    sender = (event.get("sender") or {}).get("nickname") or str(user_id)
    session_id = _session_for(runtime, scope_name, target_id)
    lock_key = f"{scope_name}:{target_id}"
    lock = locks.setdefault(lock_key, asyncio.Lock())  # 同一会话串行，防历史交叉
    cfg = runtime.config.qq
    async with lock:
        lo, hi = (cfg.reply_delay_s + [1.0, 3.0])[:2]
        await asyncio.sleep(random.uniform(float(lo), float(hi)))  # 拟人：不秒回
        try:
            answer = await asyncio.to_thread(
                runtime.agent.chat,
                text,
                session_id=session_id,
                personal_context=personal_context,
            )
        except GatewayError as exc:
            answer = f"（模型调用失败：{exc}）"
        runtime.spawn_review(session_id)  # QQ 的回合也进记忆自整理

    where = f"群{target_id}" if scope_name == "group" else "私聊"
    print(
        f"[QQ] {where} {sender}({user_id}): {text[:40]} → 回复 {len(answer)} 字", flush=True
    )
    if scope_name == "dm":
        await client.send_private(target_id, answer)
    else:
        await client.send_group(target_id, answer)


async def _serve(runtime: Runtime) -> None:
    client = runtime.qq
    assert client is not None  # main() 已检查
    locks: dict[str, asyncio.Lock] = {}

    async def on_event(event: dict) -> None:
        if event.get("post_type") != "message":
            return
        try:
            await _handle_message(runtime, client, event, locks)
        except Exception as exc:  # noqa: BLE001 —— 单条消息失败不拖垮通道
            print(f"[QQ] 处理消息失败: {exc}", flush=True)

    await client.run(on_event)


def main() -> None:
    load_env()
    runtime = build_runtime(approver=DenyApprover(), owner_kind="qq")
    # pid 目录来自 RuntimePaths：与运行数据根同源，不再另拼全局路径
    pid_file = runtime.paths.run / "omni-qq.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # 工作台状态探测用
    try:
        if runtime.qq is None:
            raise SystemExit(
                "QQ 未启用：在 config/config.yaml 设置 qq.enabled: true，"
                "并按 docs/QQ_SETUP.md 启动 NapCat。"
            )
        policy = runtime.qq_policy()
        if not policy.allowed_users:
            print(
                "[QQ] 警告：allowed_users 为空，将不响应任何私聊、也不投递任何主动消息"
                "（先配置你的 QQ 号）",
                flush=True,
            )
        if policy.allowed_groups:
            print(
                f"[QQ] 已授权群 {policy.allowed_groups}：仅在被 @ 时回复，且不注入个人档案",
                flush=True,
            )
        asyncio.run(_serve(runtime))
    finally:
        pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
