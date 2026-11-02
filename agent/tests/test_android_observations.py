"""全动作令牌：设备/会话/旋转/焦点、帧、取消和迟到/重复拒绝。"""

from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from omni.tools.android.driver import AndroidDriver
from omni.tools.android.adb import AdbDevice, AdbError
from test_android import FakeRunner, SAMPLE_XML, _dump_runner


@pytest.fixture
def env(tmp_path):
    runner = _dump_runner()
    xml = SAMPLE_XML.replace('resource-id="com.x:id/input"', 'resource-id="com.x:id/input" focused="true"')
    runner.expect("cat /sdcard/omni_uidump.xml", xml)
    runner.expect("settings get secure default_input_method", "com.gboard/.Gboard")
    runner.expect("settings get secure enabled_input_methods", "com.gboard/.Gboard")
    png = BytesIO()
    Image.new("RGB", (1080, 2400)).save(png, format="PNG")
    runner.expect("screencap", png.getvalue())
    now = [0.0]
    driver = AndroidDriver(
        adb=AdbDevice(adb_path="fake", runner=runner), cache_path=tmp_path / "tree.json", clock=lambda: now[0]
    )
    return SimpleNamespace(driver=driver, runner=runner, xml=xml, now=now)


CASES = [
    ("tap_element", {"idx": 0}),
    ("tap_coords", {"x": 200, "y": 230}),
    ("swipe", {"direction": "up"}),
    ("key", {"keycode": "KEYCODE_BACK"}),
    ("type_text", {"text": "你好"}),
]


def _writes(runner):
    return [
        c
        for c in runner.calls
        if any(p in " ".join(c) for p in ("input tap", "input swipe", "input keyevent", "input text", "ADB_INPUT_B64"))
    ]


@pytest.mark.parametrize("action,args", CASES)
def test_all_actions_are_single_use_and_report_unverified_effect(env, action, args):
    _, token, _ = env.driver.snapshot(session_id=1)
    result = env.driver.execute_action(action, token, session_id=1, **args)
    assert result.status == "unknown" and result.side_effect_state == "unknown"
    assert len(_writes(env.runner)) == 1
    assert env.driver.execute_action(action, token, session_id=1, **args).status == "error"
    assert len(_writes(env.runner)) == 1


@pytest.mark.parametrize(
    "change", ["device", "rotation", "focus", "package", "bounds", "ttl", "session", "cancel", "pixels"]
)
def test_changed_context_never_writes(env, change):
    _, token, _ = env.driver.snapshot(session_id=1)
    session, cancel = 1, None
    if change == "device":
        env.runner.expect("devices", "List of devices attached\nOTHER\tdevice\n")
    elif change == "rotation":
        env.runner.expect("cat /sdcard/omni_uidump.xml", env.xml.replace('rotation="0"', 'rotation="1"'))
    elif change == "focus":
        env.runner.expect("cat /sdcard/omni_uidump.xml", env.xml.replace('focused="true"', 'focused="false"'))
    elif change == "package":
        env.runner.expect("cat /sdcard/omni_uidump.xml", env.xml.replace("<node ", '<node package="other.app" ', 1))
    elif change == "bounds":
        env.runner.expect("cat /sdcard/omni_uidump.xml", env.xml.replace("[100,200][300,260]", "[101,200][299,260]"))
    elif change == "ttl":
        env.now[0] = 121
    elif change == "session":
        session = 2
    elif change == "cancel":
        cancel = SimpleNamespace(cancelled=True)
    else:
        png = BytesIO()
        Image.new("RGB", (1080, 2400), "red").save(png, format="PNG")
        env.runner.expect("screencap", png.getvalue())
    result = env.driver.execute_action("tap_coords", token, session_id=session, cancel=cancel, x=200, y=230)
    assert result.status in {"error", "cancelled"}
    assert not _writes(env.runner)


def test_password_hidden_and_input_refused(env):
    env.runner.expect(
        "cat /sdcard/omni_uidump.xml",
        env.xml.replace('focused="true"', 'focused="true" password="true" text="secret-value"'),
    )
    tree = env.driver.refresh_tree(session_id=1)
    assert "secret-value" not in tree
    token = env.driver.observation_id
    assert env.driver.execute_action("type_text", token, session_id=1, text="abc").status == "error"
    assert not _writes(env.runner)


