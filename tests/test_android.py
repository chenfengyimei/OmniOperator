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


# ---------- 驱动（观察绑定点击，D16/A12） ----------

SAMPLE_XML_2 = (
    '<hierarchy rotation="0">'
    '<node class="android.widget.TextView" text="删除" bounds="[100,200][300,260]" clickable="true"/>'
    "</hierarchy>"
)


def _obs_of(tree_output: str) -> str:
    """从 refresh_tree 输出头部抠出 obs=<id>（模型就是从这里抄给 tap 的）。"""
    import re

    m = re.search(r"obs=([0-9a-f]{8})", tree_output)
    assert m, "观察输出必须带 obs 身份"
    return m.group(1)


def test_observation_id_binds_serial_and_content():
    """观察身份 = 设备 + 树内容：同界面稳定，换设备/换内容必变。"""
    from omni.tools.android.driver import observation_id

    assert observation_id("EMU1", "tree") == observation_id("EMU1", "tree")
    assert observation_id("EMU1", "tree") != observation_id("EMU1", "tree2")
    assert observation_id("EMU1", "tree") != observation_id("EMU2", "tree")


def test_driver_tap_element_verifies_and_reports_after(tmp_path):
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("uiautomator dump", "")
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML)
    runner.expect("rm /sdcard/omni_uidump.xml", "")
    adb = AdbDevice(adb_path="adb", runner=runner)
    driver = AndroidDriver(adb=adb, serial="EMU1", cache_path=tmp_path / "tree.json")

    tree = driver.refresh_tree()
    assert "obs=" in tree and '"登录"' in tree
    out = driver.tap_element(0, _obs_of(tree))
    assert "已点击 [0]" in out
    assert any("input tap 200 230" in " ".join(c) for c in runner.calls)
    # 点击后强制复检：同界面 → "未变化" + 点击后的新观察（省一次 screen_tree）
    assert "界面未变化" in out and "点击后观察" in out and "obs=" in out
    # 三次 dump：观察、点击前复核、点击后复检——A12 修复就是靠点击前那次复核
    assert len(runner.calls_matching("uiautomator")) == 3


def test_tap_without_observation_is_refused(tmp_path):
    """A12 的门：不带观察身份的索引点击直接拒绝，且不碰设备。"""
    runner = FakeRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    with pytest.raises(AdbError, match="observation"):
        driver.tap_element(0)
    assert runner.calls == []  # 拒绝发生在任何设备调用之前


def test_tap_refuses_when_screen_changed_since_observation(tmp_path):
    """A12 核心：观察之后界面变了（同 idx 已指向别的元素）→ 不点，返回新观察。"""
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
    first = driver.refresh_tree()
    # 审批等待期间屏幕换页：cat 现在返回另一棵树（后注册的 expect 优先）
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML_2)
    out = driver.tap_element(0, _obs_of(first))
    assert out.startswith("未点击") and "不一致" in out
    assert '"删除"' in out and "obs=" in out  # 返回新观察让模型重新选
    assert not any("input tap" in " ".join(c) for c in runner.calls)


def test_tap_post_verify_failure_reports_unknown(tmp_path):
    """点击成功但复核观察失败：如实报"状态未知"，不假装完成。"""

    class FlakyCatRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.taps = 0

        def __call__(self, argv, timeout):
            if any(a.startswith("input tap") for a in argv):
                self.taps += 1
            # 第一次点击之后 cat 开始返回坏数据（模拟点完设备瞬间掉线）
            if self.taps >= 1 and any(a.startswith("cat ") for a in argv):
                return b"<broken"
            return super().__call__(argv, timeout)

    runner = FlakyCatRunner()
    runner.expect("devices", "List of devices attached\nEMU1\tdevice\n")
    runner.expect("uiautomator dump", "")
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML)
    runner.expect("rm /sdcard/omni_uidump.xml", "")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    first = driver.refresh_tree()
    out = driver.tap_element(0, _obs_of(first))
    assert out.startswith("已点击 [0]")
    assert "复核观察失败" in out and "状态未知" in out
    assert any("input tap 200 230" in " ".join(c) for c in runner.calls)


def test_tap_idx_missing_in_verified_tree(tmp_path):
    runner = _dump_runner()
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=tmp_path / "tree.json",
    )
    first = driver.refresh_tree()
    with pytest.raises(AdbError, match="不在当前 UI 树"):
        driver.tap_element(99, _obs_of(first))
    assert not any("input tap" in " ".join(c) for c in runner.calls)


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


# ---------- 缓存持久化（跨进程恢复；点击不再依赖缓存新鲜度，见上方观察绑定） ----------

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
    # 观察身份也一并恢复：重启后旧观察仍可用于点击前复核比对
    assert driver2.observation_id == driver.observation_id


def test_tap_on_restored_cache_still_reverifies(tmp_path):
    """旧优化"新鲜缓存跳过 dump"已刻意移除：点击前必须复核（A12 的根因就是它）。"""
    cache = tmp_path / "tree.json"
    first = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    first_output = first.refresh_tree()

    runner = _dump_runner()
    second = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=cache,
    )
    out = second.tap_element(0, _obs_of(first_output))
    assert "已点击 [0]" in out
    # 哪怕缓存是刚写的：点击前仍然 dump 复核 + 点击后复检
    assert len(runner.calls_matching("uiautomator")) == 2


def test_tap_on_restored_cache_refuses_if_screen_moved_on(tmp_path):
    """重启恢复后界面已经换页：旧观察直接失效，不点。"""
    cache = tmp_path / "tree.json"
    first = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    first_output = first.refresh_tree()

    runner = _dump_runner()
    runner.expect("cat /sdcard/omni_uidump.xml", SAMPLE_XML_2)  # 屏幕已是另一页
    second = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=runner),
        serial="EMU1",
        cache_path=cache,
    )
    out = second.tap_element(0, _obs_of(first_output))
    assert out.startswith("未点击") and '"删除"' in out
    assert not any("input tap" in " ".join(c) for c in runner.calls)


def test_corrupted_cache_is_tolerated(tmp_path):
    cache = tmp_path / "tree.json"
    cache.write_text("{broken json", encoding="utf-8")
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="adb", runner=_dump_runner()),
        serial="EMU1",
        cache_path=cache,
    )
    assert driver.tree() == ""
    # 空缓存：不带 observation 拒绝；带了走正常 dump→复核→点击
    with pytest.raises(AdbError, match="observation"):
        driver.tap_element(0)
    first = driver.refresh_tree()
    assert "已点击 [0]" in driver.tap_element(0, _obs_of(first))


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
    # 观察绑定：不带 observation 的点击在工具层就被拒（A12 的门，不碰设备）
    out = registry.dispatch("android_tap_element", {"idx": 0}, approver=AllowAllApprover())
    assert out.startswith("错误") and "observation" in out
    # 写操作错误以字符串回传（离线环境无真实设备树时的降级提示）
    assert registry.dispatch("android_type", {"text": ""}, approver=AllowAllApprover()).startswith("错误")
