"""QQ 能力域单测：CQ 清洗 / 分段 / 通道绑定 / 客户端发送 / 工具白名单 / 会话覆盖。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from omni.config import AppConfig
from omni.core.agent import Agent
from omni.memory.store import MemoryStore
from omni.tools.qq.client import (
    OneBotClient,
    chunk_message,
    clean_cq_message,
    is_loopback_url,
)
from omni.tools.qq.tools import register_qq_tools
from omni.tools.registry import ToolRegistry


def test_clean_cq_message():
    raw = "[CQ:reply,id=123][CQ:at,qq=10001] 看看这个 [CQ:image,file=x.jpg,url=https://a] 还有[CQ:face,id=14]"
    # @机器人本体剔除（触发词）；图片/表情转占位；引用标记剔除
    assert clean_cq_message(raw, self_id=10001) == "看看这个 [图片] 还有[表情]"
    # @别人保留为 @QQ号
    assert clean_cq_message("[CQ:at,qq=20002]帮我", self_id=10001) == "@20002 帮我"
    assert clean_cq_message("  普通文本  ") == "普通文本"


def test_chunk_message():
    assert chunk_message("短消息") == ["短消息"]
    assert chunk_message("") == []
    paras = "\n\n".join(f"第{i}段，" + "字" * 60 for i in range(150))
    chunks = chunk_message(paras, limit=2800)
    assert len(chunks) >= 3
    assert all(len(c) <= 2800 for c in chunks)
    assert "".join(chunks).count("第0段") == 1  # 内容不丢不重
    # 无换行长文：硬切
    hard = chunk_message("x" * 6000, limit=2800)
    assert [len(c) for c in hard] == [2800, 2800, 400]
    assert "".join(hard) == "x" * 6000


def test_channel_bindings(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    a1 = store.get_or_create_binding("qq", "10001")
    a2 = store.get_or_create_binding("qq", "10001")
    other = store.get_or_create_binding("qq", "20002")
    assert a1 == a2 and other != a1
    assert store.get_binding("qq", "10001") == a1
    assert store.get_binding("qq", "99999") is None
    store.set_binding("qq", "10001", other)
    assert store.get_binding("qq", "10001") == other
    # 会话标题带通道前缀，Web 工作台里能认出来
    titles = {sid: title for sid, title, _ in store.list_sessions(50)}
    assert titles[a1] == "qq:10001"
    store.close()


def test_client_send_chunks_via_fake_ws():
    client = OneBotClient("ws://x")
    # 未连接时同步发送返回可读错误（不抛异常）
    assert client.send_private_sync(123, "hi").startswith("错误")

    sent: list[dict] = []

    class FakeWS:
        async def send(self, data):
            payload = json.loads(data)
            sent.append(payload)
            await client._dispatch(
                json.dumps({"echo": payload["echo"], "status": "ok", "retcode": 0, "data": {"message_id": 1}}),
                lambda _: None,
            )

    async def scenario():
        client._ws = FakeWS()
        client._loop = asyncio.get_running_loop()
        await client.send_private(123, "短消息")
        long_text = "\n\n".join(f"第{i}段，" + "好" * 80 for i in range(150))
        await client.send_private(123, long_text)

    asyncio.run(scenario())
    assert sent[0]["action"] == "send_private_msg"
    assert sent[0]["params"]["user_id"] == 123
    assert sent[0]["params"]["message"] == "短消息"
    assert len(sent) > 2  # 长消息自动分段
    assert all(len(s["params"]["message"]) <= 2800 for s in sent)


def test_api_receipt_timeout_failure_and_partial_delivery():
    import pytest
    from omni.tools.qq.client import DeliveryNotStarted, DeliveryUnknown

    async def scenario():
        client = OneBotClient("ws://test", response_timeout_s=0.02)
        sent = []

        class SilentWS:
            async def send(self, raw):
                sent.append(json.loads(raw))

        client._ws = SilentWS()
        with pytest.raises(DeliveryUnknown, match="回执超时"):
            await client.send_private(1, "hello")
        assert len(sent) == 1 and not client._pending

        class RejectedWS:
            async def send(self, raw):
                request = json.loads(raw)
                await client._dispatch(json.dumps({"echo": request["echo"], "status": "failed", "retcode": 100}), None)

        client._ws = RejectedWS()
        with pytest.raises(DeliveryNotStarted):
            await client.send_private(1, "hello")

        class PartialWS:
            async def send(self, raw):
                request = json.loads(raw)
                await client._dispatch(json.dumps({"echo": request["echo"], "status": "ok", "retcode": 0}), None)
                client._ws = None

        client._ws = PartialWS()
        with pytest.raises(DeliveryUnknown, match="分段"):
            await client.send_private(1, "a" * 2900)

    asyncio.run(scenario())


def test_receiver_reads_receipt_while_event_handler_waits():
    import websockets

    async def scenario():
        replied = asyncio.Event()

        async def server(ws):
            await ws.send(json.dumps({"post_type": "message", "raw_message": "hello"}))
            request = json.loads(await ws.recv())
            await ws.send(
                json.dumps({"echo": request["echo"], "status": "ok", "retcode": 0, "data": {"message_id": 10}})
            )
            await replied.wait()

        async with websockets.serve(server, "127.0.0.1", 0) as listener:
            port = listener.sockets[0].getsockname()[1]
            client = OneBotClient(f"ws://127.0.0.1:{port}", response_timeout_s=2)

            async def handler(event):
                await client.send_private(1, "reply")
                replied.set()

            task = asyncio.create_task(client.run(handler))
            try:
                await asyncio.wait_for(replied.wait(), 5)
                assert not client._pending
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_qq_send_message_tool_guards():
    registry = ToolRegistry()
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[10001])
    # 白名单外直接拒绝（不触达网络）
    assert "白名单" in registry.dispatch("qq_send_message", {"user_id": 999, "text": "hi"})
    # 空文本
    assert "不能为空" in registry.dispatch("qq_send_message", {"user_id": 10001, "text": "  "})
    # 白名单内但 QQ 未连接 → 明确的未连接提示
    assert "未连接" in registry.dispatch("qq_send_message", {"user_id": 10001, "text": "hi"})


def test_qq_send_message_tool_structured_results():
    """发送按回执分三类：已发送(applied)/结果未知(unknown)/确定未发出(可重试)。"""
    from omni.tools.qq.client import DeliveryNotStarted, DeliveryUnknown, QQNotConnected

    class FakeClient:
        def __init__(self, exc=None, ids=(11, 22)):
            self._exc = exc
            self._ids = list(ids)

        def send_private_outcome(self, user_id, text, timeout=15.0):
            if self._exc is not None:
                raise self._exc
            return self._ids

    def build(exc=None, ids=(11, 22)):
        registry = ToolRegistry()
        register_qq_tools(registry, FakeClient(exc, ids), allowed_users=[10001])
        return registry

    ok = build().execute("qq_send_message", {"user_id": 10001, "text": "hi"})
    assert ok.status == "ok" and ok.side_effect_state == "applied"
    assert ok.text == "已发送" and ok.data["message_ids"] == [11, 22]

    unknown = build(DeliveryUnknown("QQ 回执超时，禁止自动重发")).execute(
        "qq_send_message", {"user_id": 10001, "text": "hi"}
    )
    # 旧字符串时代这条会被前缀推断误判成 ok（不以"错误"开头）
    assert unknown.status == "unknown" and unknown.side_effect_state == "unknown"
    assert "不要自动重试" in unknown.text and unknown.legacy is False

    refused = build(DeliveryNotStarted("QQ 接口拒绝（retcode=100）")).execute(
        "qq_send_message", {"user_id": 10001, "text": "hi"}
    )
    assert refused.status == "error" and refused.retryable is True
    assert refused.side_effect_state == "none" and "未发出" in refused.text

    offline = ToolRegistry()
    register_qq_tools(offline, OneBotClient("ws://x"), allowed_users=[10001])
    result = offline.execute("qq_send_message", {"user_id": 10001, "text": "hi"})
    assert result.status == "error" and result.error_code == "qq_not_connected"
    assert result.text.startswith("错误：QQ 未连接") and result.side_effect_state == "none"
    assert isinstance(QQNotConnected("x"), RuntimeError)


def test_qq_send_message_sync_wrappers_keep_legacy_strings():
    """兼容入口的文本逐字不变：旧调用点与既有断言零改动。"""
    client = OneBotClient("ws://x")
    assert client.send_private_sync(123, "hi").startswith("错误")
    assert client.send_private_sync(123, "hi") == "错误：QQ 未连接（NapCat 未启动或通道未启用）"


class FixedGateway:
    """固定回复的假模型。"""

    def __init__(self) -> None:
        self.n = 0

    def resolve(self, route: str) -> str:
        return "fake/model"

    def complete(self, messages, *, model, tools=None, **kwargs):
        self.n += 1
        return SimpleNamespace(text=f"回复{self.n}", tool_calls=[])


def test_agent_session_override(tmp_path):
    """多通道并发核心保障：显式 session_id 不受 self.session_id 切换影响。"""
    store = MemoryStore(tmp_path / "m.db")
    sid_a = store.new_session("A")
    sid_b = store.new_session("B")
    agent = Agent(AppConfig(), FixedGateway(), store, ToolRegistry(), sid_a)
    assert agent.chat("一", session_id=sid_a) == "回复1"
    assert agent.chat("二", session_id=sid_b) == "回复2"
    assert agent.chat("三") == "回复3"  # 默认走 self.session_id（=A）
    assert len(store.recent_messages(sid_a, 10)) == 4  # A：两轮问答
    assert len(store.recent_messages(sid_b, 10)) == 2  # B：一轮问答
    store.close()


def test_qq_empty_allowlist_denies_all_sends():
    registry = ToolRegistry()
    register_qq_tools(registry, OneBotClient("ws://x"), allowed_users=[])
    assert "白名单" in registry.dispatch("qq_send_message", {"user_id": 10001, "text": "hi"})

def test_is_loopback_url_recognises_local_targets():
    """回环判定只有一份：127.0.0.1/localhost/::1 算本机，其余不算。"""
    assert is_loopback_url("ws://127.0.0.1:3001")
    assert is_loopback_url("ws://localhost:3001")
    assert is_loopback_url("ws://[::1]:3001")
    assert is_loopback_url("ws://LOCALHOST:3001")
    # 远程 NapCat / 其它主机不算回环
    assert not is_loopback_url("ws://192.168.1.10:3001")
    assert not is_loopback_url("ws://napcat.example.com:3001")
    assert not is_loopback_url("ws://x")


def test_qq_loopback_connection_ignores_environment_proxy(monkeypatch):
    """回环 NapCat 不走环境代理。

    复现真机故障链：进程环境里有 HTTP_PROXY（IDE 的透明代理）时，
    websockets>=17.3 默认 proxy=True 会把本机 NapCat 的连接也塞进代理隧道；
    代理拒绝对回环目标时抛 InvalidProxyStatus 并反复重连，心跳调度日志
    里还会刷一串 transport 回调里的 EOFError 堆栈。
    """
    import websockets

    seen: list[object] = []

    class Recorder:
        """记录传给 connect 的 proxy 参数，不真正建连。"""

        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("proxy"))

        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(websockets, "connect", Recorder)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")

    async def scenario():
        client = OneBotClient("ws://127.0.0.1:3001")
        with pytest.raises(asyncio.CancelledError):
            await client.run(lambda _e: asyncio.sleep(0))

    asyncio.run(scenario())
    assert seen and seen[0] is None, "回环目标必须显式禁用代理"


def test_qq_remote_target_keeps_system_proxy_behaviour(monkeypatch):
    """远程 NapCat 仍按系统配置走代理，不因本次修复被改掉。"""
    import websockets

    seen: list[object] = []

    class Recorder:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs.get("proxy"))

        async def __aenter__(self):
            raise asyncio.CancelledError

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(websockets, "connect", Recorder)

    async def scenario():
        client = OneBotClient("ws://napcat.example.com:3001")
        with pytest.raises(asyncio.CancelledError):
            await client.run(lambda _e: asyncio.sleep(0))

    asyncio.run(scenario())
    assert seen and seen[0] is True, "非回环目标保持系统代理探测"
