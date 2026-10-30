"""截图工具与 ArtifactStore 的桥（D15/A13）。

browser/android 截图共用这一段：字节 → 产物登记 → 给模型/用户的结果文本。
文本里带 `[artifact:<id>]` 标记：
- Agent 看到标记（且隐私开关打开）就把图像转成模型图像块；
- 工作台看到标记就渲染成内联图片；
- 没开 `vision.send_to_cloud` 时，文本明确说"模型看不到这张图"，
  不让模型误以为看过（A13 的另一半）。
"""
from __future__ import annotations

from io import BytesIO
from typing import Any, Protocol


class _Artifacts(Protocol):  # 结构化 ArtifactStore，避免 tools 直接 import memory 具体类
    def save_image(self, data: bytes, mime: str, *, session_id: int | None,
                   run_id: str | None, scope: str, kind: str, source: str,
                   width: int | None, height: int | None) -> Any: ...


def _dims(png: bytes) -> tuple[int | None, int | None]:
    """PNG 尺寸（IHDR 头就在前 24 字节，不用解码整图）。"""
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    import struct

    width, height = struct.unpack(">II", png[16:24])
    return int(width), int(height)


def save_screenshot(
    artifacts: _Artifacts | None,
    data: bytes,
    *,
    session_id: int | None,
    run_id: str | None,
    kind: str,
    source: str,
    can_view: bool,
) -> str:
    """存产物并生成工具结果文本。`artifacts` 为 None 时降级为旧行为（路径模式由调用方处理）。"""
    if artifacts is None:
        # 生产装配总是有产物库；这个分支保护嵌入式/测试用法里漏了守卫的调用方，
        # 把 AttributeError 崩溃换成一句可解释的错误，而不是半截结果
        return "错误：产物库不可用，截图未保存（嵌入式装配需注入 artifacts）"
    width, height = _dims(data)
    try:
        artifact = artifacts.save_image(
            data, "image/png", session_id=session_id, run_id=run_id,
            scope="private", kind=kind, source=source, width=width, height=height,
        )
    except Exception as exc:  # noqa: BLE001 —— 保存失败要可解释，不能吞
        return f"错误：截图产物保存失败（{exc}）"
    dims = f"{width}x{height}" if width and height else "尺寸未知"
    ref = f"[artifact:{artifact.artifact_id}]"
    if can_view:
        return (
            f"截图已存为产物 {ref}（PNG，{dims}，{len(data) // 1024}KB）。"
            "这张图已作为图像内容提供给模型，可以直接描述/分析它。"
        )
    return (
        f"截图已存为产物 {ref}（PNG，{dims}，{len(data) // 1024}KB）。"
        "注意：当前未开启 vision.send_to_cloud，模型看不到图像内容；"
        "可在工作台「产物」面板查看，或用 read 读页面文本。"
    )
