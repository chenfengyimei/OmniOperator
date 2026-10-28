"""OneBot v11 正向 WebSocket 客户端（对接 NapCat）。

职责：连接/自动重连、把事件回调给上层、把 API 调用发出去；
消息清洗（CQ 码）与分段是纯函数，可单测。

线程模型：run() 跑在 asyncio 事件循环里；agent 的工具跑在线程池里，
通过 send_*_sync（run_coroutine_threadsafe）跨线程投递发送请求。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections.abc import Awaitable, Callable

# QQ 单条消息安全长度（经验值，留余量防风控）
CHUNK_LIMIT = 2800


class DeliveryNotStarted(ConnectionError):
    """可证明本次整条消息未发送，允许有界重试。"""


class DeliveryUnknown(RuntimeError):
    """可能已发送或部分发送，不能自动重放。"""


class QQNotConnected(RuntimeError):
    """通道未连接：发送动作确定没有发生（与"结果未知"严格区分）。"""

_CQ_RE = re.compile(r"\[CQ:(\w+)(?:,([^\]]*))?\]")
_CQ_TEXT = {
    "image": "[图片]",
    "face": "[表情]",
    "bface": "[表情]",
    "mface": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "json": "[卡片]",
    "xml": "[卡片]",
}


def cq_values(text: str, kind: str, key: str) -> list[str]:
    """完整解析 CQ 属性，不把 qq=1234 错认作 qq=123。"""
    result = []
    for match in _CQ_RE.finditer(text):
        if match.group(1) != kind:
            continue
        pairs = [part.split("=", 1) for part in (match.group(2) or "").split(",")]
        values = [p[1] for p in pairs if len(p) == 2 and p[0] == key]
        if len(values) == 1:
            result.extend(values)
    return result


def clean_cq_message(text: str, self_id: int | None = None) -> str:
    """CQ 码清洗：@机器人本体剔除（触发词），其它转可读占位。"""

    def repl(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        attrs = match.group(2) or ""
        if kind == "at":
            qq = re.search(r"qq=(\d+)", attrs)
            if qq and self_id and int(qq.group(1)) == int(self_id):
                return ""  # @机器人：触发词，不算内容
            return f"@{qq.group(1) if qq else '?'} "  # 提及后补空格更自然
        if kind == "reply":
            return ""  # 引用回复标记：剔除
        return _CQ_TEXT.get(kind, "")

    return _CQ_RE.sub(repl, text).strip()


def chunk_message(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """长消息分段：优先在段落边界切，超限无边界才硬切。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:  # 段落边界太靠前就没意义，硬切
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return chunks


