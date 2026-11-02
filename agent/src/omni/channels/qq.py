"""QQ 通道入口：uv run omni-qq（需先启动 NapCat 并在 config.yaml 开启 qq）。

职责纪律：只做「事件路由 + 白名单过滤 + 有界合并 + 会话绑定 + 回发」；
领域操作全部经 Runtime 门面。安装 NapCat 见 docs/QQ_SETUP.md。

安全边界（A03/A05 修复后的语义）：
- 私聊：`allowed_users` **为空时谁都不回**（空名单不等于不限）；名单外静默忽略（不暴露存在）；
- 群聊：白名单内精确 @，或显式启用的回复机器人策略；群聊无个人资料、无个人 review，
  工具采用许可名单，新增工具默认不开放；
- 会话按场景分开绑定：私聊 `dm:<user>`、群聊 `group:<group>`，同一发送者在两个群不会串历史；
- 策略每次从配置源重读：撤销白名单在下一条消息就生效，不必重启进程；
- 工具：QQ 上下文 DenyApprover（fail-closed），safe 级直通。
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import random
import time
from collections import deque
from typing import Any

from ..bootstrap import Runtime, build_runtime
from ..config import load_env
from ..core.session import SessionBusy
from ..llm.gateway import GatewayError
from ..safety.gates import DenyApprover, private_target_allowed
from ..system.service_state import StateWriter, state_path
from ..tools.qq.client import OneBotClient, chunk_message, clean_cq_message, cq_values
from .lifecycle import serve_until_stop

REPORT_INTERVAL_S = 10.0


def _event_id(event: dict) -> str | None:
    value = event.get("message_id")
    # OneBot message_id 为有符号整数，不能用 > 0 判断，也不能用缺失 ID 合成去重键。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return str(value)


def _resolve_scope(runtime: Runtime, client: OneBotClient, event: dict) -> tuple[str, int, bool] | None:
    """判定本条消息是否该处理：返回 (作用域键, 会话 key, 是否带个人上下文)。

    拒绝路径一律返回 None（静默），不区分"没授权"和"不存在"，避免探测。
    """
    policy = runtime.qq_policy()
    message_type = event.get("message_type")
    try:
        user_id = int(event.get("user_id") or 0)
        bot = int(event.get("self_id") or client.self_id or 0)
    except (TypeError, ValueError):
        return None
    if bot <= 0 or (client.self_id is not None and bot != client.self_id):
        return None
    if user_id <= 0:
        return None  # 伪造/异常事件
    if message_type == "private":
        if not private_target_allowed(policy.allowed_users, user_id).allowed:
            return None
        # 仅所有者本人的私聊注入所有者档案/常驻（owner_qq 未配置=一律不带，fail-closed）；
        # 其他白名单用户的私聊由 agent 按其人物卡注入（M6，见 core/context.py）
        personal = getattr(policy, "owner_qq", 0) > 0 and user_id == policy.owner_qq
        return "dm", user_id, personal
    if message_type == "group":
        try:
            group_id = int(event.get("group_id") or 0)
        except (TypeError, ValueError):
            return None
        if group_id <= 0 or group_id not in set(policy.allowed_groups):
            return None
        raw = str(event.get("raw_message") or "")
        mentioned = str(bot) in cq_values(raw, "at", "qq")
        inbox = getattr(runtime, "qq_messages", None)
        replied = (
            getattr(policy, "group_reply_to_bot", False)
            and inbox is not None
            and any(inbox.is_reply(bot, f"group:{group_id}", mid) for mid in cq_values(raw, "reply", "id"))
        )
        if not mentioned and not replied:
            return None
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


async def _handle_message(runtime: Runtime, client: OneBotClient, event: dict, locks: dict[str, asyncio.Lock]) -> None:
    """单事件入口；生产 dispatcher 在同一场景内合并后调用相同处理器。"""
    scope = _resolve_scope(runtime, client, event)
    inbox = getattr(runtime, "qq_messages", None)
    mid = _event_id(event)
    if scope is None or inbox is None or mid is None:
        return
    bot = int(event.get("self_id") or client.self_id)
    scene = f"{scope[0]}:{scope[1]}"
    if not inbox.claim(bot, scene, mid):
        return
    lock = locks.setdefault(scene, asyncio.Lock())
    async with lock:
        await _handle_batch(runtime, client, scope, [event])


def _cancel_session(runtime: Runtime, session_id: int, reason: str) -> None:
    active = getattr(runtime, "active_runs", None)
    if active is not None:
        for run in active.contexts():
            if run.identity.channel == "qq" and run.identity.session_id == session_id:
                run.cancel_token.cancel(reason)


async def _handle_batch(
    runtime: Runtime, client: OneBotClient, scope: tuple[str, int, bool], events: list[dict]
) -> None:
    scope_name, target_id, personal = scope
    scene = f"{scope_name}:{target_id}"
    bot = int(events[0].get("self_id") or client.self_id)
    inbox = runtime.qq_messages
    status = "suppressed"

    def valid(event: dict) -> bool:
        return _resolve_scope(runtime, client, event) == scope and inbox.active(bot, scene, _event_id(event))

    try:
        lo, hi = runtime.config.qq.reply_delay_s
        await asyncio.sleep(random.uniform(lo, hi))
        selected = [e for e in events if valid(e)]
        if not selected:
            return
        texts = [clean_cq_message(str(e.get("raw_message") or ""), self_id=bot) for e in selected]
        text = "\n".join(t for t in texts if t)
        if not text or len(text) > 16000:
            return
        sid = _session_for(runtime, scope_name, target_id)

        # 身份由可信通道提供，群话题历史与待办均绑定该场景的 session。
        # scope：私聊恒为 private（含非所有者——agent 侧据此注入人物卡）；群聊带群号
        def chat() -> str:
            return runtime.agent.chat(
                text,
                session_id=sid,
                personal_context=personal,
                channel="qq",
                principal=f"qq:{selected[0]['user_id']}",
                context_scope="private" if scope_name == "dm" else scene,
            )

        # 租约覆盖模型、回写与发送；线程真正结束前不能释放给另一进程。
        with runtime.session_leases.hold(sid) as token:
            worker = asyncio.create_task(asyncio.to_thread(chat))
            next_renew = time.monotonic() + runtime.session_leases.ttl_s / 3
            revoked = False
            try:
                while not worker.done():
                    await asyncio.wait({worker}, timeout=0.1)
                    if not all(valid(e) for e in selected):
                        revoked = True
                        _cancel_session(runtime, sid, "QQ 消息撤回或授权撤销")
                    if time.monotonic() >= next_renew:
                        if not runtime.state.renew_session_lease(token, ttl_s=runtime.session_leases.ttl_s):
                            revoked = True
                            _cancel_session(runtime, sid, "QQ 会话租约失效")
                        next_renew = time.monotonic() + runtime.session_leases.ttl_s / 3
                answer = await worker
            except asyncio.CancelledError:
                _cancel_session(runtime, sid, "QQ 通道关闭")
                await asyncio.shield(worker)
                raise
            except GatewayError:
                answer = "（模型调用失败，请稍后再试）"
            if revoked or not all(valid(e) for e in selected):
                return
            # 群消息不能作为所有者个人记忆的 review 输入。
            if personal:
                runtime.spawn_review(sid)
            for chunk in chunk_message(answer):
                if not all(valid(e) for e in selected):
                    return
                status = "unknown"  # 发送前登记语义，抛错也不能重放整批。
                # 路由按场景而非 personal 标志：非所有者私聊 personal=False，但必须仍发私聊
                ids = await (
                    client.send_private(target_id, chunk) if scope_name == "dm" else client.send_group(target_id, chunk)
                )
                inbox.remember_sent(bot, scene, ids or [])
            status = "done"
            print(f"[QQ] scene={scene} session={sid} events={len(selected)} replied_chars={len(answer)}", flush=True)
    except SessionBusy:
        return  # 另一进程在处理该话题，不偷偷并发或自动重放。
    finally:
        for event in events:
            inbox.finish(bot, scene, _event_id(event), status)


class QQDispatcher:
    """有界分场景队列；同一发送者短消息合并，撤回事件不等待模型。"""

    def __init__(self, runtime: Runtime, client: OneBotClient) -> None:
        self.runtime, self.client = runtime, client
        self.queues: dict[str, deque] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.limit = asyncio.Semaphore(runtime.config.qq.max_concurrent)
        self.pending = 0
        runtime._qq_dispatcher = self
        self.closed = False
        # 自报运行态（工作台「服务与运行」页读它）：只记账，不参与任何决策
        self.stats: dict[str, Any] = {"received": 0, "handled": 0, "dropped": 0, "last_message_at": ""}

    async def on_event(self, event: dict) -> None:
        if getattr(self.runtime, "updates", None) and self.runtime.updates.gate.active():
            return
        if self.closed:
            return
        inbox = self.runtime.qq_messages
        mid = _event_id(event)
        if inbox is None or mid is None:
            return
        try:
            bot = int(event.get("self_id") or self.client.self_id or 0)
            if bot <= 0 or (self.client.self_id is not None and bot != self.client.self_id):
                return
            if event.get("post_type") == "notice" and event.get("notice_type") in {"group_recall", "friend_recall"}:
                group = event["notice_type"] == "group_recall"
                key = int(event.get("group_id" if group else "user_id") or 0)
                policy = self.runtime.qq_policy()
                if key not in (policy.allowed_groups if group else policy.allowed_users):
                    return
                inbox.recall(bot, f"{'group' if group else 'dm'}:{key}", mid)
                return
            if event.get("post_type") != "message":
                return
            scope = _resolve_scope(self.runtime, self.client, event)
            if scope is None or len(str(event.get("raw_message") or "")) > 16000:
                return
            scene = f"{scope[0]}:{scope[1]}"
            if self.pending >= self.runtime.config.qq.max_pending or len(self.tasks) >= 32 and scene not in self.tasks:
                self.stats["dropped"] += 1
                print("[QQ] 场景队列已满，未接收新消息", flush=True)
                return
            if not inbox.claim(bot, scene, mid):
                return
            self.stats["received"] += 1
            self.stats["last_message_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            self.queues.setdefault(scene, deque()).append(event)
            self.pending += 1
            if scene not in self.tasks:
                self.tasks[scene] = asyncio.create_task(self._work(scene, scope))
        except (TypeError, ValueError):
            return

    async def _work(self, scene: str, scope: tuple[str, int, bool]) -> None:
        try:
            while self.queues[scene]:
                await asyncio.sleep(self.runtime.config.qq.merge_window_s)
                async with self.limit:
                    queue = self.queues[scene]
                    batch = [queue.popleft()]
                    size = len(str(batch[0].get("raw_message") or ""))
                    while (
                        queue
                        and len(batch) < 8
                        and queue[0]["user_id"] == batch[0]["user_id"]
                        and size + len(str(queue[0].get("raw_message") or "")) + 1 <= 16000
                    ):
                        batch.append(queue.popleft())
                        size += len(str(batch[-1].get("raw_message") or "")) + 1
                    try:
                        await _handle_batch(self.runtime, self.client, scope, batch)
                        self.stats["handled"] += len(batch)
                    except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
                        print(f"[QQ] 场景处理失败 scene={scene} type={type(exc).__name__}", flush=True)
                    finally:
                        self.pending -= len(batch)
        finally:
            queue = self.queues.pop(scene, deque())
            self.pending -= len(queue)
            self.tasks.pop(scene, None)

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _serve(runtime: Runtime, launch_id: str) -> None:
    client = runtime.qq
    assert client is not None  # main() 已检查
    dispatcher = QQDispatcher(runtime, client)
    writer = StateWriter(state_path(runtime.paths.run, "omni-qq"), service="omni-qq", launch_id=launch_id)

    async def _report() -> None:
        while True:
            connected = bool(client.connected)
            writer.write(
                health="ok" if connected else "degraded",
                note="" if connected else f"未连上 NapCat（{runtime.config.qq.ws_url}）",
                stats={
                    "connected": connected,
                    "pending": dispatcher.pending,
                    "in_flight": len(dispatcher.tasks),
                    **dispatcher.stats,
                },
            )
            await asyncio.sleep(REPORT_INTERVAL_S)

    report = asyncio.create_task(_report())
    try:
        await client.run(dispatcher.on_event)
    finally:
        report.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await report
        await dispatcher.close()


def main() -> None:
    from ..system.observability import configure_service_logging

    configure_service_logging()
    load_env()
    runtime = build_runtime(approver=DenyApprover(), owner_kind="qq")
    # pid 目录来自 RuntimePaths：与运行数据根同源，不再另拼全局路径
    launch_id = runtime.services.register_current("omni-qq")
    try:
        if runtime.qq is None:
            raise SystemExit(
                "QQ 未启用：在 config/config.yaml 设置 qq.enabled: true，并按 docs/QQ_SETUP.md 启动 NapCat。"
            )
        policy = runtime.qq_policy()
        if not policy.allowed_users:
            print(
                "[QQ] 警告：allowed_users 为空，将不响应任何私聊、也不投递任何主动消息（先配置你的 QQ 号）",
                flush=True,
            )
        if policy.allowed_groups:
            print(
                f"[QQ] 已授权群 {policy.allowed_groups}：按 @/回复策略响应，不注入个人档案",
                flush=True,
            )
        asyncio.run(serve_until_stop(runtime, "omni-qq", launch_id, _serve(runtime, launch_id)))
    finally:
        result = runtime.close()
        if not result["complete"]:
            print(f"[QQ] 关闭残留：{result['residual']}", flush=True)
        if result["complete"]:
            runtime.services.unregister_current("omni-qq", launch_id)


if __name__ == "__main__":
    main()
