"""Deterministic summary for the OmniOperator extension host."""

import json


def invoke(name, arguments, context):
    text = arguments["text"]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary = {
        "headline": lines[0][:120] if lines else "",
        "nonempty_lines": len(lines),
        "characters": len(text),
    }
    return {
        "status": "ok",
        "text": json.dumps(summary, ensure_ascii=False),
        "data": summary,
        "effect": "none",
        "error_code": "",
        "retryable": False,
        "verification": "Counted the supplied text locally",
        "artifacts": [],
    }
