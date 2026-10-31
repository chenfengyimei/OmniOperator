"""按回合能力模式（chat / retrieval / agent）的声明与纯函数。

职责：
- 声明三个模式各自**允许**的工具白名单（模式只缩小能力，绝不放大）；
- 声明模式之间的能力等级（chat < retrieval < agent），供本回合一次升档判定；
- 把"场景许可集合"（所有者 / 非所有者私聊 / 群聊）与"模式白名单"求交，
  得到本回合真正可用的工具集合；
- 声明内核专用升档工具 `request_mode` 的参数契约。

明确不做：
- 不做路由决策（归 core/router.py）、不调用模型、不碰 Registry 与审批链；
- 不在这里判断任何权限：写操作的风险等级与人工确认仍由 tools/registry.py
  与 safety/ 决定，模式只决定"本回合把哪些工具规格发给模型"；
- 不含任何状态：模式是纯数据，跨回合的持久化不是这一层的职责。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum


class TurnMode(str, Enum):
    """本回合的能力档位。"""

    CHAT = "chat"
    RETRIEVAL = "retrieval"
    AGENT = "agent"


# 能力等级：只能向上升档，不能降档。
MODE_RANK: dict[TurnMode, int] = {
    TurnMode.CHAT: 0,
    TurnMode.RETRIEVAL: 1,
    TurnMode.AGENT: 2,
}

# 档位的中文名：升档结果、拒绝提示等面向模型与用户的文案统一从这里取，
# 避免同一档位在不同提示里被叫成不同的东西。
MODE_LABELS: dict[TurnMode, str] = {
    TurnMode.CHAT: "chat（纯聊天与只读记忆）",
    TurnMode.RETRIEVAL: "retrieval（只读检索）",
    TurnMode.AGENT: "agent（完整工具与人工审批）",
}

# 请求值里的 "auto" 表示"按输入自动判定"，不是一种能力档位。
AUTO_MODE = "auto"
REQUEST_MODES: tuple[str, ...] = (AUTO_MODE, TurnMode.CHAT.value, TurnMode.RETRIEVAL.value, TurnMode.AGENT.value)

# 场景许可集合：群聊只允许公开读取与会话内待办，不碰所有者资料。
GROUP_ALLOWED_TOOLS = frozenset({"get_time", "web_read", "todo"})
# 非所有者的私聊：基础工具 + 人物记忆；所有者私人记忆（事实/档案/常驻/历史/技能）不可用。
DM_ALLOWED_TOOLS = GROUP_ALLOWED_TOOLS | frozenset(
    {"person_lookup", "person_remember", "person_event"}
)

# 模式白名单：显式列举，新增工具默认只进 full agent（不会悄悄出现在轻量模式）。
CHAT_TOOLS = frozenset(
    {"get_time", "recall", "search_history", "journal_view", "person_lookup"}
)
RETRIEVAL_TOOLS = CHAT_TOOLS | frozenset(
    {
        # 全量记忆检索（按索引分页，绝不整库进上下文）
        "daily_digest",
        "dialogue_search",
        "dialogue_read",
        "session_log_search",
        "session_log_read",
        # 工作区文件（根与敏感项由 tools/files/paths.py 决定，模型无法扩权）
        "files_list",
        "files_search",
        "files_read",
        "files_diff",
        "files_history",
        # 只读命令观察与公开网页
        "peek_command",
        "web_read",
        # 系统只读状态与 B 站读四件套
        "system_status",
        "bilibili_video_info",
        "bilibili_video_comments",
        "bilibili_video_subtitle",
        "bilibili_video_transcript",
    }
)

MODE_TOOLS: dict[TurnMode, frozenset[str] | None] = {
    # None = 不额外限制（场景许可集合即上限）
    TurnMode.AGENT: None,
    TurnMode.CHAT: CHAT_TOOLS,
    TurnMode.RETRIEVAL: RETRIEVAL_TOOLS,
}

# 内核专用升档工具：只由 Agent 拦截，**不注册进 ToolRegistry**，
# 因此无法被派发、无法绕过审批链、也不会被插件或 MCP 顶名覆盖。
REQUEST_MODE_TOOL = "request_mode"


def request_mode_spec() -> dict:
    """升档工具的 OpenAI 工具规格（每次调用返回独立副本，避免被就地改写）。"""
    return copy.deepcopy(
        {
            "type": "function",
            "function": {
                "name": REQUEST_MODE_TOOL,
                "description": (
                    "当前档位缺少完成用户请求所需的工具时，先用本工具请求升档，"
                    "不要直接回答做不到。"
                    "目标只能是 retrieval（更强的只读检索）或 agent（完整工具与人工审批）；"
                    "本回合最多升档一次，不能降档，拿不准目标就选 agent。"
                    "用现有工具就能完成的请求不要升档，也不要请求与用户诉求无关的能力。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": [TurnMode.RETRIEVAL.value, TurnMode.AGENT.value],
                            "description": "要切换到的能力档位。",
                        },
                        "reason": {
                            "type": "string",
                            "description": "为什么当前档位无法完成（一句话，便于用户理解）。",
                        },
                    },
                    "required": ["mode", "reason"],
                },
            },
        }
    )


@dataclass(frozen=True)
class RouteDecision:
    """一次路由的结果：请求的模式、最终档位、判定来源与理由。"""

    requested: str
    mode: TurnMode
    source: str  # manual / config / rule / router / fallback / legacy
    reason: str = ""
    model: str = ""

    def brief(self) -> dict:
        return {
            "requested": self.requested,
            "mode": self.mode.value,
            "source": self.source,
            "reason": self.reason,
            "model": self.model,
        }


@dataclass
class TurnModeState:
    """本回合的模式状态：当前档位、场景许可集合与可派发/被拒的工具名。"""

    mode: TurnMode = TurnMode.AGENT
    scope_allowed: frozenset[str] = frozenset()
    all_names: frozenset[str] = frozenset()
    denied: frozenset[str] = frozenset()
    escalated: bool = False
    initial_mode: TurnMode = TurnMode.AGENT
    extra: dict = field(default_factory=dict)


def scope_allowed_names(
    all_names: frozenset[str],
    *,
    personal_context: bool,
    scope: str,
) -> frozenset[str]:
    """场景许可集合：所有者全量；非所有者私聊用 DM 白名单；群聊用 GROUP 白名单。"""
    if personal_context:
        return all_names
    allowed = DM_ALLOWED_TOOLS if scope == "private" else GROUP_ALLOWED_TOOLS
    return frozenset(all_names) & allowed


def mode_allowed_names(mode: TurnMode, scope_allowed: frozenset[str]) -> frozenset[str]:
    """模式白名单与场景许可集合求交——模式永远只能缩小能力。"""
    allowed = MODE_TOOLS.get(mode)
    if allowed is None:
        return frozenset(scope_allowed)
    return frozenset(scope_allowed) & allowed


def denied_names(
    mode: TurnMode, scope_allowed: frozenset[str], all_names: frozenset[str]
) -> frozenset[str]:
    """本回合不可派发的工具名（既不出现规格，也在派发处再拦一次）。"""
    return frozenset(all_names) - mode_allowed_names(mode, scope_allowed)


def filter_specs(specs: list[dict], allowed: frozenset[str]) -> list[dict]:
    """从冻结的完整规格里筛出本回合允许发送的部分（保持原顺序）。"""
    return [spec for spec in specs if spec.get("function", {}).get("name") in allowed]


def parse_mode(value: str | None) -> TurnMode | None:
    """把请求里的模式字符串解析成档位；auto/未知返回 None。"""
    if not value:
        return None
    try:
        return TurnMode(str(value).strip().lower())
    except ValueError:
        return None


def can_escalate(current: TurnMode, target: TurnMode) -> bool:
    """只允许向上升档（chat → retrieval → agent）。"""
    return MODE_RANK[target] > MODE_RANK[current]
