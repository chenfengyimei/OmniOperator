"""纯 ADB 协议层：设备无关的 adb 命令封装（不知道 LLM 与语义）。

三处防御（ghost-in-the-droid 提炼，NOTES §2.2）：
1. keycode 白名单 ^KEYCODE_[A-Z0-9_]+$（防设备端 shell 注入）；
2. input text：空格→%s、剔除单引号、单引号包裹（ASCII only，中文走 ADBKeyboard）；
3. screencap 输出裁剪到 PNG 签名（部分设备会在 PNG 前混入警告文本行）。

runner 可注入（单测离线跑）：默认走 subprocess 调真 adb。
"""
from __future__ import annotations

import base64
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Any, Literal, overload
from .process import run_adb

ADB_KEYCODE_RE = re.compile(r"^KEYCODE_[A-Z0-9_]+$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ADB_KEYBOARD_IME = "com.android.adbkeyboard/.AdbIME"


class AdbError(RuntimeError):
    """adb 交互失败（无设备/超时/命令错误/注入尝试）。"""
    def __init__(self, message, *, code='adb_rejected'):
        super().__init__(message)
        self.code=code


def strip_to_png(data: bytes) -> bytes:
    """裁掉 PNG 签名前的任何杂行（部分设备 screencap 会混入警告文本）。"""
    idx = data.find(PNG_SIGNATURE)
    if idx == -1:
        raise AdbError("screencap 输出里找不到 PNG 签名（可能被设备端文本污染）")
    return data[idx:]


def quote_input_text(text: str) -> str:
    """input text 参数转义：空格→%s，剔除单引号，单引号包裹。"""
    escaped = text.replace("'", "").replace(" ", "%s")
    return f"'{escaped}'"


def adb_keyboard_b64(text: str) -> str:
    """中文/Unicode 文本 → UTF-8 base64（ADB_INPUT_B64 广播用）。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class AdbDevice:
    """单台设备的 adb 封装。serial=None 时使用当前唯一连接的设备。"""

    def __init__(
        self,
        serial: str | None = None,
        adb_path: str | None = None,
        runner: Any = None,
    ) -> None:
        """runner 可注入（单测用）：(argv: list[str], timeout: float) -> bytes。"""
        self._serial = (serial or "").strip() or None
        self._adb = adb_path or shutil.which("adb") or "adb"
        self._runner = runner
        self._local = threading.local()

    @contextmanager
    def deadline(self, seconds, *, override=False):
        previous=getattr(self._local,'deadline',None)
        until=time.monotonic()+seconds
        self._local.deadline=until if previous is None or override else min(previous,until)
        try:yield
        finally:self._local.deadline=previous

    # ---------- 底层 ----------

    def _argv(self, args: list[str]) -> list[str]:
        argv = [self._adb]
        return argv + [str(a) for a in args]

    @overload
    def _run(self, args: list[str], *, timeout: float = 15.0, binary: Literal[True]) -> bytes: ...

    @overload
    def _run(self, args: list[str], *, timeout: float = 15.0, binary: Literal[False] = False) -> str: ...

    def _run(self, args: list[str], *, timeout: float = 15.0, binary: bool = False) -> Any:
        until=getattr(self._local,'deadline',None)
        if until is not None:
            timeout=min(timeout,until-time.monotonic())
            if timeout<=0:raise AdbError('安卓操作总时限已用尽，未重复执行',code='adb_timeout')
        argv = self._argv(args)
        if self._runner is not None:
            out = self._runner(argv, timeout)
            if len(out)>16777216:raise AdbError('ADB 输出超过上限',code='adb_output_limit')
            return out if binary else out.decode("utf-8", errors="replace")
        try:
            code,out,err=run_adb(argv,timeout)
        except FileNotFoundError as exc:
            raise AdbError("adb 不在 PATH（先安装 Android Platform-Tools）",code='adb_missing') from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError('adb 客户端超时，设备操作可能仍在进行',code='adb_timeout') from exc
        except OverflowError as exc:
            raise AdbError('ADB 输出过大或读取未完成',code='adb_output_limit') from exc
        if code != 0:
            raise AdbError(f'adb 调用失败（退出码 {code}）',code='adb_failed')
        return out if binary else out.decode('utf-8',errors='replace')

    def _device_shell(self, serial: str, command: str, *, timeout: float = 15.0) -> str:
        return self._run(
            ["-s", serial, "shell", command], timeout=timeout
        )

    def _device_exec_out(self, serial: str, command: str, *, binary: bool = False) -> Any:
        return self._run(
            ["-s", serial, "exec-out", command], timeout=20.0, binary=binary
        )

    # ---------- 设备 ----------

    def devices(self) -> list[dict[str, str]]:
        """列出已连接设备：[{serial, state}]。"""
        out = self._run(["devices"], timeout=8)
        rows = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                rows.append({"serial": parts[0], "state": parts[1]})
        return rows

    def require_device(self, serial: str | None = None) -> str:
        rows = [r for r in self.devices() if r["state"] == "device"]
        if not rows:
            raise AdbError(
                "没有已授权的安卓设备（手机开 USB 调试、屏幕上点允许授权）"
            )
        target=serial or self._serial
        if target:
            for r in rows:
                if r["serial"] == target:
                    return target
            raise AdbError(f"指定设备 {target} 未连接或未授权",code='device_unavailable')
        if len(rows) > 1:
            raise AdbError(
                f"连接了多台设备 {[r['serial'] for r in rows]}，请指定 serial"
            )
        return rows[0]["serial"]

    def screen_size(self, serial: str) -> tuple[int, int]:
        out = self._device_shell(serial, "wm size").strip()
        matches = re.findall(r"(\d+)x(\d+)", out)
        if not matches:
            raise AdbError(f"wm size 解析失败: {out}")
        return int(matches[-1][0]), int(matches[-1][1])

    # ---------- 输入 ----------

    def tap(self, serial: str, x: int, y: int) -> None:
        if not isinstance(x, int) or not isinstance(y, int):
            raise AdbError("tap 坐标必须是整数")
        self._device_shell(serial, f"input tap {int(x)} {int(y)}")

    def swipe(
        self, serial: str, x1: int, y1: int, x2: int, y2: int, ms: int = 300
    ) -> None:
        self._device_shell(
            serial,
            f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}",
        )

    def key(self, serial: str, keycode: str) -> None:
        """keycode 必须形如 KEYCODE_BACK（白名单，防设备端 shell 注入）。"""
        code = (keycode or "").strip().upper()
        if not ADB_KEYCODE_RE.match(code):
            raise AdbError(f"keycode 非法（须形如 KEYCODE_BACK）：{keycode!r}")
        self._device_shell(serial, f"input keyevent {code}")

    def type_ascii(self, serial: str, text: str) -> None:
        """ASCII 文本输入（input text 通道）。含非 ASCII 请走 type_unicode。"""
        if any(ord(ch) > 127 for ch in text):
            raise AdbError("含非 ASCII 字符：中文请走 ADBKeyboard（type_unicode）")
        self._device_shell(serial, f"input text {quote_input_text(text)}")

    def type_unicode(self, serial: str, text: str, *, before_input=None) -> None:
        """中文/Emoji 输入：ADBKeyboard 广播，结束后恢复原输入法（必须配对）。

        前提：设备上已安装 ADBKeyboard.apk（范凯 skill 同款方案）。
        """
        original = self._device_shell(
            serial, "settings get secure default_input_method"
        ).strip()
        if not re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+', original):
            raise AdbError("拿不到当前输入法（settings get 失败），中止以免锁死输入法")
        enabled = self._device_shell(serial, 'settings get secure enabled_input_methods').strip().split(':')
        was_enabled = any(item.split(';')[0] == _ADB_KEYBOARD_IME for item in enabled) or original == _ADB_KEYBOARD_IME
        try:
            self._device_shell(serial, f"ime enable {_ADB_KEYBOARD_IME}")
            self._device_shell(serial, f"ime set {_ADB_KEYBOARD_IME}")
            if before_input is not None:
                before_input()
            b64 = adb_keyboard_b64(text)
            self._device_shell(
                serial, f"am broadcast -p com.android.adbkeyboard -a ADB_INPUT_B64 --es msg {b64}"
            )
        finally:
            # 范凯纪律：必须恢复原输入法，绝不 ime reset 猜默认值
            with self.deadline(5,override=True):
                self._device_shell(serial, f"ime set {original}")
                if not was_enabled:
                    self._device_shell(serial, f"ime disable {_ADB_KEYBOARD_IME}")

    # ---------- 捕获 ----------

    def screencap(self, serial: str) -> bytes:
        """PNG 字节（已裁剪签名前杂行）。"""
        raw = self._device_exec_out(serial, "screencap -p", binary=True)
        return strip_to_png(raw if isinstance(raw, bytes) else raw.encode())

    def dump_ui(self, serial: str) -> str:
        """UI 树 XML：dump 到临时文件再 cat 回读，用完即删（范凯纪律：精确 rm）。"""
        tmp = "/sdcard/omni_uidump.xml"
        try:
            self._device_shell(serial, f"uiautomator dump {tmp}")
            return self._device_exec_out(serial, f"cat {tmp}")
        finally:
            with self.deadline(3,override=True):
                self._device_shell(serial, f"rm {tmp}")

    def resolve_app(self, serial: str, package: str) -> str:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+',package):
            raise AdbError('应用包名非法')
        out=self._device_shell(serial,f'cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER {package}')
        component=out.strip().splitlines()[-1] if out.strip() else ''
        if not re.fullmatch(re.escape(package)+r'/[A-Za-z0-9_.$]+',component):
            raise AdbError('无法核验该应用的启动组件')
        return component

    def launch_app(self, serial: str, component: str):
        if not re.fullmatch(r'[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+',component):raise AdbError('启动组件非法')
        self._device_shell(serial,f'am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n {component}')
