"""Print the differing requirement fields (followups/limits) from both merge sides."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(r"C:\v2-acceptance\integration-cdeg")
REL = "docs/tasks/v2-agent-core-parity.json"


def stage(n: int) -> dict:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f":{n}:{REL}"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout
    return json.loads(out)


ours, theirs = stage(2), stage(3)
o = {r["id"]: r for r in ours["requirements"]}
t = {r["id"]: r for r in theirs["requirements"]}
for rid in ("K13", "K14", "K15", "K16", "K18"):
    print("=" * 20, rid)
    for field in ("followups", "limits"):
        if o[rid].get(field) != t[rid].get(field):
            print(f"-- {field} ours --")
            print(json.dumps(o[rid].get(field), ensure_ascii=False, indent=1)[:1500])
            print(f"-- {field} theirs --")
            print(json.dumps(t[rid].get(field), ensure_ascii=False, indent=1)[:1500])
