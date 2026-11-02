"""回合能力模式路由：先规则、后模型、最后回退。

职责：
- 用确定性规则处理高置信输入（明确的写操作 → agent；明确的只读查询 → retrieval；
  明确的寒暄 → chat），避免为简单输入额外付出一次模型调用；
- 规则无法判定时给出**模型分类契约**（提示词与严格 JSON 解析），
  由 Agent 用统一模型出口发起这一次分类调用；
- 无论规则还是模型，最终都要过同一道"副作用后置守卫"：
  明确的动作意图不得被降级到只读档位。

明确不做：
- 不自己调用模型、不持有 Gateway、不碰预算与取消（那属于 Agent 的模型出口）；
- 不做权限判断：路由只决定"这一回合用哪一档工具"，能不能执行仍由场景作用域、
  风险等级与审批链决定；
- 不解析历史事实、不读档案或常驻记忆——分类输入只有当前文本与少量近期原文。
"""

from __future__ import annotations

import json
import re

from .modes import RouteDecision, TurnMode

# 明确的动作意图：出现这些短语说明用户要的是"做一件事"，不是"问一句"。
# 刻意要求搭配请求标记或命令式短语，减少"保存了吗"这类疑问句被误判。
_REQUEST_MARKER = re.compile(r"(帮我|帮忙|请|麻烦|替我|给我|能不能|可以帮我|需要你|让它|把它|你来|由你)")
_ACTION_PHRASES = re.compile(
    r"(写一下|写个|写下|记一下|记住|改一下|改下|修改|重写|删除|删掉|移除|"
    r"发送|发给我|发到|发布|投稿|评论|私信|上传|下载|安装|卸载|执行|跑一下|"
    r"创建|新建|保存|导出|部署|提交|回滚|启动|停止|重启|启用|停用|排程|"
    r"提醒我|开发|实现|修复|调试|整理成文件|存到|写到|改成|换成|设置成|配置成|"
    r"整理一份|生成一份)"
)

# 明确的只读查询意图。
_READ_PHRASES = re.compile(
    r"(查一下|查查|查下|查询|检索|搜索|搜一下|搜下|找一下|找找|看看|看一下|"
    r"瞧一下|读一下|读读|总结一下|概括一下|列一下|有哪些|在哪|什么内容|"
    r"是什么|天气|股价|汇率|新闻|目录)"
)

# 寒暄与短社交用语：明确属于纯聊天。
_GREETING = re.compile(
    r"^(你好|您好|哈喽|hi|hello|hey|在吗|在不在|早上好|早安|中午好|下午好|"
    r"晚上好|晚安|谢谢|多谢|辛苦了|再见|拜拜|好的|好吧|嗯|嗯嗯|哦|喔|噢|哈|"
    r"嘿嘿|在的|没事|打扰了)[呀啊哦啦呢嘛哟了呗哈]*[!！。~？?、, ]*$"
)

# 模型分类契约：只要这一个 JSON，不要解释、不要 Markdown。
ROUTER_SYSTEM_PROMPT = (
    "你是 OmniOperator 的回合能力判定器。根据用户当前的输入，判断这一回合需要哪一档能力：\n"
    "- chat：纯聊天或闲聊，包括对用户自身情况的一般性提问。不需要查外部资料、不需要动本机。\n"
    "- retrieval：需要查询资料的只读请求，例如查找文件、读网页、看 B 站内容、翻历史对话、"
    "查系统状态、总结已有材料。不产生写操作。\n"
    "- agent：需要真正动手的请求，例如写改文件、发消息、发布、运行命令、操作设备、"
    "创建任务、调试排错、多步骤的活。\n"
    "判定只看用户这一句在要什么，不要推测后续步骤。若同时包含只读查询和实际操作，判 agent。\n"
    "只输出一个 JSON 对象，不要任何其他文字："
    '{"mode": "chat|retrieval|agent", "reason": "不超过二十字的中文理由"}'
)

