"""B站草稿工具及逐次人工发布审批；所有浏览器依赖由组装根注入。"""

from __future__ import annotations

import json

from ..registry import Risk, SessionRef, Tool, ToolRegistry
from ..result import ToolResult
from ..runctx import RunRef
from .publishing import BilibiliPublisher, PublicationError, preview


def register_bilibili_write_tools(
    registry: ToolRegistry, publisher: BilibiliPublisher, session_ref: SessionRef, run_ref: RunRef
) -> None:
    fields = {
        "kind": {"type": "string", "enum": ["comment", "private_message"]},
        "target": {"type": "string", "description": "完整视频或指定私信对话 URL"},
        "account": {"type": "string", "description": "经页面观察核验的当前登录 B站 UID"},
        "content": {"type": "string", "description": "完整发布内容，必须原样展示供人工确认"},
    }
    mention_field = {
        "mentions": {
            "type": "array",
            "description": (
                "要艾特的用户：每项含 uid 与 name。正文里用 @{uid} 占位，"
                "保存草稿时会替换成 @昵称，并在人工确认时逐条列出"
            ),
            "items": {
                "type": "object",
                "properties": {"uid": {"type": "string"}, "name": {"type": "string"}},
                "required": ["uid", "name"],
            },
        }
    }

    def _mentions(args) -> list[dict]:
        raw = args.get("mentions") or []
        if not isinstance(raw, list):
            raise PublicationError("mentions 必须是数组")
        rows = []
        for item in raw:
            if not isinstance(item, dict):
                raise PublicationError("mentions 每项必须是对象")
            rows.append({"uid": str(item.get("uid", "")), "name": str(item.get("name", ""))})
        return rows

    def prepare(args):
        sid = session_ref.get()
        if sid is None:
            return ToolResult.fail("缺少会话上下文")
        try:
            payload = {key: str(args.get(key, "")) for key in fields}
            row = publisher.prepare(session_id=sid, mentions=_mentions(args), **payload)
        except PublicationError as exc:
            return ToolResult.fail(str(exc), error_code="draft_rejected")
        except Exception as exc:  # noqa: BLE001, S110  （有意容错：边界转换/生命周期清理不阻断）
            return ToolResult.fail(f"页面观察失败：{type(exc).__name__}", error_code="draft_rejected")
        mentions = row.get("mentions") or []
        extra = "\n将艾特：" + "、".join(f"{item['name']}({item['uid']})" for item in mentions) if mentions else ""
        return ToolResult.ok("草稿已保存，尚未填写或发布：\n" + json.dumps(row, ensure_ascii=False) + extra, data=row)

    def submit(args):
        sid = session_ref.get()
        if sid is None:
            return ToolResult.fail("缺少会话上下文")
        run = run_ref.get()
        return publisher.submit(
            session_id=sid,
            draft_id=str(args.get("draft_id", "")),
            expected_hash=str(args.get("content_hash", "")),
            cancel=run.cancel_token if run else None,
            **{key: str(args.get(key, "")) for key in fields},
        )

    def drafts(args):
        sid = session_ref.get()
        rows = [preview(row) for row in publisher.store.list(100) if row["session_id"] == sid]
        return json.dumps(rows, ensure_ascii=False)

    registry.register(
        Tool(
            "bilibili_prepare_write",
            "观察当前 B站表单并保存评论/私信草稿；不填写、不发送。页面控件须已由用户配置。"
            "评论目标可带 ?reply_id=<评论ID> 表示回复某条评论；艾特用 @{uid} 占位 + mentions 名单。",
            {"type": "object", "properties": {**fields, **mention_field}, "required": list(fields)},
            prepare,
            toolset="bilibili",
            risk=Risk.SAFE,
        )
    )
    registry.register(
        Tool(
            "bilibili_drafts",
            "查看本会话 B站草稿、内容 hash 和发送状态；unknown 必须由人核查。",
            {"type": "object", "properties": {}},
            drafts,
            toolset="bilibili",
            risk=Risk.SAFE,
            read_only=True,
        )
    )
    submit_fields = {**fields, "draft_id": {"type": "string"}, "content_hash": {"type": "string"}}
    registry.register(
        Tool(
            "bilibili_submit_draft",
            "人工批准这一个账号/目标/完整内容后，通过页面仅提交一次草稿并回读新增记录；未知结果不能重试。",
            {"type": "object", "properties": submit_fields, "required": list(submit_fields)},
            submit,
            toolset="bilibili",
            risk=Risk.CONFIRM,
            requires_human=True,
        )
    )
