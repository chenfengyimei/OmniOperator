"""D17 投递 outbox：掉线不丢消息、恢复自动补投、unknown 不自动重发。

验收口径（DEEPSEEK_DEVELOPMENT_OUTLINE D17）：
- QQ 未连接 → 明确没发 → 入队 pending，心跳恢复后自动补投；
- 白名单撤销 / QQ 未启用 → 用户意图，**不入队**；
- 发送中途抛异常 → 结果未知（可能已发出一部分）→ 入队 unknown，不自动重发；
- 同一条消息 (target, content) 活跃期内只排一次队；进入终态后可重新入队；
- 补投重试有上限（3 次 → dead）；「已投递」不能被丢弃或重排。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import subprocess
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from omni.bootstrap import build_runtime
from omni.config import QQ
from omni.core.qq_policy import proactive_decision
from omni.channels.web import create_app
from omni.core.cron import make_outbox_drainer, make_runtime_deliver
from omni.memory.cron import Job
from omni.memory.state import OutboxStore, StateStore
from omni.safety.gates import DenyApprover
from omni.tools.qq.client import DeliveryNotStarted


def test_atomic_claim_across_processes_and_stale_owner(tmp_path):
    state, outbox = _store(tmp_path)
    entry_id = outbox.enqueue(job="j", target_qq=10001, content="一次发送")
    code = """
