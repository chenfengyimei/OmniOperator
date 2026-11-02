"""浏览器能力域 + 确认流单测（不启动真浏览器）。"""

import json
import threading
import time
from types import SimpleNamespace

from omni.memory.state import StateStore
from omni.safety.broker import ApprovalBroker
from omni.safety.gates import AllowAllApprover, Risk, WebApprover
from omni.tools.browser.budget import WriteBudget
from omni.tools.browser.driver import BrowserError
from omni.tools.browser.tools import register_browser_tools
from omni.tools.registry import Tool, ToolRegistry


def _state(tmp_path, name="state.db") -> StateStore:
    return StateStore(tmp_path / name)


def _confirm_tool() -> Tool:
    return Tool(
        name="browser_click",
        description="点击页面元素（需确认）",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda _a: "ok",
        toolset="browser",
        risk=Risk.CONFIRM,
    )


# ---------- 写预算 ----------


def test_write_budget_limit_and_day_rollover(tmp_path):
    state = StateStore(tmp_path / "state.db")
    budget = WriteBudget(state, max_per_day=2)
    ok1, msg1 = budget.try_consume()
    ok2, _msg2 = budget.try_consume()
    assert ok1 and ok2 and "2/2" in msg1 or "1/2" in msg1
    ok3, msg3 = budget.try_consume()
    assert not ok3 and "额度已用完" in msg3
    assert budget.usage_today() == (2, 2)

    # 额度按 (scope, 日) 分行存：别的日期用满 99 次不影响今天
    with state._tx() as conn:
        conn.execute("INSERT INTO budgets(scope, day, used) VALUES ('browser_write', '2000-01-01', 99)")
    assert budget.usage_today() == (2, 2)
    ok4 = WriteBudget(state, max_per_day=2)
    assert ok4.usage_today() == (2, 2)
    # 新的一天（模拟 tomorrow）只有当天那行会被读
    assert state.budget_used("browser_write", "2000-01-01") == 99
    assert state.budget_used("browser_write", "2000-01-02") == 0


# ---------- 审批代理 ----------


def test_broker_allow_deny_timeout():
    broker = ApprovalBroker(timeout_s=1.0)
    tool = _confirm_tool()

    # 允许：另一个线程延迟后 resolve
    result = {}

    def requester_allow():
        result["allow"] = broker.request(tool, timeout=5)

    threading.Thread(target=requester_allow).start()
    time.sleep(0.2)
    pending = broker.list_pending()
    assert len(pending) == 1 and pending[0]["tool_name"] == "browser_click"
    assert broker.resolve(pending[0]["id"], True)
    time.sleep(0.2)
    assert result["allow"].allowed is True

    # 拒绝：带理由
    def requester_deny():
        result["deny"] = broker.request(tool, timeout=5)

    threading.Thread(target=requester_deny).start()
    time.sleep(0.2)
    pending = broker.list_pending()
    assert broker.resolve(pending[0]["id"], False, reason="不想点")
    time.sleep(0.2)
    assert result["deny"].allowed is False
    assert "不想点" in result["deny"].reason

    # 超时自动拒绝（fail-closed）
    decision = broker.request(tool, timeout=0.05)
    assert decision.allowed is False and "超时" in decision.reason

    # resolve 不存在的 id
    assert broker.resolve("nope", True) is False


def test_web_approver_risk_routing():
    broker = ApprovalBroker(timeout_s=5)
    approver = WebApprover(broker)
    safe = SimpleNamespace(name="x", description="d", risk=Risk.SAFE)
    dangerous = SimpleNamespace(name="x", description="d", risk=Risk.DANGEROUS)
    assert approver.check(safe).allowed is True
    assert approver.check(dangerous).allowed is False
    # CONFIRM → broker（这里验证走通 broker：后台放行）
    confirm = _confirm_tool()
    result = {}

    def run():
        result["d"] = approver.check(confirm, {"target": "发布", "text": "完整消息"})

    threading.Thread(target=run).start()
    time.sleep(0.2)
    pending = broker.list_pending()
    assert pending and pending[0]["risk"] == "confirm"
    assert "发布" in pending[0]["detail"] and "完整消息" in pending[0]["detail"]
    broker.resolve(pending[0]["id"], True)
    time.sleep(0.2)
    assert result["d"].allowed is True


# ---------- 浏览器工具（假驱动） ----------


class FakeDriver:
    def __init__(self) -> None:
        self.clicked: list[str] = []
        self.typed: list[tuple[str, str]] = []

    def navigate(self, url):
        return f"标题：{url}"

    def read(self, max_chars=4000):
        return "页面文本内容"

    def screenshot(self):
        from pathlib import Path

        return Path("shots") / "latest.png"

    def click(self, target):
        self.clicked.append(target)
        return "[00:00:01 操作后页面] 已点击"

    def type_text(self, target, text):
        self.typed.append((target, text))
        return "[00:00:02 操作后页面] 已输入"


