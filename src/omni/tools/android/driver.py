"""Android 观察令牌、单设备互斥、一次动作与回读。磁盘缓存仅用于展示。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ...config import DATA_DIR
from ...system.filelock import FileLock
from ..result import ToolResult
from .adb import AdbDevice, AdbError
from .ui import parse_elements, render_tree


def observation_id(serial: str, tree_text: str) -> str:
    return hashlib.sha256(f'{serial}\n{tree_text}'.encode('utf-8')).hexdigest()


def scale_coords(x: int, y: int, stream_w: int, stream_h: int, real_w: int, real_h: int) -> tuple[int, int]:
    if min(stream_w, stream_h, real_w, real_h) <= 0 or not 0 <= x < stream_w or not 0 <= y < stream_h:
        raise AdbError('帧尺寸必须为正，坐标必须位于帧内')
    return min(real_w - 1, round(x * real_w / stream_w)), min(real_h - 1, round(y * real_h / stream_h))


def _pixels(png: bytes) -> tuple[str, tuple[int, int]]:
    with Image.open(BytesIO(png)) as image:
        if image.width * image.height > 32_000_000:
            raise AdbError('屏幕图像过大')
        with image.convert('RGB') as rgb:
            return hashlib.sha256(rgb.tobytes()).hexdigest(), rgb.size


class AndroidDriver:
    def __init__(self, adb: AdbDevice | None = None, serial: str | None = None, *,
                 cache_path: Path | None = None, shots_dir: Path | None = None,
                 observation_ttl_s: float = 120, clock=time.monotonic) -> None:
        self._adb = adb or AdbDevice(serial=serial)
        self._cache_path = Path(cache_path or DATA_DIR / 'android_cache/tree.json')
        self._shots_dir = Path(shots_dir or self._cache_path.parent / 'shots')
        self._ttl, self._clock = observation_ttl_s, clock
        self._lock = threading.RLock()
        self._observations: dict[str, dict] = {}
        self._elements, self._tree_text = [], ''
        self._screen = self._cache_serial = self._captured_at = self._observation_id = None
        try:
            raw = json.loads(self._cache_path.read_text(encoding='utf-8'))
            self._elements, self._tree_text = raw['elements'], raw['tree_text']
            self._cache_serial, self._captured_at = raw['serial'], raw['captured_at']
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _device_lock(self, serial: str):
        name = hashlib.sha256(serial.encode()).hexdigest()[:24]
        return FileLock(self._cache_path.parent / f'device-{name}.lock', timeout_s=2)

    def _capture(self, serial: str, *, pixels: bool = False) -> dict:
        xml = self._adb.dump_ui(serial)
        if len(xml) > 2_000_000:
            raise AdbError('UI 树过大')
        root = ET.fromstring(xml)
        nodes = list(root.iter('node'))
        if not nodes or len(nodes) > 3000:
            raise AdbError('UI 树为空或过大，无法核验界面')
        rotation = int(root.attrib.get('rotation', '0'))
        if rotation not in (0, 1, 2, 3):
            raise AdbError('屏幕方向无法核验')
        width, height = self._adb.screen_size(serial)
        size = (height, width) if rotation % 2 else (width, height)
        raw = ET.tostring(root, encoding='unicode')
        state = {'serial': serial, 'fingerprint': observation_id(serial, raw + repr(size)),
                 'size': size, 'elements': parse_elements(xml), 'nodes': [dict(n.attrib) for n in nodes]}
        if pixels:
            png = self._adb.screencap(serial)
            state['pixels'], actual_size = _pixels(png)
            if actual_size != size:
                raise AdbError('屏幕截图与方向/尺寸不一致，拒绝坐标操作')
            if ET.tostring(ET.fromstring(self._adb.dump_ui(serial)), encoding='unicode') != raw:
                raise AdbError('截图期间界面已变化，请重新观察')
            state['png'] = png
        return state

    def _remember(self, state: dict, session_id=None) -> str:
        token = uuid.uuid4().hex
        while len(self._observations) >= 32:
            self._observations.pop(next(iter(self._observations)))
        self._observations[token] = {**{k: v for k, v in state.items() if k != 'png'}, 'session': session_id, 'at': self._clock()}
        self._elements, self._tree_text = state['elements'], render_tree(state['elements'])
        self._screen, self._cache_serial = state['size'], state['serial']
        self._captured_at = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        self._observation_id = token
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_name(f'.{uuid.uuid4().hex}.tmp')
            temporary.write_text(json.dumps({'serial': self._cache_serial, 'captured_at': self._captured_at,
                                             'elements': self._elements, 'tree_text': self._tree_text}, ensure_ascii=False), encoding='utf-8')
            os.replace(temporary, self._cache_path)
        except OSError:
            pass
        return self.observation_header() + '\n' + self._tree_text

    def refresh_tree(self, *, session_id=None) -> str:
        with self._lock:
            serial = self._adb.require_device()
            with self._device_lock(serial):
                return self._remember(self._capture(serial), session_id)

    def snapshot(self, *, session_id=None) -> tuple[bytes, str, tuple[int, int]]:
        with self._lock:
            serial = self._adb.require_device()
            with self._device_lock(serial):
                state = self._capture(serial, pixels=True)
                self._remember(state, session_id)
                return state['png'], self._observation_id, state['size']

    def observe(self, *, session_id=None) -> dict:
        with self._lock:
            tree = self.refresh_tree(session_id=session_id)
            return {'tree': tree, 'observation': self._observation_id, 'elements': self.elements()}

    def observation_header(self) -> str:
        return f'观察 obs={self._observation_id or "无效，请重新观察"}（设备 {self._cache_serial}，{self._captured_at}；单次有效）'

    @property
    def observation_id(self) -> str | None:
        return self._observation_id

    def tree(self) -> str:
        return self._tree_text

    def elements(self) -> list[dict[str, Any]]:
        return list(self._elements)

    def screenshot(self) -> Path:
        data = self.screenshot_bytes()
        self._shots_dir.mkdir(parents=True, exist_ok=True)
        target = self._shots_dir / 'latest.png'
        target.write_bytes(data)
        return target

    def screenshot_bytes(self) -> bytes:
        return self._adb.screencap(self._adb.require_device())

    def screen_size(self) -> tuple[int, int]:
        return self._adb.screen_size(self._adb.require_device())

    def execute_action(self, action: str, observation: str, *, session_id=None, cancel=None, **args) -> ToolResult:
        attempted = False
        with self._lock:
            try:
                expected = self._observations.get(observation)
                if not expected or expected['session'] != session_id or self._clock() - expected['at'] > self._ttl:
                    raise AdbError('observation 缺失、过期、已使用或不属于本会话；请重新观察')
                if action not in {'tap_element', 'tap_coords', 'tap_scaled', 'swipe', 'key', 'type_text'}:
                    raise AdbError('未知安卓动作')
                if getattr(cancel, 'cancelled', False):
                    return ToolResult.cancelled('安卓动作已取消，未执行')
                serial = self._adb.require_device()
                with self._device_lock(serial):
                    coordinate = action in {'tap_coords', 'tap_scaled'}
                    if coordinate and 'pixels' not in expected:
                        raise AdbError('坐标动作必须引用 android_screenshot 或工作台锁定帧的 observation')
                    current = self._capture(serial, pixels=coordinate)
                    if current['fingerprint'] != expected['fingerprint'] or (coordinate and current['pixels'] != expected['pixels']):
                        self._observations.pop(observation, None)
                        latest = self._remember(current, session_id)
                        return ToolResult.fail('未点击/未执行：设备、界面或帧与观察不一致。\n' + latest, error_code='android_stale')
                    width, height = current['size']
                    if action == 'tap_element':
                        element = next((e for e in current['elements'] if e['idx'] == args['idx']), None)
                        if not element:
                            raise AdbError('索引不在当前 UI 树里')
                        if element.get('password') or not element.get('enabled', True):
                            raise AdbError('密码/禁用元素不允许操作')
                        x, y = element['center']
                        label = f"已点击 [{element['idx']}]"
                        command = lambda: self._adb.tap(serial, x, y)
                    elif coordinate:
                        x, y = int(args['x']), int(args['y'])
                        if action == 'tap_scaled':
                            x, y = scale_coords(x, y, int(args['stream_w']), int(args['stream_h']), width, height)
                        label, command = f'已点击 ({x},{y})', lambda: self._adb.tap(serial, x, y)
                    elif action == 'swipe':
                        direction = args['direction']
                        if direction not in {'up', 'down', 'back'}:
                            raise AdbError('direction 必须为 up/down/back')
                        start, end = ((.7, .3) if direction == 'up' else (.3, .7))
                        command = (lambda: self._adb.key(serial, 'KEYCODE_BACK')) if direction == 'back' else (
                            lambda: self._adb.swipe(serial, width // 2, int(height * start), width // 2, int(height * end), 400))
                        label = f'已下发 {direction}'
                    elif action == 'key':
                        from .adb import ADB_KEYCODE_RE
                        code = str(args['keycode']).strip().upper()
                        if not ADB_KEYCODE_RE.fullmatch(code):
                            raise AdbError('keycode 非法')
                        label, command = f'已按键 {code}', lambda: self._adb.key(serial, code)
                    else:
                        text = args['text']
                        focused = [n for n in current['nodes'] if n.get('focused') == 'true']
                        if not isinstance(text, str) or not text or len(text) > 2000 or any(ord(c) < 32 for c in text):
                            raise AdbError('输入内容为空、过长或含控制字符')
                        if len(focused) != 1 or focused[0].get('password') == 'true' or 'EditText' not in focused[0].get('class', ''):
                            raise AdbError('未能核验唯一非密码输入焦点')
                        if focused[0].get('enabled') == 'false':
                            raise AdbError('输入框已禁用')
                        unicode_input = any(ord(c) > 127 for c in text) or "'" in text or '%s' in text
                        def before_input():
                            if getattr(cancel, 'cancelled', False):
                                raise AdbError('输入法切换后运行已取消')
                            focused_now = [n for n in self._capture(serial)['nodes'] if n.get('focused') == 'true']
                            if focused_now != focused:
                                raise AdbError('输入法切换后焦点已变化，未发送文本')
                        command = (lambda: self._adb.type_unicode(serial, text, before_input=before_input)) if unicode_input else (lambda: self._adb.type_ascii(serial, text))
                        label = '已下发输入'
                    if action.startswith('tap') and not (0 <= x < width and 0 <= y < height):
                        raise AdbError('坐标越界，未执行')
                    if getattr(cancel, 'cancelled', False):
                        return ToolResult.cancelled('安卓动作已取消，未执行')
                    self._observations.pop(observation)
                    attempted = True
                    command()
                    after = self._capture(serial)
                    latest = self._remember(after, session_id)
                    changed = after['fingerprint'] != expected['fingerprint']
                    note = '界面有变化' if changed else '界面未变化'
                    # 树变化不等于业务目标达成；不把 ADB 返回码当成发送/购买成功。
                    return ToolResult.unknown(f'{label}；{note}。点击后观察/动作后观察：\n{latest}\n动作已下发，目标效果需核查，禁止重复旧动作。',
                                              error_code='android_effect_unverified', data={'observation': self._observation_id})
            except Exception as exc:
                if attempted:
                    return ToolResult.unknown(f'复核观察失败或动作返回异常（{type(exc).__name__}）；状态未知，禁止重放', error_code='android_unknown')
                return ToolResult.fail(f'错误：{exc}', error_code='android_rejected')

    def tap_element(self, idx: int, observation: str | None = None) -> str:
        if not observation:
            raise AdbError('tap_element 需要 observation')
        return self.execute_action('tap_element', observation, idx=idx).text

    def tap_coords(self, x: int, y: int, observation: str = '') -> str:
        return self.execute_action('tap_coords', observation, x=x, y=y).text

    def tap_scaled(self, x: int, y: int, stream_w: int, stream_h: int, observation: str = '') -> str:
        return self.execute_action('tap_scaled', observation, x=x, y=y, stream_w=stream_w, stream_h=stream_h).text

    def swipe(self, direction: str, observation: str = '') -> str:
        return self.execute_action('swipe', observation, direction=direction).text

    def key(self, keycode: str, observation: str = '') -> str:
        return self.execute_action('key', observation, keycode=keycode).text

    def type_text(self, text: str, observation: str = '') -> str:
        return self.execute_action('type_text', observation, text=text).text
