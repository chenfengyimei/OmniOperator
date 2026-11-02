"""校验真实证据与常驻结果；永不执行发布或代替用户批准。"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from .provenance import source_fingerprint
from .services import OMNI_SERVICES
from .soak import read, summarize

REQUIRED_CASES = (
    "northstar_a",
    "northstar_b",
    "northstar_c",
    "northstar_d",
    "northstar_e",
    "desktop_notepad",
    "desktop_second_app",
    "desktop_edge_cases",
    "qq_group_isolation",
    "bilibili_write",
    "embedding_local",
    "embedding_api",
    "backup_restore",
    "fault_restart_cancel",
    "offline_regression",
)


def assess(manifest_path: Path, soak_path: Path, *, expected_source: str | None = None) -> dict:
    source = expected_source or source_fingerprint()
    blockers, accepted = [], []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("scope") != "extended-local-agent"
        or manifest.get("schema") != 1
        or manifest.get("source") != source
    ):
        blockers.append("验收清单范围/版本与当前源码不一致")
    if manifest.get("blocking_issues") != []:
        blockers.append("存在未关闭的阻断项或尚未完成阻断项审查")
    cases = manifest.get("cases", [])
    if not isinstance(cases, list):
        cases = []
    for name in REQUIRED_CASES:
        matches = [row for row in cases if isinstance(row, dict) and row.get("id") == name]
        if len(matches) != 1:
            blockers.append(name + ": 缺失或重复条目")
            continue
        row = matches[0]
        if (
            row.get("status") != "passed"
            or row.get("confirmed_real") is not True
            or not row.get("verified_by")
            or row.get("source") != source
            or not row.get("observed_at")
        ):
            blockers.append(name + ": 待真实验收")
            continue
        try:
            observed = dt.datetime.fromisoformat(row["observed_at"])
            if observed.tzinfo is None or observed > dt.datetime.now(dt.timezone.utc):
                raise ValueError("invalid observation timestamp")
            evidence = manifest_path.parent / row["evidence"]
            if not evidence.resolve().is_relative_to(manifest_path.parent.resolve()) or evidence.is_symlink():
                raise ValueError("evidence outside campaign")
            if not evidence.is_file() or not 0 < evidence.stat().st_size <= 10_000_000:
                raise ValueError("evidence missing/oversized")
            if hashlib.sha256(evidence.read_bytes()).hexdigest() != row.get("sha256"):
                raise ValueError("evidence hash mismatch")
            accepted.append(name)
        except (KeyError, OSError, ValueError, TypeError):
            blockers.append(name + ": 证据路径或校验失败")
    try:
        campaign, samples = read(soak_path)
        soak = summarize(campaign, samples)
        if campaign["source"] != source:
            blockers.append("常驻采样不是当前源码版本")
        if set(campaign["services"]) != set(OMNI_SERVICES):
            blockers.append("常驻验收未覆盖 QQ 和心跳两个必要服务")
        blockers.extend(soak["blockers"])
        if not soak["soak_passed"]:
            blockers.append("72 小时验收未通过")
    except (OSError, ValueError, KeyError, TypeError):
        blockers.append("常驻证据缺失或损坏")
    return {
        "source": source,
        "scope": "extended-local-agent",
        "accepted": accepted,
        "required": list(REQUIRED_CASES),
        "blockers": blockers,
        "ready_for_human_review": not blockers,
        "publishing_authorized": False,
        "note": "真实语义由具名验收者确认；脚本核对证据完整性，不能证明截图/人工陈述的真实性。对外发布仍需用户明确批准。",
    }