def _registry(driver, budget) -> ToolRegistry:
    registry = ToolRegistry()
    register_browser_tools(registry, driver, budget)
    return registry


def test_browser_tools_read_safe(tmp_path):
    registry = _registry(FakeDriver(), WriteBudget(_state(tmp_path)))
    assert "标题" in registry.dispatch("browser_navigate", {"url": "https://bilibili.com"})
    assert "页面文本内容" in registry.dispatch("browser_read", "{}")
    assert "latest.png" in registry.dispatch("browser_screenshot", "{}")


def test_browser_tools_write_consumes_budget(tmp_path):
    driver = FakeDriver()
    budget = WriteBudget(_state(tmp_path), max_per_day=1)
    registry = _registry(driver, budget)
    out = registry.dispatch("browser_click", {"target": "text=登录"}, approver=AllowAllApprover())
    assert "已点击" in out and driver.clicked == ["text=登录"]
    assert "操作后页面" in out  # 写后回读验证附带
    # 预算耗尽：第二次被拒，且不触达驱动
    out2 = registry.dispatch("browser_click", {"target": "text=发布"}, approver=AllowAllApprover())
    assert "额度已用完" in out2 and driver.clicked == ["text=登录"]

    # 驱动报错以字符串回传
    class BoomDriver(FakeDriver):
        def click(self, target):
            raise BrowserError("元素找不到")

    registry2 = _registry(BoomDriver(), WriteBudget(_state(tmp_path, "s2"), max_per_day=5))
    assert "错误：元素找不到" in registry2.dispatch("browser_click", {"target": "text=x"}, approver=AllowAllApprover())


# ---------------------------------------------------------------- 结构化结果（D09 收口）


class RaisingDriver(FakeDriver):
    """按注入的异常类型模拟写失败路径。"""

    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def click(self, target):
        raise self._exc

    def type_text(self, target, text):
        raise self._exc


def test_browser_write_structured_results(tmp_path):
    """写成功=applied（带回读验证）；Busy=可重试未执行；异常=副作用未知。"""
    from omni.tools.browser.driver import BrowserBusy, BrowserUnknown

    driver = FakeDriver()
    registry = _registry(driver, WriteBudget(_state(tmp_path), max_per_day=10))
    ok = registry.execute("browser_click", {"target": "text=登录"}, approver=AllowAllApprover())
    assert ok.status == "ok" and ok.side_effect_state == "applied" and ok.legacy is False
    assert "操作后页面" in ok.verification  # 写后回读进 verification

    # 动作内异常（元素找不到/点击后等待失败分不开）：副作用未知，不盲目重试
    registry2 = _registry(
        RaisingDriver(BrowserError("元素找不到")), WriteBudget(_state(tmp_path, "s2"), max_per_day=10)
    )
    failed = registry2.execute("browser_click", {"target": "text=x"}, approver=AllowAllApprover())
    assert failed.status == "unknown" and failed.side_effect_state == "unknown"
    assert failed.error_code == "browser_action_unknown" and "元素找不到" in failed.text

    # 所有者线程超时：动作可能仍在执行
    registry3 = _registry(
        RaisingDriver(BrowserUnknown("等待所有者线程超时，动作可能仍在执行")),
        WriteBudget(_state(tmp_path, "s3"), max_per_day=10),
    )
    unknown = registry3.execute("browser_type", {"target": "text=x", "text": "hi"}, approver=AllowAllApprover())
    assert unknown.status == "unknown" and unknown.error_code == "browser_unknown"

    # 排队被拒：从未进入所有者线程，可重试
    registry4 = _registry(
        RaisingDriver(BrowserBusy("浏览器忙：请求排队已满")), WriteBudget(_state(tmp_path, "s4"), max_per_day=10)
    )
    busy = registry4.execute("browser_click", {"target": "text=x"}, approver=AllowAllApprover())
    assert busy.status == "error" and busy.retryable is True and busy.side_effect_state == "none"

    # 预算耗尽：确定未执行
    registry5 = _registry(FakeDriver(), WriteBudget(_state(tmp_path, "s5"), max_per_day=1))
    assert registry5.execute("browser_click", {"target": "text=a"}, approver=AllowAllApprover()).status == "ok"
    exhausted = registry5.execute("browser_click", {"target": "text=b"}, approver=AllowAllApprover())
    assert exhausted.status == "error" and exhausted.error_code == "budget_exhausted"
    assert exhausted.side_effect_state == "none" and driver.clicked == ["text=登录"]
