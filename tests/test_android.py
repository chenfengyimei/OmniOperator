"""安卓能力域单测：三防御 / UI 解析 / 中文输入序列 / 工具派发（全部离线）。"""
import base64

import pytest

from omni.tools.android.adb import (
    AdbDevice,
    AdbError,
    adb_keyboard_b64,
    quote_input_text,
    strip_to_png,
)
from omni.tools.android.driver import AndroidDriver
from omni.tools.android.ui import parse_elements, render_tree
from omni.safety.gates import AllowAllApprover
from omni.tools.registry import ToolRegistry


class FakeRunner:
    """记录 argv、按子串匹配返回预置输出的假 runner。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.script: list[tuple[str, bytes]] = []

    def expect(self, pattern: str, out) -> None:
        self.script.append((pattern, out if isinstance(out, bytes) else out.encode("utf-8")))

    def __call__(self, argv: list[str], timeout: float) -> bytes:
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for pattern, out in reversed(self.script):  # 后注册的优先（支持覆盖）
            if pattern in joined:
                return out
        return b""

    def calls_matching(self, pattern: str) -> list[list[str]]:
        return [c for c in self.calls if any(pattern in a for a in c)]


# ---------- 纯函数 ----------

def test_quote_input_text():
    assert quote_input_text("hello world") == "'hello%sworld'"
    assert quote_input_text("tom's") == "'toms'"  # 单引号剔除
    assert quote_input_text("ab-cd_1") == "'ab-cd_1'"


def test_adb_keyboard_b64_roundtrip():
    b64 = adb_keyboard_b64("你好，小鱼")
    assert base64.b64decode(b64).decode("utf-8") == "你好，小鱼"


def test_strip_to_png():
    good = b"some warning line\nanother\n" + b"\x89PNG\r\n\x1a\n" + b"pngdata"
    assert strip_to_png(good).startswith(b"\x89PNG")
    assert strip_to_png(b"\x89PNG\r\n\x1a\npure").startswith(b"\x89PNG")
    with pytest.raises(AdbError, match="PNG"):
        strip_to_png(b"no png here")


# ---------- keycode 白名单 / 输入防御 ----------

def _device_with_runner():
    runner = FakeRunner()
    adb = AdbDevice(adb_path="adb", runner=runner)
    return adb, runner


def test_keycode_whitelist_blocks_injection():
    adb, runner = _device_with_runner()
    adb.key("EMU1", "KEYCODE_BACK")
    assert any("input keyevent KEYCODE_BACK" in " ".join(c) for c in runner.calls)
    # 小写会被归一化后放行（安全且友好）
    adb.key("EMU1", "keycode_back")
    for evil in ("KEYCODE_BACK; rm -rf /", "KEYCODE_BACK && reboot"):
        with pytest.raises(AdbError, match="keycode 非法"):
            adb.key("EMU1", evil)
    assert not any("rm -rf" in " ".join(c) for c in runner.calls)


def test_type_ascii_escapes_and_rejects_unicode():
    adb, runner = _device_with_runner()
    adb.type_ascii("EMU1", "hello world")
    assert any("'hello%sworld'" in " ".join(c) for c in runner.calls)
    with pytest.raises(AdbError, match="非 ASCII"):
        adb.type_ascii("EMU1", "你好")


def test_type_unicode_restores_ime_in_order():
    adb, runner = _device_with_runner()
    runner.expect("settings get secure default_input_method", "com.gboard/.Gboard\n")
    adb.type_unicode("EMU1", "你好小鱼")
    joined_calls = [" ".join(c) for c in runner.calls]
    # 顺序：enable → set ADBKeyboard → 广播 → set 回原输入法 → disable
    idx = [next(i for i, c in enumerate(joined_calls) if pat in c)
           for pat in ("ime enable", "ADB_INPUT_B64", "ime set com.gboard", "ime disable")]
    assert idx == sorted(idx)
    # 广播内容是 UTF-8 base64
    broadcast_line = next(c for c in joined_calls if "ADB_INPUT_B64" in c)
    b64 = broadcast_line.split("msg ")[1].strip()
    assert base64.b64decode(b64).decode("utf-8") == "你好小鱼"
    # 恢复原输入法
    assert any("ime set com.gboard/.Gboard" in c for c in joined_calls)
    assert any("ime disable com.android.adbkeyboard" in c for c in joined_calls)


def test_unicode_requires_settings_read(tmp_path):
    adb, runner = _device_with_runner()
    # settings get 返回空 → 中止而不锁死输入法
    with pytest.raises(AdbError, match="输入法"):
        adb.type_unicode("EMU1", "你好")


def test_devices_parse(tmp_path):
    adb, runner = _device_with_runner()
    runner.expect(
        "devices",
        "List of devices attached\nEMU1\tdevice\nABC\toffline\n",
    )
    rows = adb.devices()
    assert rows == [{"serial": "EMU1", "state": "device"}, {"serial": "ABC", "state": "offline"}]


def test_require_device(tmp_path):
    adb, runner = _device_with_runner()
    runner.expect("devices", b"List of devices attached\n")
    with pytest.raises(AdbError, match="没有已授权"):
        adb.require_device()
    runner.expect("devices", b"List of devices attached\nA\tdevice\nB\tdevice\n")
    with pytest.raises(AdbError, match="多台设备"):
        adb.require_device()


def test_adb_missing_binary():
    adb = AdbDevice(adb_path="adb-not-exist-xyz", runner=None)
    with pytest.raises(AdbError, match="PATH"):
        adb.devices()


# ---------- UI 解析 ----------

SAMPLE_XML = (
    '<hierarchy rotation="0">'
    '<node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false"/>'
    '<node class="android.widget.TextView" text="登录" bounds="[100,200][300,260]" clickable="true"/>'
    '<node class="android.widget.EditText" resource-id="com.x:id/input" bounds="[50,300][500,360]" clickable="true"/>'
    '<node class="android.view.View" bounds="[10,10][20,20]"/>'
    "</hierarchy>"
)


def test_parse_elements():
    elements = parse_elements(SAMPLE_XML)
    # 纯容器与无信息节点被剔除
    assert len(elements) == 2
    assert elements[0]["text"] == "登录" and elements[0]["center"] == (200, 230)
    assert elements[1]["resource_id"] == "com.x:id/input"
    assert elements[1]["clickable"] is True


def test_parse_elements_max_nodes():
    many = "<hierarchy>" + "".join(
        f'<node class="android.widget.TextView" text="t{i}" bounds="[{i},0][{i + 10},20]" clickable="true"/>'
        for i in range(200)
    ) + "</hierarchy>"
    assert len(parse_elements(many, max_nodes=50)) == 50


def test_parse_bad_xml():
    with pytest.raises(Exception):
        parse_elements("<not-xml")


def test_render_tree_format():
    elements = parse_elements(SAMPLE_XML)
    tree = render_tree(elements)
    assert '[0] TextView "登录" @(200,230) [可点击]' in tree
    assert "上滑" in tree or "可滚动" not in tree.split("\n")[0]


# ---------- 驱动（tap_element 依赖缓存树） ----------

def test_driver_tap_element_uses_cached_tree(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("uiautomator dump", "")
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML)
    runner.expect("rm /sdcard/omni_uidump.xml", "")
    adb = AdbDevice(adb_path="adb", runner=runner)
    driver = AndroidDriver(adb=adb, serial="EMU1", cache_path=tmp_path / "tree.json")

    tree = driver.refresh_tree()
    assert '"登录"' in tree
    out = driver.tap_element(0)
    assert "已点击 [0]" in out
    assert any("input tap 200 230" in " ".join(c) for c in runner.calls)


def test_driver_tap_element_refreshes_when_stale(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("uiautomator dump", "")
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML)
    runner.expect("rm /sdcard/omni_uidump.xml", "")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    # 缓存为空时 tap_element 自动刷新树后能命中
    out = driver.tap_element(0)
    assert "已点击 [0]" in out


def test_driver_screenshot(tmp_path):
    runner = FakeRunner()
    runner.expect("screencap", b"\x89PNG\r\n\x1a\n" + b"data")
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    path = driver.screenshot()
    assert path.exists() and path.read_bytes().startswith(b"\x89PNG")


# ---------- 缓存持久化（跨进程恢复 + 陈旧防御） ----------

def _dump_runner() -> FakeRunner:
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("uiautomator dump", "")
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML)
    runner.expect("rm /sdcard/omni_uidump.xml", "")
    return runner


def test_driver_persists_and_restores_tree(tmp_path):
    import json

    cache = tmp_path / "sub" / "tree.json"
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    driver.refresh_tree()
    assert cache.exists()
    raw = json.loads(cache.read_text(encoding="utf-8"))
    assert raw["serial"] == "EMU1" and raw["captured_at"]

    # 新实例（模拟进程重启）从磁盘恢复，无需任何设备调用
    driver2 = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=FakeRunner()),
        serial="EMU1",
        cache_path=cache,
    )
    assert '"登录"' in driver2.tree()
    assert len(driver2.elements()) == 2


def test_tap_on_fresh_restored_cache_skips_dump(tmp_path):
    cache = tmp_path / "tree.json"
    first = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    first.refresh_tree()

    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    second = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=cache,
    )
    out = second.tap_element(0)
    assert "已点击 [0]" in out
    assert not runner.calls_matching("uiautomator")  # 新鲜缓存直接点，不重新 dump


def test_tap_on_stale_cache_refreshes_first(tmp_path):
    import datetime as dt
    import json

    cache = tmp_path / "tree.json"
    first = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    first.refresh_tree()
    # 把缓存时间改到 2 小时前（模拟长时间空闲后重启）
    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["captured_at"] = (
        dt.datetime.now().astimezone() - dt.timedelta(hours=2)
    ).isoformat(timespec="seconds")
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    runner = _dump_runner()
    second = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=cache,
    )
    out = second.tap_element(0)
    assert "已点击 [0]" in out
    assert runner.calls_matching("uiautomator")  # 陈旧缓存：先重新观察再点


def test_tap_on_serial_mismatch_refreshes_first(tmp_path):
    """缓存属于 EMU1、当前解析到另一台设备时必须重新观察（防点错屏幕）。"""
    cache = tmp_path / "tree.json"
    first = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    first.refresh_tree()

    runner = _dump_runner()
    second = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=cache,
    )
    # 偷换缓存 serial 模拟"这份树是别的设备观察来的"
    import json

    raw = json.loads(cache.read_text(encoding="utf-8"))
    raw["serial"] = "OTHER"
    cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    second._load_cache()
    out = second.tap_element(0)
    assert "已点击 [0]" in out
    assert runner.calls_matching("uiautomator")


def test_corrupted_cache_is_tolerated(tmp_path):
    cache = tmp_path / "tree.json"
    cache.write_text("{broken json", encoding="utf-8")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    assert driver.tree() == ""
    out = driver.tap_element(0)  # 空缓存 → 自动刷新后命中
    assert "已点击 [0]" in out


# ---------- 工作台点屏（坐标换算唯一入口） ----------

def test_scale_coords():
    from omni.tools.android.adb import AdbError
    from omni.tools.android.driver import scale_coords

    assert scale_coords(100, 200, 500, 1000, 1080, 2400) == (216, 480)
    assert scale_coords(0, 0, 100, 100, 1080, 2400) == (0, 0)
    with pytest.raises(AdbError):
        scale_coords(1, 1, 0, 100, 1080, 2400)  # 非法帧尺寸


def test_driver_tap_scaled(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("wm size", "Physical size: 1080x2400\n")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    out = driver.tap_scaled(100, 200, 500, 1000)
    assert "(216,480)" in out
    assert any("input tap 216 480" in " ".join(c) for c in runner.calls)


def test_driver_screenshot_bytes(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("screencap", b"\x89PNG\r\n\x1a\n" + b"data")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    assert driver.screenshot_bytes().startswith(b"\x89PNG")


# ---------- 工具派发 ----------

def test_android_tools_dispatch(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    driver = AndroidDriver(adb=AdbDevice(adb_path="adb", runner=runner), serial="EMU1")
    registry = ToolRegistry()
    from omni.tools.android.tools import register_android_tools

    register_android_tools(registry, driver)
    names = set(registry.names())
    assert {
        "android_status", "android_screen_tree", "android_screenshot",
        "android_tap_element", "android_tap_coords", "android_swipe",
        "android_key", "android_type",
    } <= names
    # 风险分级：读 SAFE，写 CONFIRM
    assert registry.get("android_screen_tree").risk.value == "safe"
    assert registry.get("android_tap_element").risk.value == "confirm"
    # SAFE 读操作直通
    out = registry.dispatch("android_status", "{}")
    assert "EMU1" in out
    # 写操作错误以字符串回传（离线环境无真实设备树时的降级提示）
    assert registry.dispatch("android_type", {"text": ""}, approver=AllowAllApprover()).startswith("错误")
