"""高层语义驱动：把 adb 原语包装成 agent 友好的语义操作。

- refresh_tree()：观察（UI 树 → 结构化元素），输出头部带**观察身份**
  `obs=<id>`（设备序列号 + 树内容的短哈希）；持久化到
  data/android_cache/tree.json，进程重启后仍能看到上次观察的树；
- tap_element(idx, observation)：观察绑定点击（D16/A12）——点击前重新
  观察，内容哈希与模型看到的观察一致才点；不一致只返回新观察，绝不按
  旧 idx 点新元素；点击后强制复检并返回点击后的新观察；
- type_text：自动分流 ASCII/中文（ADBKeyboard），底层保证恢复输入法；
- screenshot：截图存档 data/android_shots/latest.png。

为什么 tap 前必须重新观察：缓存的"新鲜"只说明**我们**最近 dump 过，
不代表屏幕没变——弹窗、旋转、跳转都不会通知我们。模型看到的观察和
点击瞬间的屏幕之间隔着一次人工审批（可能几分钟），这期间界面完全可以
换掉。只有"点击前 dump 的内容哈希 == 模型引用的观察"才能保证 idx
仍然指向模型选的那个元素。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from ...config import DATA_DIR
from .adb import AdbDevice, AdbError
from .ui import parse_elements, render_tree


def observation_id(serial: str, tree_text: str) -> str:
    """观察身份：设备序列号 + 树内容的短哈希。

    时间**不进哈希**：同一界面两次观察 id 相同——点击前的复核才有
    "界面没变"的可比性；界面内容或设备一变，id 必变。
    """
    digest = hashlib.sha256(f"{serial}\n{tree_text}".encode("utf-8")).hexdigest()
    return digest[:8]


def scale_coords(
    x: int, y: int, stream_w: int, stream_h: int, real_w: int, real_h: int
) -> tuple[int, int]:
    """帧内坐标 → 真实屏幕坐标（NOTES §2.3：换算只在唯一入口做一次）。

    stream_w/h 是前端展示帧的尺寸，real_w/h 来自 wm size；两者可不同
    （后端将来做半分辨率压缩也只改这里）。
    """
    if not stream_w or not stream_h:
        raise AdbError("stream_w/stream_h 必须为正（前端须上传展示帧尺寸）")
    return round(x * real_w / stream_w), round(y * real_h / stream_h)


class AndroidDriver:
    def __init__(
        self,
        adb: AdbDevice | None = None,
        serial: str | None = None,
        *,
        cache_path: Path | None = None,
        shots_dir: Path | None = None,
    ) -> None:
        self._adb = adb or AdbDevice(serial=serial)
        self._cache_path = cache_path or (DATA_DIR / "android_cache" / "tree.json")
        # 截图目录可注入：否则临时实例会把截图写到真机 data/android_shots（A08）
        self._shots_dir = Path(shots_dir) if shots_dir else (DATA_DIR / "android_shots")
        self._elements: list[dict[str, Any]] = []
        self._tree_text: str = ""
        self._screen: tuple[int, int] | None = None
        self._captured_at: str | None = None
        self._cache_serial: str | None = None
        self._observation_id: str | None = None
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
            self._observation_id = raw.get("observation_id")
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
                        "observation_id": self._observation_id,
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

    # ---------- 观察 ----------

    def refresh_tree(self) -> str:
        """观察界面：刷新 UI 树并缓存元素；输出头部带观察身份 obs=<id>。"""
        serial = self._adb.require_device()
        xml = self._adb.dump_ui(serial)
        self._elements = parse_elements(xml)
        self._tree_text = render_tree(self._elements)
        self._captured_at = self._now_iso()
        self._cache_serial = serial
        self._observation_id = observation_id(serial, self._tree_text)
        self._save_cache()
        return self.observation_header() + "\n" + self._tree_text

    def observation_header(self) -> str:
        """观察头：模型引用它把"我选的 idx"绑定到"我看到的那份界面"。"""
        if not self._observation_id:
            return "观察 obs=（还没有观察记录，先 screen_tree）"
        return f"观察 obs={self._observation_id}（设备 {self._cache_serial}，{self._captured_at}）"

    @property
    def observation_id(self) -> str | None:
        return self._observation_id

    def tree(self) -> str:
        return self._tree_text

    def elements(self) -> list[dict[str, Any]]:
        return list(self._elements)

    def screenshot(self) -> Path:
        serial = self._adb.require_device()
        png = self._adb.screencap(serial)
        shots = self._shots_dir
        shots.mkdir(parents=True, exist_ok=True)
        path = shots / "latest.png"
        path.write_bytes(png)
        return path

    def screenshot_bytes(self) -> bytes:
        """当前屏幕 PNG（不落盘）——工作台 MJPEG 流每帧调用。"""
        serial = self._adb.require_device()
        return self._adb.screencap(serial)

    def screen_size(self) -> tuple[int, int]:
        if self._screen is None:
            serial = self._adb.require_device()
            self._screen = self._adb.screen_size(serial)
            self._save_cache()
        return self._screen

    def tap_scaled(self, x: int, y: int, stream_w: int, stream_h: int) -> str:
        """工作台点屏：帧内坐标 + 帧尺寸 → wm size 反算真实坐标后点击。"""
        serial = self._adb.require_device()
        w, h = self._adb.screen_size(serial)
        rx, ry = scale_coords(int(x), int(y), int(stream_w), int(stream_h), w, h)
        self._adb.tap(serial, rx, ry)
        return f"已点击 ({rx},{ry})"

    # ---------- 动作（CONFIRM 级，每轮只做一个） ----------

    def tap_element(self, idx: int, observation: str | None = None) -> str:
        """观察绑定点击（D16/A12）：界面和观察不一致就不点，只返回新观察。

        - `observation` 必须来自 screen_tree 输出头部的 `obs=<id>`——
          它把"模型选的 idx"绑定到"模型看到的那份界面"上；
        - 点击前重新 dump：内容哈希一致才说明界面没变，idx 仍指向模型
          选的那个元素；否则**绝不盲点**，返回新观察让模型重新选择；
        - 点击后强制复检：返回点击后的新观察（失败则如实说状态未知）。
        """
        expected = str(observation or "").strip()
        if not expected:
            raise AdbError(
                "tap_element 需要 observation 参数（android_screen_tree 输出第一行的 obs=…）。"
                "不带观察身份的索引点击已禁用：界面变化后旧 idx 会点错元素"
            )
        self.refresh_tree()
        if self._observation_id != expected:
            return (
                f"未点击：界面与观察 obs={expected} 不一致（设备或界面已经变了，"
                "审批/等待期间屏幕可能切换）。下面是当前界面的新观察，"
                "请基于它重新选择元素：\n\n"
                + self.observation_header() + "\n" + self._tree_text
            )
        el = next((e for e in self._elements if e["idx"] == idx), None)
        if el is None:
            raise AdbError(f"索引 {idx} 不在当前 UI 树里（先 screen_tree 再试）")
        cx, cy = el["center"]
        serial = self._cache_serial or self._adb.require_device()
        self._adb.tap(serial, cx, cy)
        label = el["text"] or el["content_desc"] or el["resource_id"] or ""
        clicked = f"已点击 [{idx}] {label} @({cx},{cy})"
        try:
            after = self.refresh_tree()
        except Exception as exc:  # noqa: BLE001 —— 点上了但复核失败：如实报未知
            return (
                f"{clicked}；复核观察失败（{exc}）——界面状态未知，"
                "先 screen_tree 重新观察再决定下一步"
            )
        changed = self._observation_id != expected
        note = "界面有变化" if changed else "界面未变化"
        return f"{clicked}；{note}。点击后观察：\n\n{after}"

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
