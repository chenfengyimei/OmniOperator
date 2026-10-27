"""用量（tokens）与价格：把"这次调用大概/实际花了多少"变成可记账的数字。（D10）

三条纪律：

1. **未知不是零**。供应商没报单价，就是"未知"，记 `None` 而不是 0；
   供应商没报 usage，也记 `None`，结算时按预留额度保守保留。
2. **本地模型是真免费**。`ollama/*` 这类在本机跑的模型，单价是确定的 0，
   与"不知道多少钱"必须分开——否则本地零成本任务会被当成未知费用。
3. **单价表离线**。LiteLLM 默认会去 GitHub 拉价格表（导入时就请求网络），
   这与"离线可跑、测试不联网"直接冲突；这里强制用随包发布的本地快照，
   造价查不到就承认查不到。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any

# 必须在 import litellm 之前：否则 LiteLLM 会在导入期向 GitHub 拉取价格表
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402  —— 有意在环境变量之后导入

from ..config import LLM as LLMConfig  # noqa: E402

MICRO = 1_000_000  # 1 美元 = 10^6 微美元（整数记账，不用浮点累计）

# 这些 provider 的模型跑在本机/自有服务上：单价 0 是事实，不是"未知"
LOCAL_PROVIDERS = ("ollama", "ollama_chat", "lm_studio", "vllm", "hosted_vllm", "local")


def provider_of(model: str) -> str:
    return model.split("/", 1)[0].lower() if "/" in model else ""


def micro_from_usd(usd: float | None) -> int | None:
    """美元 → 微美元整数；`None` 保持 `None`（未知不得变成 0）。"""
    if usd is None:
        return None
    micro = int(round(float(usd) * MICRO))
    if micro == 0 and usd > 0:  # 极小的真实花费不要被四舍五入抹成"免费"
        return 1
    return micro


def usd_from_micro(micro: int | None) -> float | None:
    return None if micro is None else round(micro / MICRO, 6)


@dataclass(frozen=True)
class Usage:
    """一次模型调用的 token 用量。`reported=False` 表示供应商没给。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reported: bool = False

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None and self.completion_tokens is None:
            return None
        return int(self.prompt_tokens or 0) + int(self.completion_tokens or 0)

    def brief(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reported": self.reported,
        }

    @classmethod
    def from_response(cls, response: Any) -> Usage:
        """从 LiteLLM 响应里取 usage；取不到就如实标未上报。"""
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        prompt = _int_or_none(_field(usage, "prompt_tokens"))
        completion = _int_or_none(_field(usage, "completion_tokens"))
        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            reported=prompt is not None and completion is not None,
        )


def _field(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


@dataclass(frozen=True)
class Price:
    """单价（每 token，美元）。`source` 说明这个数字是哪来的。"""

    input_per_token: float | None
    output_per_token: float | None
    source: str  # config | litellm | local_free | unknown

    @property
    def known(self) -> bool:
        return self.input_per_token is not None and self.output_per_token is not None

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        if not self.known:
            return None
        return float(self.input_per_token or 0) * max(0, prompt_tokens) + float(
            self.output_per_token or 0
        ) * max(0, completion_tokens)

    def brief(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "input_per_m": None
            if self.input_per_token is None
            else round(self.input_per_token * MICRO, 4),
            "output_per_m": None
            if self.output_per_token is None
            else round(self.output_per_token * MICRO, 4),
        }


def resolve_price(cfg: LLMConfig, model: str) -> Price:
    """单价解析顺序：配置声明 → LiteLLM 本地价格表 → 未知（本地模型记免费）。"""
    cap = cfg.capabilities.get(model)
    if cap is not None and cap.input_usd_per_m is not None and cap.output_usd_per_m is not None:
        return Price(
            cap.input_usd_per_m / MICRO, cap.output_usd_per_m / MICRO, "config"
        )
    try:
        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model, prompt_tokens=MICRO, completion_tokens=MICRO
        )
    except Exception:  # noqa: BLE001 —— 查不到价格是正常情况，不是错误
        return Price(None, None, "unknown")
    if not prompt_cost and not completion_cost:
        if provider_of(model) in LOCAL_PROVIDERS:
            return Price(0.0, 0.0, "local_free")
        # 单价 0 又不在本机：不要替供应商宣布"免费"
        return Price(None, None, "unknown")
    return Price(prompt_cost / MICRO, completion_cost / MICRO, "litellm")


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算输入 token（只用于预留额度，不用于计费）。

    中文约 1.5 字符/token、英文约 4 字符/token，取 2 字符/token 作为偏保守的中间值，
    另加固定开销覆盖工具 schema 与角色包装。
    """
    chars = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chars += len(content)
        elif isinstance(content, list):  # 多模态块：只按文本部分估
            chars += sum(
                len(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
            )
            chars += 256 * len(content)
        for call in message.get("tool_calls") or []:
            chars += len(str(call))
    return max(1, math.ceil(chars / 2) + 64 * max(1, len(messages) // 8) + 64)


def uses_vision(messages: list[dict[str, Any]]) -> bool:
    """消息里是否包含图像块（决定是否需要 vision 能力）。"""
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"image_url", "image", "input_image"}:
                return True
    return False
