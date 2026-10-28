"""UI 树解析（纯函数）：uiautomator XML → 结构化元素 → 文本树。

输出对齐 ghost-in-the-droid 的 get_screen_tree 风格：
- 元素带 [idx] 索引（agent 点 tap_element(idx, observation)，索引比裸坐标鲁棒）；
- 只保留有信息量的节点（有文字/描述，或可点击/可滚动）；
- 超过 max_nodes 截断；末尾提示可滚动。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
MAX_NODES = 80


class UiParseError(ValueError):
    """UI 树 XML 解析失败。"""


def _center(bounds: str) -> tuple[int, int] | None:
    m = _BOUNDS_RE.search(bounds or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def parse_elements(xml_text: str, max_nodes: int = MAX_NODES) -> list[dict[str, Any]]:
    """解析 uiautomator dump XML → 元素列表（idx 从 0 连续编号）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UiParseError(f"UI 树 XML 解析失败：{exc}") from exc
    elements: list[dict[str, Any]] = []
    for node in root.iter("node"):
        attrib = node.attrib
        text = (attrib.get("text") or "").strip()
        desc = (attrib.get("content-desc") or "").strip()
        rid = (attrib.get("resource-id") or "").strip()
        password = attrib.get('password') == 'true'
        if password:
            text, desc = '[密码框已隐藏]', ''
        clickable = attrib.get("clickable") == "true"
        scrollable = attrib.get("scrollable") == "true"
        if not (text or desc or rid or clickable or scrollable):
            continue
        center = _center(attrib.get("bounds", ""))
        if center is None:
            continue
        elements.append(
            {
                "idx": len(elements),
                "text": text,
                "content_desc": desc,
                "resource_id": rid,
                "class": attrib.get("class", ""),
                "bounds": attrib.get("bounds", ""),
                "center": center,
                "clickable": clickable,
                "scrollable": scrollable,
                "password": password,
                "enabled": attrib.get('enabled') != 'false',
            }
        )
        if len(elements) >= max_nodes:
            break
    return elements


def render_tree(elements: list[dict[str, Any]]) -> str:
    """渲染成 agent 可读的缩进树：[idx] 类名 "文本" @坐标 [可点击]。"""
    if not elements:
        return "（UI 树为空：页面可能还在加载，稍等再刷新一次）"
    lines = []
    for e in elements:
        label = e["text"] or e["content_desc"] or e["resource_id"] or ""
        cx, cy = e["center"]
        flags = []
        if e["clickable"]:
            flags.append("可点击")
        if e["scrollable"]:
            flags.append("可滚动")
        flag_text = (" [" + "/".join(flags) + "]") if flags else ""
        cls = e["class"].rsplit(".", 1)[-1]
        lines.append(f'[{e["idx"]}] {cls} "{label}" @({cx},{cy}){flag_text}')
    lines.append("（提示：点 tap_element 用 [idx]；页面底部内容可能需要上滑）")
    return "\n".join(lines)
