"""B站草稿→人工批准→一次 UI 提交→记录回读；浏览器能力通过协议注入。

不导入 browser 域、不调用私有写 API、不处理验证码；unknown 仅人工核查。
"""
from __future__ import annotations

import json
import re
from typing import Protocol
from urllib.parse import urlsplit

from ...config import BilibiliWrite
from ...memory.publications import PublicationError, PublicationStore
from ..result import ToolResult


class FormPort(Protocol):
    def observe(self, kind: str) -> dict: ...
    def submit(self, kind: str, expected: dict, content: str, *, cancel=None, authorized=lambda: True) -> dict: ...


class BudgetPort(Protocol):
    def try_consume(self) -> tuple[bool, str]: ...


def canonical_target(kind: str, url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PublicationError("目标必须是 B站 HTTPS 页面")
    if kind == "comment" and parsed.hostname == "www.bilibili.com":
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
        if match:
            return f"https://www.bilibili.com/video/{match[1]}", match[1]
    if kind == "private_message" and parsed.hostname == "message.bilibili.com":
        match = re.fullmatch(r"/?whisper/mid([1-9][0-9]{0,19})/?", parsed.fragment)
        if match and parsed.path in ("", "/"):
            return f"https://message.bilibili.com/#/whisper/mid{match[1]}", match[1]
    raise PublicationError("目标不是受支持的视频评论/指定用户私信页面")


def preview(row: dict) -> dict:
    return {key: row[key] for key in ("id", "session_id", "kind", "target", "account", "content", "content_hash", "status", "evidence", "error")}


class BilibiliPublisher:
    def __init__(self, config: BilibiliWrite, store: PublicationStore, forms: FormPort, budget: BudgetPort) -> None:
        self.config, self.store, self.forms, self.budget = config, store, forms, budget

    def _check(self, kind: str, content: str) -> str:
        if not self.config.enabled or kind not in self.config.forms:
            raise PublicationError("B站写流程未启用或缺少已核验的页面控件配置")
        content = content.strip()
        if not content or len(content) > self.config.max_content_chars or any(ord(c) < 32 and c not in "\n\t" for c in content):
            raise PublicationError("内容为空、过长或包含控制字符")
        return content

    def prepare(self, *, session_id: int, kind: str, target: str, account: str, content: str) -> dict:
        content = self._check(kind, content)
        target, key = canonical_target(kind, target)
        if not re.fullmatch(r"[1-9][0-9]{0,19}", account):
            raise PublicationError("账号必须为当前登录者的 B站 UID")
        observation = self.forms.observe(kind)
        actual, _ = canonical_target(kind, observation["url"])
        if actual != target or observation["target"] != key or observation["account"] != account:
            raise PublicationError("当前页面账号/目标与预览不符，未创建草稿")
        if observation.get("editor_value", "").strip() not in ("", content):
            raise PublicationError("编辑框已有其它内容，请先由用户处理，未覆盖")
        return preview(self.store.create(session_id=session_id, kind=kind, target=target, account=account,
                                         content=content, observation=observation))

    def submit(self, *, session_id: int, draft_id: str, kind: str, target: str,
               account: str, content: str, expected_hash: str, cancel=None) -> ToolResult:
        claimed = None
        try:
            self._check(kind, content)
            row = self.store.get(draft_id)
            if not row or row["session_id"] != session_id:
                raise PublicationError("草稿不存在或不属于本会话")
            if any(row[key] != value for key, value in {"kind": kind, "target": target, "account": account,
                                                        "content": content, "content_hash": expected_hash}.items()):
                raise PublicationError("审批预览与保存草稿不一致，批准后编辑须重新批准")
            if getattr(cancel, "cancelled", False):
                return ToolResult.cancelled("发布已取消，未执行")
            claimed = self.store.claim(draft_id, session_id=session_id, expected_hash=expected_hash, ttl_s=self.config.draft_ttl_s)
            # 填写与发送分别占一份浏览器预算；扣费失败不触及页面。
            for _ in range(2):
                ok, detail = self.budget.try_consume()
                if not ok:
                    self.store.finish(draft_id, claimed["attempt_token"], "blocked", error=detail)
                    return ToolResult.denied(detail, error_code="write_budget")
            def authorized() -> bool:
                current = self.store.get(draft_id)
                return bool(current and current["status"] == "sending" and current["attempt_token"] == claimed["attempt_token"])
            result = self.forms.submit(kind, json.loads(claimed["observation"]), content, cancel=cancel, authorized=authorized)
            status = result.get("state")
            evidence = result.get("evidence") or {}
            if status == "sent" and (not evidence.get("record_id") or evidence.get("account") != account or evidence.get("content_hash") != expected_hash):
                status = "unknown"
            if status == "sent":
                try:
                    if canonical_target(kind, evidence.get("url", ""))[0] != target:
                        status = "unknown"
                except (PublicationError, ValueError):
                    status = "unknown"
            if status not in {"sent", "blocked", "unknown"}:
                status = "unknown"
            stored = self.store.finish(draft_id, claimed["attempt_token"], status, evidence=json.dumps(evidence, ensure_ascii=False),
                                       error=result.get("error", "") if status != "sent" else "")
            if not stored:
                return ToolResult.unknown("发布账本未确认，禁止自动重发", error_code="publication_ledger_unknown")
            if status == "sent":
                return ToolResult.ok(f"草稿 {draft_id} 已提交，并读到新增记录 {evidence['record_id']}",
                                     side_effect_state="applied", verification=json.dumps(evidence, ensure_ascii=False))
            if status == "blocked":
                return ToolResult.fail(result.get("error", "页面核验失败，未写入"), error_code="publication_blocked")
            return ToolResult.unknown(result.get("error", "发布结果未知，请人工核查；禁止自动重发"), error_code="publication_unknown")
        except PublicationError as exc:
            return ToolResult.fail(str(exc), error_code="publication_rejected")
        except Exception as exc:
            if claimed:
                try:
                    self.store.finish(draft_id, claimed["attempt_token"], "unknown", error=type(exc).__name__)
                except Exception:
                    pass  # 原 sending 同样禁止自动重放。
                return ToolResult.unknown("发布流程异常，结果未知；请人工核查", error_code="publication_exception")
            return ToolResult.fail(f"发布流程未开始：{type(exc).__name__}", error_code="publication_unavailable")
