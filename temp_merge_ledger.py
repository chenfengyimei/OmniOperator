"""Merge the parity ledger from both merge sides by task and batch id."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(r"C:\v2-acceptance\integration-cdeg")
REL = "docs/tasks/v2-agent-core-parity.json"
TARGET = ROOT / REL


def stage(n: int) -> dict:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f":{n}:{REL}"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return json.loads(out)


ours, theirs = stage(2), stage(3)

# batches: ours order, then theirs-only entries in their order
merged_batches = list(ours["batches"])
seen = {b["id"] for b in merged_batches}
for batch in theirs["batches"]:
    if batch["id"] not in seen:
        merged_batches.append(batch)
        seen.add(batch["id"])

# requirements: union followups by batch id; limits from the branch that owns the requirement
ours_req = {r["id"]: r for r in ours["requirements"]}
theirs_req = {r["id"]: r for r in theirs["requirements"]}
merged_reqs = []
for base in ours["requirements"]:
    rid = base["id"]
    other = theirs_req[rid]
    merged = dict(base)
    followups = list(base.get("followups", []))
    seen_batches = {f.get("batch") for f in followups}
    for item in other.get("followups", []):
        if item.get("batch") not in seen_batches:
            followups.append(item)
            seen_batches.add(item.get("batch"))
    merged["followups"] = followups
    if rid in {"K13", "K18"} and other.get("limits") != base.get("limits"):
        merged["limits"] = other.get("limits")
    merged_reqs.append(merged)

result = dict(ours)
result["batches"] = merged_batches
result["requirements"] = merged_reqs
TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("batches", len(ours["batches"]), "+", len(theirs["batches"]), "->", len(merged_batches))
print("requirements", len(merged_reqs), "followups", sum(len(r.get("followups", [])) for r in merged_reqs))
