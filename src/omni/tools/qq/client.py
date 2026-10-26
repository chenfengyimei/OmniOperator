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
    def __init__(self, ws_url: str, access_token: str = "") -> None:
        self._ws_url = ws_url
        self._token = access_token
        self._ws: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._echo = itertools.count(1)
        self.self_id: int | None = None  # 机器人自己的 QQ 号（群 @ 识别用）

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def run(self, on_event: Callable[[dict], Awaitable[None]]) -> None:
        """连接 + 断线自动重连 + 事件分发（阻塞直到取消）。"""
        import websockets  # 懒导入

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        backoff = 2.0
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
                        await self._dispatch(raw, on_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 —— 网络抖动一律重连
                print(f"[QQ] 连接断开（{exc}），{backoff:.0f}s 后重连…", flush=True)
            finally:
                self._ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.7, 60.0)

    async def _dispatch(
        self, raw: str, on_event: Callable[[dict], Awaitable[None]]
    ) -> None:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        if event.get("post_type") == "meta_event":
            # lifecycle 事件带机器人自身 QQ 号
            if event.get("meta_event_type") == "lifecycle" and event.get("self_id"):
                self.self_id = int(event["self_id"])
                print(f"[QQ] 机器人 QQ 号：{self.self_id}", flush=True)
            return
        await on_event(event)

    # ---------- 发送（async 上下文用） ----------

    async def call(self, action: str, params: dict) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionError("QQ 未连接（NapCat 未启动？）")
        payload = {"action": action, "params": params, "echo": next(self._echo)}
        await ws.send(json.dumps(payload, ensure_ascii=False))

    async def send_private(self, user_id: int, text: str) -> None:
        for chunk in chunk_message(text):
            await self.call("send_private_msg", {"user_id": int(user_id), "message": chunk})

    async def send_group(self, group_id: int, text: str) -> None:
        for chunk in chunk_message(text):
            await self.call("send_group_msg", {"group_id": int(group_id), "message": chunk})

    # ---------- 发送（同步线程用：agent 工具在 asyncio.to_thread 里） ----------

    def send_private_sync(self, user_id: int, text: str, timeout: float = 15.0) -> str:
        return self._sync(self.send_private(user_id, text), timeout)

    def send_group_sync(self, group_id: int, text: str, timeout: float = 15.0) -> str:
        return self._sync(self.send_group(group_id, text), timeout)

    def _sync(self, coro: Awaitable[None], timeout: float) -> str:
        if not self.connected or self._loop is None:
            coro.close()  # 释放未投递的协程，避免 RuntimeWarning
            return "错误：QQ 未连接（NapCat 未启动或通道未启用）"
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            future.result(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 —— 发送失败回传给 LLM
            return f"发送失败：{exc}"
        return "已发送"
