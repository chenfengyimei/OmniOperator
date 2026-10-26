"""QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。

职责纪律：只做「事件路由 + 白名单过滤 + 会话绑定 + 拟人延迟 + 回发」；
领域操作全部经 Runtime 门面。安装 NapCat 见 docs/QQ_SETUP.md。

安全边界（NOTES §1.7，P2 上线当天必须有"谁能指挥它"的白名单）：
- 私聊：仅 allowed_users 内的 QQ 号生效，其它静默忽略（不暴露存在）；
- 群聊：仅 allowed_groups 内的群 + 消息里 @机器人 才生效；
- 工具：QQ 上下文 DenyApprover（fail-closed），safe 级直通。
"""
from __future__ import annotations

import asyncio
import random

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..llm.gateway import GatewayError
from ..safety.gates import DenyApprover
from ..tools.qq.client import OneBotClient, clean_cq_message


async def _handle_message(
    runtime: Runtime, client: OneBotClient, event: dict, locks: dict[int, asyncio.Lock]
) -> None:
    cfg = runtime.config.qq
    message_type = event.get("message_type")
    user_id = int(event.get("user_id") or 0)
    raw = str(event.get("raw_message") or "")
    group_id = int(event.get("group_id") or 0)

    if message_type == "private":
        if cfg.allowed_users and user_id not in cfg.allowed_users:
            return  # 白名单外：静默忽略
    elif message_type == "group":
        if group_id not in cfg.allowed_groups:
            return
        if client.self_id is None or f"[CQ:at,qq={client.self_id}" not in raw:
            return  # 群里只回应被 @ 的消息
    else:
        return

    text = clean_cq_message(raw, self_id=client.self_id)
    if not text:
        return

    sender = (event.get("sender") or {}).get("nickname") or str(user_id)
    session_id = runtime.store.get_or_create_binding("qq", str(user_id))
    lock = locks.setdefault(user_id, asyncio.Lock())
    async with lock:  # 同一用户的回合串行，防止历史交叉
        lo, hi = (cfg.reply_delay_s + [1.0, 3.0])[:2]
        await asyncio.sleep(random.uniform(float(lo), float(hi)))  # 拟人：不秒回
        try:
            answer = await asyncio.to_thread(
                runtime.agent.chat, text, session_id=session_id
            )
        except GatewayError as exc:
            answer = f"（模型调用失败：{exc}）"
        runtime.spawn_review(session_id)  # QQ 的回合也进记忆自整理

    print(f"[QQ] {sender}({user_id}): {text[:40]} → 回复 {len(answer)} 字", flush=True)
    if message_type == "private":
        await client.send_private(user_id, answer)
    else:
        await client.send_group(group_id, answer)


async def _serve(runtime: Runtime) -> None:
    client = runtime.qq
    assert client is not None  # main() 已检查
    locks: dict[int, asyncio.Lock] = {}

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
    runtime = build_runtime(approver=DenyApprover())
    if runtime.qq is None:
        raise SystemExit(
            "QQ 未启用：在 config/config.yaml 设置 qq.enabled: true，"
            "并按 docs/QQ_SETUP.md 启动 NapCat。"
        )
    if not runtime.config.qq.allowed_users:
        print("[QQ] 警告：allowed_users 为空，将不响应任何私聊（先配置你的 QQ 号）", flush=True)
    try:
        asyncio.run(_serve(runtime))
    except KeyboardInterrupt:
        print("\n[QQ] 再见。", flush=True)


if __name__ == "__main__":
    main()
