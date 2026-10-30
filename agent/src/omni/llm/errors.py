"""模型调用的错误分类与脱敏（D10）。

为什么单独一层：以前 `gateway.complete` 把所有异常`except Exception` 包成一句
`f"模型 {model} 调用失败: {exc}"`。这带来三个问题：

1. **不可判定**：调用方（Agent / 通道）无法区分"密钥错了，重试一万次也没用"和
   "限流了，退避一下还能成"；于是只能整体失败或盲目重试。
2. **会泄密**：LiteLLM/OpenAI SDK 的异常文本里常带请求头、URL 和部分密钥，
   原样回显到网页或 QQ 就等于把凭据写进日志。
3. **无法解释**：界面只能说"失败了"，说不出这是配置问题、额度问题还是网络问题。

这里的纪律：
- 分类只看**异常类型名与 HTTP 状态码**，不依赖 LiteLLM 内部字段（版本间会变）；
- 可重试的只有"暂时性"故障（限流、连接、超时、5xx）；认证/参数/上下文超限一律不重试；
- 错误文本统一过 `sanitize`，密钥形态的串被替换成 `<已隐藏>`；
- **不切换供应商**：重试永远打同一个 model 串，换供应商等于换一份账单和一个数据去向。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 错误码：调用方/界面按它做分支，不要用文本匹配
AUTH = "auth"
PERMISSION = "permission"
RATE_LIMIT = "rate_limit"
TIMEOUT = "timeout"
CONNECTION = "connection"
SERVER = "server"
BAD_REQUEST = "bad_request"
CONTEXT_LENGTH = "context_length"
UNSUPPORTED = "unsupported_capability"
BUDGET = "budget_exhausted"
PRICE_UNKNOWN = "price_unknown"
CANCELLED = "cancelled"
STREAM_INTERRUPTED = "stream_interrupted"
UNKNOWN = "unknown"

RETRYABLE_CODES = frozenset({RATE_LIMIT, TIMEOUT, CONNECTION, SERVER})

# 供应商"重试也不会好"的异常：名字比 import 稳定（LiteLLM 会重命名类）
_NO_RETRY_NAMES = {
    "AuthenticationError": (AUTH, "密钥无效或缺失"),
    "PermissionDeniedError": (PERMISSION, "密钥没有该模型/接口的权限"),
    "NotFoundError": (BAD_REQUEST, "模型名或接口路径不存在"),
    "BadRequestError": (BAD_REQUEST, "请求参数不被该模型接受"),
    "InvalidRequestError": (BAD_REQUEST, "请求参数不被该模型接受"),
    "UnprocessableEntityError": (BAD_REQUEST, "请求参数不被该模型接受"),
    "ContextWindowExceededError": (CONTEXT_LENGTH, "上下文超出该模型的窗口上限"),
    "ContentPolicyViolationError": (BAD_REQUEST, "请求被供应商的内容策略拒绝"),
    "BudgetExceededError": (BUDGET, "供应商侧额度已耗尽"),
    "PaymentRequiredError": (AUTH, "供应商账户欠费或被限制"),
}

_RETRY_NAMES = {
    "RateLimitError": (RATE_LIMIT, "被供应商限流"),
    "Timeout": (TIMEOUT, "请求超时"),
    "APIConnectionError": (CONNECTION, "网络连接失败"),
    "APITimeoutError": (TIMEOUT, "请求超时"),
    "InternalServerError": (SERVER, "供应商 5xx"),
    "ServiceUnavailableError": (SERVER, "供应商 5xx"),
    "BadGatewayError": (SERVER, "供应商网关错误"),
    "APIError": (SERVER, "供应商返回错误"),
}

_HINTS = {
    AUTH: "检查 .env 里该供应商的密钥是否有效；错误文本已脱敏，不会回显密钥",
    PERMISSION: "该密钥没有此模型的权限，或需要开通对应服务",
    RATE_LIMIT: "供应商限流：已按有界退避重试，仍失败时请稍后再试",
    TIMEOUT: "请求超时：可调大 llm.request_timeout_s，或换更近的供应商",
    CONNECTION: "网络不通：本地模型请确认 Ollama 等已启动；远端请检查代理",
    SERVER: "供应商侧故障：与本机配置无关，稍后重试",
    BAD_REQUEST: "模型名/参数不被接受：核对 models 配置与所需参数",
    CONTEXT_LENGTH: "本轮上下文过长：可调小 limits.max_history_messages 或开启压缩",
    UNSUPPORTED: "该模型未声明此项能力：在 config 的 llm.capabilities 里如实声明",
    BUDGET: "费用额度已用尽：这是确定性代码拦截，未发出新的模型请求",
    PRICE_UNKNOWN: "该模型未声明单价，无法计入费用上限：请声明价格或标为免费",
    CANCELLED: "运行已被取消，未继续发起模型请求",
    STREAM_INTERRUPTED: "流式回答中途断开：已收到的部分保留，用量按保守值记账",
    UNKNOWN: "未分类的模型错误：详见脱敏后的原始信息",
}

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password)\b\s*[=:]\s*\S{6,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
)


def sanitize(text: str, *, limit: int = 500) -> str:
    """去掉密钥形态的串并截断：错误文本会进日志、网页和 QQ 消息。"""
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("<已隐藏>", out)
    if len(out) > limit:
        out = out[:limit] + "…"
    return out


@dataclass
class GatewayError(RuntimeError):
    """模型调用失败。`code` 是稳定的分支依据，`message` 已脱敏。"""

    code: str
    message: str
    model: str = ""
    status: int | None = None
    retryable: bool = False
    attempts: int = 1
    # 供应商是否可能已经处理（并计费）了这次请求：超时/连接中断时为 True
    maybe_billed: bool = False

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)

    def __str__(self) -> str:  # pragma: no cover - 直接返回已脱敏文本
        return self.message

    @property
    def hint(self) -> str:
        return _HINTS.get(self.code, _HINTS[UNKNOWN])

    def brief(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "model": self.model,
            "status": self.status,
            "retryable": self.retryable,
            "attempts": self.attempts,
            "maybe_billed": self.maybe_billed,
            "hint": self.hint,
        }


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify(
    exc: BaseException, *, model: str = "", attempts: int = 1
) -> GatewayError:
    """把任意异常翻译成带错误码的 GatewayError（不抛异常，纯函数）。"""
    name = type(exc).__name__
    text = sanitize(str(exc))
    status = _status_of(exc)

    if isinstance(exc, GatewayError):  # 已经是本层错误：保持原样
        return exc

    if name in _NO_RETRY_NAMES:
        code, reason = _NO_RETRY_NAMES[name]
        return GatewayError(
            code=code,
            message=f"{reason}（{name}，HTTP {status or 'n/a'}）：{text}",
            model=model,
            status=status,
            retryable=False,
            attempts=attempts,
        )
    if name in _RETRY_NAMES:
        code, reason = _RETRY_NAMES[name]
        return GatewayError(
            code=code,
            message=f"{reason}（{name}，HTTP {status or 'n/a'}）：{text}",
            model=model,
            status=status,
            retryable=True,
            attempts=attempts,
            maybe_billed=code in {TIMEOUT, CONNECTION, SERVER},
        )

    # 没有类名可用时退到状态码判断（兼容 OpenAI 兼容层的裸 HTTP 异常）
    if status == 401 or status == 403:
        return GatewayError(AUTH, f"认证失败（HTTP {status}）：{text}", model, status, False, attempts)
    if status == 429:
        return GatewayError(RATE_LIMIT, f"被供应商限流（HTTP 429）：{text}", model, status, True, attempts)
    if status == 400 or status == 422:
        return GatewayError(BAD_REQUEST, f"请求被拒绝（HTTP {status}）：{text}", model, status, False, attempts)
    if status is not None and 500 <= status < 600:
        return GatewayError(SERVER, f"供应商 {status}：{text}", model, status, True, attempts, True)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        code = TIMEOUT if isinstance(exc, TimeoutError) else CONNECTION
        return GatewayError(code, f"网络问题（{name}）：{text}", model, status, True, attempts, True)

    return GatewayError(UNKNOWN, f"模型调用失败（{name}）：{text}", model, status, False, attempts)


def cancel_error(model: str = "") -> GatewayError:
    return GatewayError(CANCELLED, "运行已取消：未发起模型请求", model=model, retryable=False)


def budget_error(message: str, *, model: str = "") -> GatewayError:
    return GatewayError(BUDGET, message, model=model, retryable=False)


def unsupported(message: str, *, model: str = "") -> GatewayError:
    return GatewayError(UNSUPPORTED, message, model=model, retryable=False)
