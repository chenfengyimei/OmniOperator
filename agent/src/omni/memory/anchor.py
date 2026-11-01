"""沉淀锚定（P5-b）：后台写入的记忆必须能指回它依据的原文。

学自 dsh-bili-humanism（`lib/personality.mjs` 的 `PERSONALITY_REFLECTION_REQUIRED`）：
人格札记里的每句话都必须逐字出现在它所引用的信笺原文里，否则拒绝落库；
札记行末还会带上"来源手账：<id> · 修订 N"，让人能回查。
抄的是**机制**（确定性校验 + 可回查指针），不抄形态（我们不建 human-world.md）。

为什么我们这边也需要：
review（回合后整理）与 journal（夜间整理）会把一段对话压缩成一条**每轮都注入
提示词**的常驻/档案/人物条目。这条记忆此后每轮都在影响判断，但此前没有任何依据：
模型编造的成本是零，用户也无从核对"这条是我真说过的话，还是它自己写的"。

抄到什么、抄到什么程度（关键取舍）：
- 参考实现锚的是**agent 自己的人格反思**；我们锚的是**关于用户的沉淀条目**
  —— 语义不同，所以不照搬它的落点，只搬"必须可核对"这件事。
- 参考实现要求"整句逐字"；我们写的是**概括**（原文里根本没有这句话），
  强制整句会误伤正常记忆。所以改成**摘录支撑**：条目可以概括，但必须附一段
  逐字原文，且系统用确定性比对校验得出处（不是让另一个模型去判断"像不像"）。
- 只对**后台写入器**强制（review / journal）。对话中用户亲口说的内容不需要出处，
  逼它交出处只会把正常记忆写入变成负担。
- 证据**不进注入块**：常驻 2200 / 档案 2000 字符是硬上限，注入体积纪律优先；
  出处放在 view / lookup 的核对面与工作台里。

来源材料通过上下文变量绑定（`anchor_scope`）：工具层只在"当前正处于后台整理"
时才要求摘录，不必知道调用方到底是 review 还是 journal。
"""
from __future__ import annotations

import contextlib
import contextvars
import re
from dataclasses import dataclass
from typing import Iterator

# 只有后台写入器需要出处：对话路径不强制（用户亲口说的内容本身就是出处）。
ANCHORED_SOURCES = ("review", "journal")

# 摘录长度上限：不让模型把整段材料塞进"摘录"里充当证据（参考实现的句子上限 240）。
MAX_EVIDENCE_CHARS = 400

# 摘录是"逐字照抄"，但模型常把换行/缩进改写；比对前把空白折叠成单空格。
_WHITESPACE = re.compile(r"\s+")

_current: contextvars.ContextVar["AnchorContext | None"] = contextvars.ContextVar(
    "omni_anchor_context", default=None
)


class AnchorError(ValueError):
    """锚定失败：条目缺少出处，或出处不在来源材料里。

    消息要**可操作**：告诉调用方"抄一段原文"，而不是只说"校验失败"。
    """


@dataclass(frozen=True)
class AnchorContext:
    """一次后台整理的来源上下文。

    - `material`：模型这次看到的原文（对话片段 / 笔记素材）。
      校验只认它——模型能抄的只有它看见的东西。
    - `source`：来源子系统（review / journal）。
    - `source_ref`：来源指针（`session:42` / `journal:daily:2026-11-01`），可回查。
    """

    material: str
    source: str
    source_ref: str = ""


@contextlib.contextmanager
def anchor_scope(material: str, *, source: str, source_ref: str = "") -> Iterator[AnchorContext]:
    """绑定来源材料：在这个上下文里写入的沉淀必须带逐字摘录。

    用上下文变量而不是逐层传参：写入工具分布在 tools/personal 下四个模块，
    逐层传参会把"当前是不是后台整理"这件事扩散到每个调用点。
    退出时一定还原（异常路径也还原），不把上下文漏给下一个任务。
    """
    ctx = AnchorContext(material=str(material or ""), source=str(source or ""), source_ref=str(source_ref or ""))
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def current_anchor() -> AnchorContext | None:
    """当前是否处于锚定上下文（不在则返回 None）。"""
    return _current.get()


def normalize_evidence(text: str) -> str:
    """折叠空白：换行、缩进、全角空格统一成单空格（展示与长度计量用）。"""
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def _squash(text: str) -> str:
    """比对口径：去掉**全部**空白。

    为什么不是"折叠成单空格"：来源材料里一句话中间可能带换行（多行消息、
    粘贴的代码块），折叠成单空格会在中间塞进一个空格，模型逐字抄的那句
    就对不上了——**假阴性比略松的比对更糟**（合法的长期记忆写不进去）。

    跨段拼接也不用担心：来源材料每条消息都带角色/时间前缀
    （`[user] `、`[10:23] user: `），拼接处的前缀挡在那里，拼不起来。
    """
    return _WHITESPACE.sub("", str(text or ""))


def verify_evidence(evidence: str, material: str, *, label: str = "这条记忆") -> str:
    """校验摘录确实出自材料；不通过抛 AnchorError。

    比对口径：忽略空白后做子串包含。**不做**语义相似度判断——
    那是"让模型判断模型"，不可靠也不便宜；确定性比对才是硬约束。
    """
    if not normalize_evidence(evidence):
        raise AnchorError(
            f"{label}缺少原文摘录：请从来源材料里逐字照抄一段（不要改写、不要拼接、不要概括）"
        )
    if len(normalize_evidence(evidence)) > MAX_EVIDENCE_CHARS:
        raise AnchorError(
            f"{label}的原文摘录超长（{len(normalize_evidence(evidence))} 字，"
            f"上限 {MAX_EVIDENCE_CHARS}）：请只截取真正支撑这句话的一小段"
        )
    if _squash(evidence) not in _squash(material):
        raise AnchorError(
            f"{label}的原文摘录在来源材料里找不到（忽略空白后仍对不上）："
            "请照着材料原文重新抄一段，可截短，但一个字都不要改"
        )
    return normalize_evidence(evidence)


def require_evidence(raw: str, *, label: str = "这条记忆") -> tuple[str, str]:
    """写入前的统一入口：返回 (落库的摘录, 来源指针)。

    - 不在锚定上下文（对话路径）：出处无从核对，一律返回空——存下没核对过的
      "出处"比不存更糟（看着有依据，其实没核对过）。这条不变式是：
      **落库的摘录一定经过逐字校验，且带得上可回查的来源指针。**
    - 在锚定上下文（后台整理）：摘录必填且必须逐字命中材料，否则抛 AnchorError。
    """
    ctx = current_anchor()
    if ctx is None:
        return "", ""
    text = str(raw or "").strip()
    if not text:
        raise AnchorError(
            f"{label}必须带原文摘录（evidence）：从你正在整理的原文里逐字照抄一段，"
            "不要改写、不要拼接、不要概括——没有出处的内容不写入长期记忆"
        )
    verify_evidence(text, ctx.material, label=label)
    return text, ctx.source_ref


def describe_source(source: str, source_ref: str) -> str:
    """把来源指针翻成人能读的标注（核对面用）。"""
    ref = str(source_ref or "").strip()
    src = str(source or "").strip()
    if ref.startswith("session:"):
        return f"对话 #{ref.split(':', 1)[1]}"
    if ref.startswith("journal:"):
        parts = ref.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        period = parts[2] if len(parts) > 2 else ""
        label = {"daily": "日记", "weekly": "周记", "monthly": "月记"}.get(kind, kind or "笔记")
        return f"{label} {period}".strip()
    return ref or src or "未知来源"