class OneBotClient:
    def __init__(self, ws_url: str, access_token: str = "", *, response_timeout_s: float = 10) -> None:
        self._ws_url = ws_url
        self._token = access_token
        self._ws: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._echo = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._response_timeout_s = response_timeout_s
        self.self_id: int | None = None  # 机器人自己的 QQ 号（群 @ 识别用）

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def run(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        """连接 + 断线自动重连 + 事件分发（阻塞直到取消）。"""
        import websockets  # 懒导入

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        backoff = 2.0
        events: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)

        async def enqueue(event: dict) -> None:
            try:
                events.put_nowait(event)
            except asyncio.QueueFull:
                print("[QQ] 入站队列已满，本条事件未接收", flush=True)

        async def consume() -> None:
            while True:
                event = await events.get()
                try:
                    await on_event(event)
                except Exception as exc:
                    print(f"[QQ] 事件处理失败（{type(exc).__name__}）", flush=True)
                finally:
                    events.task_done()

        # 接收循环必须持续读取 API 回执；不能被正在等待回执的聊天处理器阻塞。
        consumer = asyncio.create_task(consume())
        try:
            while True:
                try:
                    async with websockets.connect(
                        self._ws_url, additional_headers=headers, max_size=2**22
                    ) as ws:
                        self._ws = ws
                        self._loop = asyncio.get_running_loop()
                        backoff = 2.0
                        print(f"[QQ] 已连接 {self._ws_url}", flush=True)
                        async for raw in ws:
                            await self._dispatch(raw, enqueue)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(f"[QQ] 连接断开（{type(exc).__name__}），{backoff:.0f}s 后重连…", flush=True)
                finally:
                    self._ws = None
                    for future in list(self._pending.values()):
                        if not future.done():
                            future.set_exception(DeliveryUnknown("QQ 断线，回执未知"))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.7, 60.0)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    async def _dispatch(
        self, raw: str | bytes, on_event: Callable[[dict], Awaitable[None]]
    ) -> None:
        # websockets 的帧可能是 str 或 bytes；json.loads 两者都收（bytes 自动识别编码）
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        if "echo" in event and "post_type" not in event:
            try:
                future = self._pending.get(int(event["echo"]))
            except (TypeError, ValueError):
                return
            if future is not None and not future.done():
                if event.get("status") == "ok" and event.get("retcode") == 0:
                    future.set_result(event.get("data") or {})
                elif event.get("status") == "failed":
                    future.set_exception(DeliveryNotStarted(f"QQ 接口拒绝（retcode={event.get('retcode')}）"))
                else:
                    future.set_exception(DeliveryUnknown("QQ 未返回确定成功的回执"))
            return
        if event.get("post_type") == "meta_event":
            # lifecycle 事件带机器人自身 QQ 号
            if event.get("meta_event_type") == "lifecycle" and event.get("self_id"):
                self.self_id = int(event["self_id"])
                print(f"[QQ] 机器人 QQ 号：{self.self_id}", flush=True)
            return
        await on_event(event)

    # ---------- 发送（async 上下文用） ----------

    async def call(self, action: str, params: dict) -> dict:
        ws = self._ws
        if ws is None:
            raise DeliveryNotStarted("QQ 未连接（NapCat 未启动？）")
        echo = next(self._echo)
        future = asyncio.get_running_loop().create_future()
        self._pending[echo] = future
        try:
            try:
                await ws.send(json.dumps({"action": action, "params": params, "echo": echo}, ensure_ascii=False))
            except Exception as exc:
                raise DeliveryUnknown("QQ 写入连接时失败，是否发送未知") from exc
            try:
                return await asyncio.wait_for(asyncio.shield(future), self._response_timeout_s)
            except TimeoutError as exc:
                raise DeliveryUnknown("QQ 回执超时，禁止自动重发") from exc
        finally:
            self._pending.pop(echo, None)
            if not future.done():
                future.cancel()

    async def _send_chunks(self, action: str, target_key: str, target: int, text: str) -> list[int]:
        sent_ids: list[int] = []
        for index, chunk in enumerate(chunk_message(text)):
            try:
                receipt = await self.call(action, {target_key: int(target), "message": chunk, "auto_escape": True})
                if isinstance(receipt.get("message_id"), int):
                    sent_ids.append(receipt["message_id"])
            except DeliveryNotStarted as exc:
                if index:
                    raise DeliveryUnknown("已有分段发送成功，后续失败；不能重发整条消息") from exc
                raise
        return sent_ids

    async def send_private(self, user_id: int, text: str) -> list[int]:
        return await self._send_chunks("send_private_msg", "user_id", user_id, text)

    async def send_group(self, group_id: int, text: str) -> list[int]:
        return await self._send_chunks("send_group_msg", "group_id", group_id, text)

    # ---------- 发送（同步线程用：agent 工具在 asyncio.to_thread 里） ----------

    def send_private_outcome(self, user_id: int, text: str, timeout: float = 15.0) -> list[int]:
        """同步私聊发送：成功返回回执 message_id 列表，失败抛类型化异常。"""
        return self._send_threadsafe(self.send_private(user_id, text), timeout)

    def send_group_outcome(self, group_id: int, text: str, timeout: float = 15.0) -> list[int]:
        return self._send_threadsafe(self.send_group(group_id, text), timeout)

    def _send_threadsafe(self, coro: Awaitable[list[int]], timeout: float) -> list[int]:
        if not self.connected or self._loop is None:
            coro.close()  # 释放未投递的协程，避免 RuntimeWarning
            raise QQNotConnected("QQ 未连接（NapCat 未启动或通道未启用）")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def send_private_sync(self, user_id: int, text: str, timeout: float = 15.0) -> str:
        """兼容入口：文本结果与历史版本逐字一致（新代码用 send_private_outcome）。"""
        try:
            self.send_private_outcome(user_id, text, timeout)
        except QQNotConnected as exc:
            return f"错误：{exc}"
        except Exception as exc:  # noqa: BLE001 —— 失败按旧契约回传文本
            return f"发送未确认（不要自动重试）：{exc}"
        return "已发送"

    def send_group_sync(self, group_id: int, text: str, timeout: float = 15.0) -> str:
        try:
            self.send_group_outcome(group_id, text, timeout)
        except QQNotConnected as exc:
            return f"错误：{exc}"
        except Exception as exc:  # noqa: BLE001
            return f"发送未确认（不要自动重试）：{exc}"
        return "已发送"
