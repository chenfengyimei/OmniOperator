from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from pathlib import Path

from ...config import Desktop
from ..result import ToolResult
from .native import DesktopError


class DesktopDriver:
    def __init__(self, config: Desktop, backend, leases, budget, *, clock=time.monotonic):
        self.config, self.backend, self.leases, self.budget = config, backend, leases, budget
        self._clock = clock
        self._lock = threading.RLock()
        self._observations: dict[str, tuple[float, int | None, dict]] = {}

    def _enabled(self):
        if not self.config.enabled or not self.config.allowed_apps:
            raise DesktopError("桌面未启用或 allowed_apps 为空；只允许用户配置的应用")

    def _allowed(self, identity: dict) -> bool:
        exe = str(identity.get("exe") or "")
        if not exe:
            return False
        return str(Path(exe).resolve()).casefold() in {
            str(Path(app).resolve()).casefold() for app in self.config.allowed_apps
        }

    def _apps(self):
        return [str(Path(app).resolve()) for app in self.config.allowed_apps]

    def windows(self) -> dict:
        self._enabled()
        payload = self.backend.request("windows")
        return {**payload, "windows": [item for item in payload["windows"] if self._allowed(item)]}

    def _remember(self, payload: dict, session_id: int | None) -> dict:
        if not self._allowed(payload["state"]["identity"]):
            raise DesktopError("窗口的可执行文件不在允许范围")
        token = uuid.uuid4().hex
        with self._lock:
            now = self._clock()
            self._observations = {key: item for key, item in self._observations.items()
                                  if now - item[0] <= self.config.observation_ttl_s}
            while len(self._observations) >= 32:
                del self._observations[next(iter(self._observations))]
            self._observations[token] = (now, session_id, payload)
        return {"observation": token, **payload}

    def observe(self, window: int, *, session_id=None) -> dict:
        self._enabled()
        # 先检查允许应用再读取控件正文，避免从白名单外读取私人窗口。
        windows = self.windows()["windows"]
        if not any(int(row["hwnd"]) == int(window) for row in windows):
            raise DesktopError("窗口不存在或不在允许的应用内")
        return self._remember(self.backend.request("observe", window=int(window), allowed_apps=self._apps()), session_id)

    def _observed(self, token: str, session_id) -> dict:
        self._enabled()
        with self._lock:
            item = self._observations.get(token)
        if item is None or item[1] != session_id or self._clock() - item[0] > self.config.observation_ttl_s:
            raise DesktopError("观察不存在、已过期或属于其它会话；请重新观察")
        if not self._allowed(item[2]["state"]["identity"]):
            raise DesktopError("应用授权已撤销")
        return item[2]

    def action(self, observation: str, element: str, action: str, text: str = "", *, session_id=None, cancel=None, preview=None) -> ToolResult:
        token = None
        dispatched = False
        try:
            before = self._observed(observation, session_id)
            state = before["state"]
            if preview is not None and (preview.get("window_title") != state["identity"].get("title")
                                        or preview.get("application") != state["identity"].get("exe")):
                raise DesktopError("审批预览与实际窗口不一致，未执行")
            nodes = [node for node in state["nodes"] if node["id"] == element]
            if action not in {"set_value", "invoke", "select"} or len(nodes) != 1:
                raise DesktopError("动作或元素身份不合法")
            node = nodes[0]
            if node.get("password") or node.get("truncated") or node.get("offscreen") or not node.get("enabled"):
                raise DesktopError("目标不可编辑、被遮挡或内容不完整")
            if not node.get(action):
                raise DesktopError("控件不支持该后台语义动作；需用户手动操作，不自动抢焦点")
            if state.get("minimized") or not state.get("enabled"):
                raise DesktopError("窗口最小化或被弹窗禁用")
            if len(text) > 4000:
                raise DesktopError("单次文本超出 4000 字符")
            identity = state["identity"]
            token = self.leases.acquire(f"desktop:{identity['hwnd']}", ttl_s=max(120, self.config.timeout_s * 3))
            if cancel is not None:
                cancel.raise_if_cancelled()
            fresh = self.backend.request("observe", window=identity["hwnd"], allowed_apps=self._apps())
            if fresh["fingerprint"] != before["fingerprint"]:
                raise DesktopError("观察已变化，未执行；请重新观察")
            if getattr(cancel, "cancelled", False):
                return ToolResult.cancelled("本次运行已取消，未执行桌面写入")
            ok, reason = self.budget.try_consume()
            if not ok:
                raise DesktopError(reason)
            # 一次观察只允许一次写入尝试，结果未知也不能重放旧动作。
            with self._lock:
                if self._observations.pop(observation, None) is None:
                    raise DesktopError("观察已被使用，未执行")
            dispatched = True
            after = self.backend.request("action", window=identity["hwnd"], fingerprint=before["fingerprint"],
                                         element=element, action=action, text=text, allowed_apps=self._apps())
            if any(after["state"]["identity"].get(key) != identity.get(key) for key in ("hwnd", "pid", "created", "exe")):
                raise DesktopError("写后窗口进程身份变化，效果未知，禁止重试", unknown=True)
            observed = self._remember(after, session_id)
            if after.get("verified"):
                return ToolResult.ok("桌面编辑已回读确认。\n" + json.dumps(observed, ensure_ascii=False),
                                     side_effect_state="applied", verification="UIAutomation 文本精确回读", data=observed)
            return ToolResult(status="unknown", text="动作已下发，回读未确认目标效果；不要自动重试。\n" + json.dumps(observed, ensure_ascii=False),
                              side_effect_state="unknown", verification="需要人工核查", data=observed)
        except DesktopError as exc:
            return ToolResult(status="unknown" if exc.unknown else "error", text=str(exc),
                              error_code="desktop_unknown" if exc.unknown else "desktop_rejected",
                              side_effect_state="unknown" if exc.unknown else "none")
        except Exception as exc:
            return ToolResult(status="unknown" if dispatched else "error", text=f"桌面操作未确认（{type(exc).__name__}）",
                              side_effect_state="unknown" if dispatched else "none", error_code="desktop_failed")
        finally:
            if token:
                self.leases.release(token)

    def screenshot(self, observation: str, *, session_id=None) -> bytes:
        before = self._observed(observation, session_id)
        payload = self.backend.request("screenshot", window=before["state"]["identity"]["hwnd"],
                                       fingerprint=before["fingerprint"], allowed_apps=self._apps())
        return base64.b64decode(payload["png"], validate=True)