def test_coordinate_requires_snapshot_and_rejects_out_of_frame(env):
    env.driver.refresh_tree(session_id=1)
    assert env.driver.execute_action("tap_coords", env.driver.observation_id, session_id=1, x=1, y=1).status == "error"
    _, token, _ = env.driver.snapshot(session_id=1)
    assert (
        env.driver.execute_action("tap_scaled", token, session_id=1, x=-1, y=1, stream_w=540, stream_h=1200).status
        == "error"
    )
    assert not _writes(env.runner)


def test_ime_switch_failure_still_restores_original():
    class Runner(FakeRunner):
        def __call__(self, argv, timeout):
            result = super().__call__(argv, timeout)
            if "ime set com.android.adbkeyboard/.AdbIME" in " ".join(argv):
                raise AdbError("lost confirmation")
            return result

    runner = Runner()
    runner.expect("settings get secure default_input_method", "com.gboard/.Gboard")
    with pytest.raises(AdbError):
        AdbDevice(adb_path="fake", runner=runner).type_unicode("EMU1", "中文")
    commands = [" ".join(c) for c in runner.calls]
    assert any("ime set com.gboard/.Gboard" in c for c in commands)
    assert not any("ADB_INPUT_B64" in c for c in commands)


def test_existing_adb_ime_is_not_disabled():
    runner = FakeRunner()
    runner.expect("settings get secure default_input_method", "com.android.adbkeyboard/.AdbIME")
    AdbDevice(adb_path="fake", runner=runner).type_unicode("EMU1", "中文")
    assert not any("ime disable" in " ".join(c) for c in runner.calls)


def test_ime_name_from_device_cannot_inject_shell():
    runner = FakeRunner()
    runner.expect("settings get secure default_input_method", "com.ime/.IME; reboot")
    with pytest.raises(AdbError):
        AdbDevice(adb_path="fake", runner=runner).type_unicode("EMU1", "中文")
    assert not any("ime set" in " ".join(c) for c in runner.calls)


def test_focus_change_during_ime_switch_blocks_text_and_restores(env):
    original = env.runner.__class__

    class SwitchFocus(original):
        def __call__(self, argv, timeout):
            value = super().__call__(argv, timeout)
            if "ime set com.android.adbkeyboard/.AdbIME" in " ".join(argv):
                self.expect("cat /sdcard/omni_uidump.xml", env.xml.replace('focused="true"', 'focused="false"'))
            return value

    env.runner.__class__ = SwitchFocus
    env.driver.refresh_tree(session_id=1)
    result = env.driver.execute_action("type_text", env.driver.observation_id, session_id=1, text="不能输入错位置")
    assert result.status == "unknown"  # 已切换输入法，有副作用；未发送文字。
    assert not _writes(env.runner)
    assert any("ime set com.gboard/.Gboard" in " ".join(c) for c in env.runner.calls)


def test_cross_instance_file_lock_blocks_device_action(env, tmp_path):
    env.driver.refresh_tree(session_id=1)
    token = env.driver.observation_id
    with env.driver._device_lock("EMU1"):
        result = env.driver.execute_action("key", token, session_id=1, keycode="KEYCODE_HOME")
    assert result.status == "error" and not _writes(env.runner)


def test_workbench_snapshot_binds_click_and_replay_is_rejected(env, tmp_path):
    from test_web import _client

    client, runtime = _client(tmp_path)
    runtime.android = env.driver
    try:
        snap = client.get("/api/android/snapshot")
        assert snap.status_code == 200 and snap.json()["image"].startswith("data:image/png;base64,")
        body = {"x": 100, "y": 200, "stream_w": 540, "stream_h": 1200, "observation": snap.json()["observation"]}
        result = client.post("/api/android/tap", json=body)
        assert result.status_code == 200 and result.json()["status"] == "unknown" and not result.json()["ok"]
        assert client.post("/api/android/tap", json=body).status_code == 409
        assert len(_writes(env.runner)) == 1
    finally:
        assert runtime.close()["complete"]
