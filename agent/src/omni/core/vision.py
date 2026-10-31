"""工具产物 → 模型图像块（D15/A13）。

A13 的问题：截图工具只返回一个路径字符串，模型从来没"看"到过任何像素。
这里的职责是把已授权（`vision.send_to_cloud`）的产物字节转成模型能消费的
图像块，并且：

- 长边超 `max_edge_px` 先缩放（省 token、也省上下文窗口）；
- 重编码为 JPEG（截图场景下无损 PNG 没有意义，体积差一个数量级）；
- 超过 `max_image_bytes` 的直接不给——"发了但被供应商拒收"比"不发"更浪费；
- **原图坐标要明确**：缩放后的图给模型看，坐标换算说明放在文本里，
  模型报的坐标按原图口径校正，避免"模型说点 (780, 441)、实际是 (960, 540)"
  这种错位。

明确不做：不做 OCR、不做图像内容审核（那是上游审批器的职责）；
GIF 动图不缩放重编码（丢帧），超限就拒绝。
"""
from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

from ..memory.artifacts import Artifact


# 发给模型的消息里，图像块的形态（OpenAI 兼容；LiteLLM 会按供应商归一）
def image_part(data: bytes, mime: str) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{encoded}"},
    }


def prepare_image(
    data: bytes, *, max_edge_px: int, max_bytes: int, original_width: int | None = None,
    original_height: int | None = None,
) -> tuple[bytes, str, int, int, int, int] | None:
    """把产物图像准备成可发送形态。

    返回 `(jpeg 字节, mime, 宽, 高, 原宽, 原高)`；超限/损坏/非静态图返回 None。
    原始尺寸优先用登记值（截图时的真实尺寸），缺省退回解码值。
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - pillow 是固定依赖，防的是环境异常
        return None
    try:
        with Image.open(BytesIO(data)) as src:
            if getattr(src, "is_animated", False):
                return None  # 动图缩放会丢帧，不做
            if src.mode not in ("RGB", "L"):
                img = src.convert("RGB")
            else:
                img = src.copy()
    except Exception:  # noqa: BLE001 —— 损坏/非图像数据
        return None
    orig_w = original_width or img.size[0]
    orig_h = original_height or img.size[1]
    try:
        img.thumbnail((max(1, max_edge_px), max(1, max_edge_px)))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=88)
        payload = buf.getvalue()
    except Exception:  # noqa: BLE001
        return None
    if not payload or len(payload) > max_bytes:
        return None
    return payload, "image/jpeg", img.size[0], img.size[1], orig_w, orig_h


def coordinate_note(width: int, height: int, orig_width: int, orig_height: int) -> str:
    """缩放坐标系说明：模型看的是缩放图，坐标必须换算回原图口径。"""
    if (width, height) == (orig_width, orig_height):
        return f"图像尺寸 {width}x{height}（未缩放）。"
    sx = orig_width / width if width else 1.0
    sy = orig_height / height if height else 1.0
    return (
        f"你看到的是缩放图 {width}x{height}；原图 {orig_width}x{orig_height}。"
        f"报告坐标时一律按原图：x 乘 {sx:.3f}、y 乘 {sy:.3f} 后取整。"
    )


def artifact_text(artifact: Artifact) -> str:
    """工具结果里引用产物的标准写法。"""
    dims = f"{artifact.width}x{artifact.height}" if artifact.width and artifact.height else "尺寸未知"
    return (
        f"截图已存为产物 [artifact:{artifact.artifact_id}]"
        f"（PNG，{dims}，{artifact.size // 1024}KB）"
    )
