"""高层语义驱动：把 adb 原语包装成 agent 友好的语义操作。

- refresh_tree()：观察（拿 UI 树 → 结构化元素）——每次动作前先看；
- tap_element(idx)：索引优先点击（比裸坐标鲁棒）；
- type_text：自动分流 ASCII/中文（ADBKeyboard），底层保证恢复输入法；
- screenshot：截图存档 data/android_shots/latest.png。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...config import DATA_DIR
from .adb import AdbDevice, AdbError
from .ui import parse_elements, render_tree


class AndroidDriver:
    def __init__(self, adb: AdbDevice | None = None, serial: str | None = None) -> None:
        self._adb = adb or AdbDevice(serial=serial)
        self._elements: list[dict[str, Any]] = []
        self._tree_text = ""
        self._screen: tuple[int, int] | None = None

    # ---------- 观察 ----------

    def refresh_tree(self) -> str:
        """观察界面：刷新 UI 树并缓存元素（tap_element 依赖这份缓存）。"""
        serial = self._adb.require_device()
        xml = self._adb.dump_ui(serial)
        self._elements = parse_elements(xml)
        self._tree_text = render_tree(self._elements)
        return self._tree_text

    def tree(self) -> str:
        return self._tree_text

    def elements(self) -> list[dict[str, Any]]:
        return list(self._elements)

    def screenshot(self) -> Path:
        serial = self._adb.require_device()
        png = self._adb.screencap(serial)
        shots = DATA_DIR / "android_shots"
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / "latest.png"
        path.write_bytes(png)
        return path

    def screen_size(self) -> tuple[int, int]:
        if self._screen is None:
            serial = self._adb.require_device()
            self._screen = self._adb.screen_size(serial)
        return self._screen

    # ---------- 动作（CONFIRM 级，每轮只做一个） ----------

    def tap_element(self, idx: int) -> str:
        """按索引点击缓存的 UI 元素；缓存里没有就先刷新树。"""
        if not (0 <= idx < len(self._elements)):
            self.refresh_tree()
        el = next((e for e in self._elements if e["idx"] == idx), None)
        if el is None:
            raise AdbError(f"索引 {idx} 不在当前 UI 树里（先 refresh_tree 再试）")
        serial = self._adb.require_device()
        cx, cy = el["center"]
        self._adb.tap(serial, cx, cy)
        label = el["text"] or el["content_desc"] or el["resource_id"] or ""
        return f"已点击 [{idx}] {label} @({cx},{cy})"

    def tap_coords(self, x: int, y: int) -> str:
        serial = self._adb.require_device()
        self._adb.tap(serial, x, y)
        return f"已点击坐标 ({x},{y})"

    def swipe(self, direction: str) -> str:
        """上滑/下滑/返回。方向名比坐标对 agent 更稳。"""
        serial = self._adb.require_device()
        direction = (direction or "").strip().lower()
        if direction == "back":
            self._adb.key(serial, "KEYCODE_BACK")
            return "已按返回"
        w, h = self.screen_size()
        cx = w // 2
        moves = {
            "up": (cx, int(h * 0.7), cx, int(h * 0.3)),    # 内容上移=看更多
            "down": (cx, int(h * 0.3), cx, int(h * 0.7)),
        }
        if direction not in moves:
            raise AdbError(f"未知滑动方向 {direction}（up/down/back）")
        x1, y1, x2, y2 = moves[direction]
        self._adb.swipe(serial, x1, y1, x2, y2, 400)
        return f"已{direction}滑动"

    def type_text(self, text: str) -> str:
        """智能输入：纯 ASCII 走 input text，含中文走 ADBKeyboard 广播。"""
        serial = self._adb.require_device()
        if any(ord(ch) > 127 for ch in text):
            self._adb.type_unicode(serial, text)
            return f"已通过 ADBKeyboard 输入中文：{text[:50]}"
        self._adb.type_ascii(serial, text)
        return f"已输入：{text[:50]}"

    def key(self, keycode: str) -> str:
        serial = self._adb.require_device()
        self._adb.key(serial, keycode)
        return f"已按键 {keycode}"
