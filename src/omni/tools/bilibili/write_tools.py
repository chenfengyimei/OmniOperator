"""B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。"""
from __future__ import annotations

import json

from ..registry import Tool, ToolRegistry, Risk, SessionRef
from ..runctx import RunRef
from ..result import ToolResult
from .publishing import BilibiliPublisher, PublicationError, preview


def register_bilibili_write_tools(registry: ToolRegistry, publisher: BilibiliPublisher,
                                  session_ref: SessionRef, run_ref: RunRef) -> None:
    fields = {"kind": {"type": "string", "enum": ["comment", "private_message"]},
              "target": {"type": "string", "description": "完整视频或指定私信对话 URL"},
              "account": {"type": "string", "description": "经页面观察核验的当前登录 B站 UID"},
              "content": {"type": "string", "description": "完整发布内容，必须原样展示供人工确认"}}
    def prepare(args):
        sid = session_ref.get()
        if sid is None:
            return ToolResult.fail("缺少会话上下文")
        try:
            row = publisher.prepare(session_id=sid, **{key: str(args.get(key, "")) for key in fields})
            return ToolResult.ok("草稿已保存，尚未填写或发布：\n" + json.dumps(row, ensure_ascii=False), data=row)
        except Exception as exc:
            return ToolResult.fail(str(exc) if isinstance(exc, PublicationError) else f"页面观察失败：{type(exc).__name__}", error_code="draft_rejected")

    def submit(args):
        sid = session_ref.get()
        if sid is None:
            return ToolResult.fail("缺少会话上下文")
        run = run_ref.get()
        return publisher.submit(session_id=sid, draft_id=str(args.get("draft_id", "")),
                                expected_hash=str(args.get("content_hash", "")),
                                cancel=run.cancel_token if run else None,
                                **{key: str(args.get(key, "")) for key in fields})

    def drafts(args):
        sid = session_ref.get()
        rows = [preview(row) for row in publisher.store.list(100) if row["session_id"] == sid]
        return json.dumps(rows, ensure_ascii=False)

    registry.register(Tool("bilibili_prepare_write", "观察当前 B站表单并保存评论/私信草稿；不填写、不发送。页面控件须已由用户配置。",
                           {"type": "object", "properties": fields, "required": list(fields)}, prepare, toolset="bilibili", risk=Risk.SAFE))
    registry.register(Tool("bilibili_drafts", "查看本会话 B站草稿、内容 hash 和发送状态；unknown 必须由人核查。",
                           {"type": "object", "properties": {}}, drafts, toolset="bilibili", risk=Risk.SAFE))
    submit_fields = {**fields, "draft_id": {"type": "string"}, "content_hash": {"type": "string"}}
    registry.register(Tool("bilibili_submit_draft", "人工批准这一个账号/目标/完整内容后，通过页面仅提交一次草稿并回读新增记录；未知结果不能重试。",
                           {"type": "object", "properties": submit_fields, "required": list(submit_fields)},
                           submit, toolset="bilibili", risk=Risk.CONFIRM, requires_human=True))