# 允许模型返回的 JSON 键（多一个键就当无效，避免模型夹带指令）。
_ALLOWED_KEYS = frozenset({"mode", "reason"})
_VALID_MODES = frozenset({TurnMode.CHAT.value, TurnMode.RETRIEVAL.value, TurnMode.AGENT.value})


class ModeRouter:
    """能力档位判定器。构造只吃配置，便于单测直接实例化。"""

    def __init__(self, config) -> None:
        self._config = config

    # ---------------------------------------------------------------- 规则

    def rule_decision(self, text: str, *, requested: str) -> RouteDecision | None:
        """高置信规则命中才返回判定；None 表示"交给模型判定"。"""
        stripped = (text or "").strip()
        if not stripped:
            # 空输入按纯聊天处理：没有可执行的内容，也没有值得检索的目标。
            return RouteDecision(requested=requested, mode=TurnMode.CHAT, source="rule", reason="空输入按聊天处理")
        if self._looks_like_action(stripped):
            return RouteDecision(
                requested=requested,
                mode=TurnMode.AGENT,
                source="rule",
                reason="明确的操作意图",
            )
        if _READ_PHRASES.search(stripped):
            return RouteDecision(
                requested=requested,
                mode=TurnMode.RETRIEVAL,
                source="rule",
                reason="明确的只读查询",
            )
        if _GREETING.match(stripped):
            return RouteDecision(
                requested=requested,
                mode=TurnMode.CHAT,
                source="rule",
                reason="寒暄与短社交用语",
            )
        return None

    @staticmethod
    def _looks_like_action(text: str) -> bool:
        """动作意图判定：命令式短语，或"请/帮我 + 动作动词"。"""
        if not _ACTION_PHRASES.search(text):
            return False
        if _REQUEST_MARKER.search(text):
            return True
        # 没有请求标记时，只认句首的命令式短语（中文动宾结构）。
        head = text[:12]
        return bool(
            re.match(
                r"^(写|改|删|发|发布|上传|下载|安装|执行|创建|新建|保存|导出|部署|提交|回滚|启动|停止|重启|启用|停用|排程|开发|实现|修复|调试|整理|生成|记住)",
                head,
            )
        )

    # ---------------------------------------------------------------- 模型判定

    @staticmethod
    def parse_model_reply(text: str) -> tuple[TurnMode, str] | None:
        """严格解析模型分类结果；任何不合规都返回 None（由调用方回退）。"""
        raw = (text or "").strip()
        if not raw:
            return None
        # 只接受整段就是一个 JSON 对象；带解释文字一律不认。
        if not (raw.startswith("{") and raw.endswith("}")):
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or set(data.keys()) != _ALLOWED_KEYS:
            return None
        mode = data.get("mode")
        reason = data.get("reason")
        if not isinstance(mode, str) or mode not in _VALID_MODES:
            return None
        if not isinstance(reason, str) or len(reason) > 120:
            return None
        return TurnMode(mode), reason.strip()

    # ---------------------------------------------------------------- 收口

    def guard(self, mode: TurnMode, text: str) -> TurnMode:
        """副作用后置守卫：明确的动作意图不得被降级到只读档位。"""
        if mode is not TurnMode.AGENT and self._looks_like_action((text or "").strip()):
            return TurnMode.AGENT
        return mode

    def fallback(self, text: str, *, requested: str) -> RouteDecision:
        """路由失败时的回退。

        回退到**完整能力**而不是聊天：路由没判定出来，说明我们不知道用户要做什么，
        此时宁可多花一批工具规格的 token，也不让用户的事做不成。
        （明确的动作意图仍然只是佐证，回退档位本身一律是 agent。）
        """
        return RouteDecision(
            requested=requested,
            mode=TurnMode.AGENT,
            source="fallback",
            reason="路由未判定，回退到完整模式",
        )
