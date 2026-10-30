"""个人能力：refine；工具仅使用注入依赖，不构造运行时。"""
from __future__ import annotations

from typing import Any
from ..registry import Risk, SessionRef, Tool, ToolRegistry


def register_refine_tool(
    registry: ToolRegistry,
    reviewer: Any,
    session_ref: SessionRef,
) -> None:
    """session_refine（SAFE）：手动触发对**当前会话**的回合后整理。

    与自动 review（每轮结束后由通道提交）共用同一个有界队列、同一套
    消息区间去重与 review 模型账本——这是给模型的一颗"用户说'整理一下
    这轮对话'时可以按下的按钮"（Hermes /refine 的等价物），不新开执行路径。
    刻意不收 session_id 参数：作用域取 SessionRef 的当前会话，模型不能
    指定整理别的会话；群聊作用域下它与个人记忆工具一样被禁用。
    """

    def session_refine(_: dict[str, Any]) -> str:
        sid = session_ref.get()
        if sid is None:
            return "错误：会话上下文缺失（session_refine 只能在对话过程中使用）"
        verdict = reviewer.submit(sid)
        if verdict == "queued":
            return (
                "已提交整理请求：后台将用 review 模型审阅本会话最近的对话，"
                "把值得长期保留的内容提炼为事实记忆/档案/技能。"
                "完成后结果可在工作台「Review」页与 review 日志里查看"
            )
        if verdict == "duplicate":
            return (
                "没有需要整理的新内容（本会话暂无消息，或最近这段已整理过），"
                "无需重复触发"
            )
        if verdict == "full":
            return "整理队列已满，本次跳过；请稍后再试（宁可少整理一轮，不堆积待办）"
        if verdict == "closed":
            return "整理服务正在关闭，无法提交新的整理请求"
        return "提交失败（存储暂不可用），本次未排队"

    registry.register(
        Tool(
            name="session_refine",
            description=(
                "手动触发对当前会话的记忆整理：后台用 review 模型审阅最近对话，"
                "把值得长期记住的提炼成事实记忆、用户档案或技能。"
                "用户说'整理一下这轮对话''把这轮记下来'时使用；"
                "自动整理通常在每轮结束后已运行，多数时候无需手动触发"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            func=session_refine,
            toolset="core",
            risk=Risk.SAFE,
        )
    )