import sys
from omni.memory.state import StateStore, OutboxStore
s = StateStore(sys.argv[1])
item = OutboxStore(s).claim_next()
print(item['id'] if item else 0)
s.close()
"""
    processes = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "state.db")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(4)]
    claims = []
    for process in processes:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, error
        claims.append(int(output.strip()))
    assert claims.count(entry_id) == 1
    row = outbox.get(entry_id)
    assert not outbox.retry(entry_id) and not outbox.discard(entry_id)
    assert not outbox.finish_claim(entry_id, "wrong-token", "sent")
    assert outbox.claim_next(now=dt.datetime.now().astimezone() + dt.timedelta(minutes=6)) is None
    assert outbox.get(entry_id)["status"] == "unknown"
    assert not outbox.finish_claim(entry_id, row["claim_token"], "sent")
    assert outbox.pending() == []
    state.close()


def test_drain_does_not_retry_same_message_within_tick(tmp_path):
    rt, outbox, state = _runtime(tmp_path, FakeQQ(fail_with=DeliveryNotStarted("offline")))
    for content in ("first", "second", "third"):
        outbox.enqueue(job="j", target_qq=10001, content=content)
    stats = asyncio.run(make_outbox_drainer(rt)())
    assert stats["retry"] == 3
    assert [r["attempts"] for r in outbox.list()] == [1, 1, 1]
    state.close()


def test_raw_connection_error_is_unknown(tmp_path):
    rt, outbox, state = _runtime(tmp_path, FakeQQ(fail_with=ConnectionError("partial write")))
    outbox.enqueue(job="j", target_qq=10001, content="hello")
    assert asyncio.run(make_outbox_drainer(rt)())["unknown"] == 1
    assert not outbox.pending()
    state.close()


def test_online_send_has_claim_before_network_and_cancel_is_unknown(tmp_path):
    rt, outbox, state = _runtime(tmp_path, FakeQQ())
    async def send(target, content):
        row = outbox.list()[0]
        assert row["status"] == "sending" and row["claim_token"]
        raise asyncio.CancelledError()
    rt.qq.send_private = send
    import pytest
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(make_runtime_deliver(rt)(_job(), "content"))
    assert outbox.list()[0]["status"] == "unknown"
    state.close()


def _store(tmp_path) -> tuple[StateStore, OutboxStore]:
    state = StateStore(tmp_path / "state.db")
    return state, OutboxStore(state)


def _job(deliver_to_qq: int | None = 10001, name: str = "morning-report") -> Job:
    return Job(name=name, prompt="写早报", every_minutes=30, deliver_to_qq=deliver_to_qq)


# ---------------------------------------------------------------- 存储语义


def test_enqueue_dedup_and_release_after_terminal(tmp_path):
    state, outbox = _store(tmp_path)
    first = outbox.enqueue(job="j", target_qq=10001, content="报告A", error="QQ 未连接")
    assert first is not None
    # 活跃期内同 (target, content) 去重
    assert outbox.enqueue(job="j", target_qq=10001, content="报告A") is None
    # 不同内容 / 不同目标不受去重影响
    second = outbox.enqueue(job="j", target_qq=10001, content="报告B")
    third = outbox.enqueue(job="j", target_qq=10002, content="报告A")
    assert second is not None and third is not None

    # 终态释放去重名额：sent 之后同内容可以重新入队
    assert outbox.mark(first, "sent")
    again = outbox.enqueue(job="j", target_qq=10001, content="报告A")
    assert again is not None

    rows = outbox.list()
    assert len(rows) == 4
    assert any(r["status"] == "sent" and r["sent_at"] for r in rows)
    # 活跃条目排在终态前面
    statuses = [r["status"] for r in rows]
    assert statuses.index("sent") > max(
        i for i, s in enumerate(statuses) if s in ("pending", "unknown")
    )
    state.close()


def test_pending_respects_attempts_and_retry_discard(tmp_path):
    state, outbox = _store(tmp_path)
    entry = outbox.enqueue(job="j", target_qq=10001, content="报告")
    assert [e["id"] for e in outbox.pending()] == [entry]
    assert [e["id"] for e in outbox.pending(max_attempts=1)] == [entry]

    # 一次失败尝试后：默认预算（3）里还在，预算 1 的视角里已经出局
    assert outbox.attempt(entry, error="补投失败") == 1
    assert [e["id"] for e in outbox.pending()] == [entry]
    assert outbox.pending(max_attempts=1) == []

    assert outbox.attempt(entry, error="又失败") == 2
    assert outbox.attempt(entry, error="第三次失败") == 3
    assert outbox.pending() == []  # 尝试耗尽 → 不再自动补投

    # dead → retry 重新给满预算
    assert outbox.mark(entry, "dead", error="补投失败 3 次")
    assert outbox.retry(entry)
    revived = outbox.get(entry)
    assert revived["status"] == "pending" and revived["attempts"] == 0

    # sent 是终态里的终态：不能 retry、不能 discard
    assert outbox.mark(entry, "sent")
    assert outbox.retry(entry) is False
    assert outbox.discard(entry) is False
    # discard 只对未投递的条目生效
    other = outbox.enqueue(job="j", target_qq=10001, content="另一条")
    assert outbox.discard(other)
    assert outbox.get(other)["status"] == "discarded"
    state.close()


def test_enqueue_cap(tmp_path):
    state, outbox = _store(tmp_path)
    for i in range(OutboxStore.MAX_LIVE):
        assert outbox.enqueue(job="j", target_qq=10001, content=f"报告{i}") is not None
    try:
        outbox.enqueue(job="j", target_qq=10001, content="超限的一条")
        raised = False
    except ValueError as exc:
        raised = "已满" in str(exc)
    assert raised
    # 终态一条就能腾出名额
    outbox.mark(outbox.list()[0]["id"], "discarded")
    assert outbox.enqueue(job="j", target_qq=10001, content="超限的一条") is not None
    state.close()


# ---------------------------------------------------------------- 投递分类


class FakeQQ:
    def __init__(self, connected: bool = True, fail_with: Exception | None = None) -> None:
        self.connected = connected
        self._fail_with = fail_with
        self.sent: list[tuple[int, str]] = []

    async def send_private(self, user_id: int, text: str) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.sent.append((user_id, text))


def _runtime(tmp_path, qq, allowed=(10001,)):
    state, outbox = _store(tmp_path)
    return (
        SimpleNamespace(
            qq=qq,
            qq_policy=lambda: QQ(allowed_users=list(allowed), proactive_enabled=True, owner_qq=10001,
                                 quiet_start="00:00", quiet_end="00:00", proactive_daily_limit=50),
            state=state,
            outbox=outbox,
        ),
        outbox,
        state,
    )


def test_proactive_requires_opt_in_owner_and_quiet_hours():
    policy = QQ(allowed_users=[1, 2], owner_qq=1)
    noon = dt.datetime(2026, 10, 28, 4, tzinfo=dt.timezone.utc)
    assert proactive_decision(policy, 1, now=noon).state == "deny"
    policy.proactive_enabled = True
    assert proactive_decision(policy, 2, now=noon).state == "deny"
    assert proactive_decision(policy, 1, now=noon).state == "allow"
    assert proactive_decision(policy, 1, now=noon + dt.timedelta(hours=10)).state == "defer"
    assert proactive_decision(policy, 1, now=noon - dt.timedelta(hours=5)).state == "defer"
    policy.timezone = "Moon/Base"
    assert proactive_decision(policy, 1, now=noon).state == "deny"


def test_daily_budget_defers_without_spending_network_attempts(tmp_path):
    rt, outbox, state = _runtime(tmp_path, FakeQQ())
    policy = rt.qq_policy()
    policy.proactive_daily_limit = 1
    rt.qq_policy = lambda: policy
    for text in ("first", "second"):
        outbox.enqueue(job="nightly", target_qq=10001, content=text)
    stats = asyncio.run(make_outbox_drainer(rt)())
    assert stats["sent"] == 1 and stats["deferred"] == 1
    assert outbox.pending()[0]["attempts"] == 0
    assert asyncio.run(make_outbox_drainer(rt)())["deferred"] == 1
    assert outbox.pending()[0]["attempts"] == 0 and len(rt.qq.sent) == 1
    policy.proactive_enabled = False
    assert asyncio.run(make_outbox_drainer(rt)())["discarded"] == 1
    state.close()


def test_revocation_between_chunks_stops_with_unknown(tmp_path):
    rt, outbox, state = _runtime(tmp_path, FakeQQ())
    policy = rt.qq_policy()
    rt.qq_policy = lambda: policy
    sent = []
    async def send(target, text):
        sent.append(text)
        policy.proactive_enabled = False
    rt.qq.send_private = send
    result = asyncio.run(make_runtime_deliver(rt)(_job(), "x" * 4000))
    assert "未知" in result and len(sent) == 1
    assert outbox.list()[0]["status"] == "unknown"
    state.close()


def test_manual_retry_conflicting_live_content_is_refused(tmp_path):
    state, outbox = _store(tmp_path)
    old = outbox.enqueue(job="j", target_qq=1, content="same")
    assert outbox.discard(old)
    outbox.enqueue(job="j", target_qq=1, content="same")
    assert not outbox.retry(old)
    state.close()


def test_deliver_offline_enqueues_pending(tmp_path):
    qq = FakeQQ(connected=False)
    rt, outbox, state = _runtime(tmp_path, qq)
    deliver = make_runtime_deliver(rt)
    detail = asyncio.run(deliver(_job(), "早报内容"))
    assert "已入待投递队列" in detail and "QQ 未连接" in detail
    rows = outbox.list()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["content"] == "早报内容" and rows[0]["job"] == "morning-report"
    # 同内容再投一次：去重，不重复排队
    detail2 = asyncio.run(deliver(_job(), "早报内容"))
    assert "去重" in detail2 and len(outbox.list()) == 1
    state.close()


def test_deliver_revoked_and_disabled_do_not_enqueue(tmp_path):
    # 白名单撤销 = 用户意图：不入队（否则恢复后会发出用户已拒绝的消息）
    qq = FakeQQ(connected=False)
    rt, outbox, state = _runtime(tmp_path, qq, allowed=())
    detail = asyncio.run(make_runtime_deliver(rt)(_job(), "内容"))
    assert "白名单" in detail and outbox.list() == []
    # QQ 未启用：不入队
    rt2, outbox2, state2 = _runtime(tmp_path, None)
    detail2 = asyncio.run(make_runtime_deliver(rt2)(_job(), "内容"))
    assert "未启用" in detail2 and outbox2.list() == []
    state.close()
    state2.close()


def test_deliver_send_failure_is_unknown_not_auto_resent(tmp_path):
    qq = FakeQQ(connected=True, fail_with=RuntimeError("发送中途断开"))
    rt, outbox, state = _runtime(tmp_path, qq)
    detail = asyncio.run(make_runtime_deliver(rt)(_job(), "报告"))
    assert "发送结果未知" in detail and "不自动重发" in detail
    rows = outbox.list()
    assert len(rows) == 1 and rows[0]["status"] == "unknown"
    # unknown 不进 pending：补投循环永远不会碰它
    assert outbox.pending() == []
    state.close()


def test_deliver_success_is_persisted_before_sending(tmp_path):
    qq = FakeQQ(connected=True)
    rt, outbox, state = _runtime(tmp_path, qq)
    detail = asyncio.run(make_runtime_deliver(rt)(_job(), "报告"))
    assert detail == "已投递 QQ 10001"
    assert qq.sent == [(10001, "报告")]
    assert outbox.list()[0]["status"] == "sent"
    assert outbox.list()[0]["attempts"] == 1
    state.close()


# ---------------------------------------------------------------- 补投排水


def test_drain_noop_until_connected(tmp_path):
    qq = FakeQQ(connected=False)
    rt, outbox, state = _runtime(tmp_path, qq)
    outbox.enqueue(job="j", target_qq=10001, content="报告")
    stats = asyncio.run(make_outbox_drainer(rt)())
    assert stats["sent"] == 0 and outbox.list()[0]["status"] == "pending"
    state.close()


def test_drain_sends_revokes_and_caps(tmp_path):
    qq = FakeQQ(connected=True)
    rt, outbox, state = _runtime(tmp_path, qq)
    ok_id = outbox.enqueue(job="j", target_qq=10001, content="报告A")
    revoked_id = outbox.enqueue(job="j", target_qq=10002, content="报告B")

    stats = asyncio.run(make_outbox_drainer(rt)())
    # 10001 在白名单 → 已投递；10002 不在 → 白名单撤销直接丢弃
    assert stats == {"sent": 1, "discarded": 1, "retry": 0, "unknown": 0, "dead": 0, "deferred": 0}
    assert qq.sent == [(10001, "报告A")]
    assert outbox.get(ok_id)["status"] == "sent"
    assert "白名单撤销" in outbox.get(revoked_id)["last_error"]
    state.close()


def test_drain_connection_error_retries_then_dead(tmp_path):
    qq = FakeQQ(connected=True, fail_with=DeliveryNotStarted("ws 没了"))
    rt, outbox, state = _runtime(tmp_path, qq)
    entry = outbox.enqueue(job="j", target_qq=10001, content="报告")
    drain = make_outbox_drainer(rt)
    for i in range(OutboxStore.MAX_ATTEMPTS):
        stats = asyncio.run(drain())
        assert stats["retry"] == 1 or stats["dead"] == 1
        row = outbox.get(entry)
        assert row["attempts"] == i + 1
    # 三次之后成死信，不再出现在 pending 里
    assert outbox.get(entry)["status"] == "dead"
    assert outbox.pending() == []
    state.close()


def test_drain_unexpected_error_marks_unknown(tmp_path):
    qq = FakeQQ(connected=True, fail_with=ValueError("协议错误"))
    rt, outbox, state = _runtime(tmp_path, qq)
    entry = outbox.enqueue(job="j", target_qq=10001, content="报告")
    stats = asyncio.run(make_outbox_drainer(rt)())
    assert stats["unknown"] == 1
    row = outbox.get(entry)
    assert row["status"] == "unknown" and "停止自动重发" in row["last_error"]
    # unknown 永不自动补投；但用户可以手动重排
    assert outbox.pending() == []
    assert outbox.retry(entry)
    assert outbox.get(entry)["status"] == "pending"
    state.close()


# ---------------------------------------------------------------- 工作台端点


def _client(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("persona:\n  name: 队列小鱼\n", encoding="utf-8")
    runtime = build_runtime(
        approver=DenyApprover(),
        db_path=tmp_path / "m.db",
        config_path=config_path,
        skills_root=tmp_path / "skills",
        data_dir=tmp_path / "data",
    )
    app = create_app(runtime)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.post(
        "/api/auth/pair", json={"code": app.state.web_auth.pairing_code}
    ).status_code == 200
    return client, runtime


def test_outbox_endpoints(tmp_path):
    client, runtime = _client(tmp_path)
    try:
        assert client.get("/api/system/cron/outbox").json()["items"] == []

        entry = runtime.outbox.enqueue(
            job="morning-report", target_qq=10001,
            content="很长的早报内容" * 20, error="QQ 未连接（NapCat 未启动？）",
        )
        listed = client.get("/api/system/cron/outbox").json()["items"]
        assert len(listed) == 1
        item = listed[0]
        assert item["status"] == "pending" and item["job"] == "morning-report"
        assert "content" not in item and len(item["preview"]) <= 80  # 只给预览

        # 丢弃 → 终态；重新排队 → pending（给满重试预算）
        assert client.post(f"/api/system/cron/outbox/{entry}/discard").json()["ok"] is True
        assert client.get("/api/system/cron/outbox").json()["items"][0]["status"] == "discarded"
        assert client.post(f"/api/system/cron/outbox/{entry}/retry").json()["ok"] is True
        assert client.get("/api/system/cron/outbox").json()["items"][0]["status"] == "pending"

        # sent 之后：不可丢弃、不可重排；不存在的条目 404
        runtime.outbox.mark(entry, "sent")
        assert client.post(f"/api/system/cron/outbox/{entry}/discard").status_code == 404
        assert client.post(f"/api/system/cron/outbox/{entry}/retry").status_code == 404
        assert client.post("/api/system/cron/outbox/99999/discard").status_code == 404
    finally:
        runtime.store.close()
