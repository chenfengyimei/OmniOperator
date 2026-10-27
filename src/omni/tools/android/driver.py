"""高层语义驱动：把 adb 原语包装成 agent 友好的语义操作。

- refresh_tree()：观察（拿 UI 树 → 结构化元素）——每次动作前先看；
  结果持久化到 data/android_cache/tree.json（原子写），进程重启后
  仍能看到上次观察的树（心跳/通道重启不丢现场）；
- tap_element(idx)：索引优先点击（比裸坐标鲁棒）；对陈旧缓存（超过
  fresh_ttl_s 未刷新）或换过设备的缓存先强制重新观察，绝不盲点；
- type_text：自动分流 ASCII/中文（ADBKeyboard），底层保证恢复输入法；
- screenshot：截图存档 data/android_shots/latest.png。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

from ...config import DATA_DIR
from .adb import AdbDevice, AdbError
from .ui import parse_elements, render_tree

DEFAULT_FRESH_TTL_S = 600.0  # 缓存树超过 10 分钟即视为陈旧（屏幕可能已锁屏/跳转）


class AndroidDriver:
    def __init__(
        self,
        adb: AdbDevice | None = None,
        serial: str | None = None,
        *,
        cache_path: Path | None = None,
        fresh_ttl_s: float = DEFAULT_FRESH_TTL_S,
    ) -> None:
        self._adb = adb or AdbDevice(serial=serial)
        self._cache_path = cache_path or (DATA_DIR / "android_cache" / "tree.json")
        self._fresh_ttl_s = fresh_ttl_s
        self._elements: list[dict[str, Any]] = []
        self._tree_text = ""
        self._screen: tuple[int, int] | None = None
        self._captured_at: str | None = None
        self._cache_serial: str | None = None
        self._load_cache()

    # ---------- 缓存持久化（原子写，参照 JobStore 的 tmp+os.replace） ----------

    @staticmethod
    def _now_iso() -> str:
        return dt.datetime.now().astimezone().isoformat(timespec="seconds")

    def _load_cache(self) -> None:
        """启动时恢复上次观察结果；文件缺失/损坏一律按空缓存处理（不影响设备操作）。"""
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            elements = raw["elements"]
            if not isinstance(elements, list):
                raise ValueError("elements 必须是列表")
            self._elements = elements
            self._tree_text = str(raw.get("tree_text", ""))
            self._captured_at = raw.get("captured_at")
            self._cache_serial = raw.get("serial")
            screen = raw.get("screen")
            if isinstance(screen, list) and len(screen) == 2:
                self._screen = (int(screen[0]), int(screen[1]))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass

    def _save_cache(self) -> None:
        """持久化最新观察结果；纯尽力而为——盘写不进去也不能影响设备操作。"""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "serial": self._cache_serial,
                        "captured_at": self._captured_at,
                        "screen": list(self._screen) if self._screen else None,
                        "tree_text": self._tree_text,
                        "elements": self._elements,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self._cache_path)
        except OSError as exc:
            print(f"[android] 缓存写盘失败（忽略）：{exc}", file=sys.stderr, flush=True)

    def _cache_is_stale(self) -> bool:
        if self._captured_at is None:
            return True
        try:
            captured = dt.datetime.fromisoformat(self._captured_at)
        except ValueError:
            return True
        age = (dt.datetime.now().astimezone() - captured).total_seconds()
        return age > self._fresh_ttl_s

    # ---------- 观察 ----------

    def refresh_tree(self) -> str:
        """观察界面：刷新 UI 树并缓存元素（tap_element 依赖这份缓存）。"""
        serial = self._adb.require_device()
        xml = self._adb.dump_ui(serial)
        self._elements = parse_elements(xml)
        self._tree_text = render_tree(self._elements)
        self._captured_at = self._now_iso()
        self._cache_serial = serial
        self._save_cache()
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
            self._save_cache()
        return self._screen

    # ---------- 动作（CONFIRM 级，每轮只做一个） ----------

    def tap_element(self, idx: int) -> str:
        """按索引点击缓存的 UI 元素；缓存缺失/陈旧/换过设备都先刷新树。"""
        serial = self._adb.require_device()
        if (
            not (0 <= idx < len(self._elements))
            or self._cache_is_stale()
            or self._cache_serial != serial
        ):
            self.refresh_tree()
        el = next((e for e in self._elements if e["idx"] == idx), None)
        if el is None:
            raise AdbError(f"索引 {idx} 不在当前 UI 树里（先 refresh_tree 再试）")
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
