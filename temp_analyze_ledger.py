"""Compare ours/theirs versions of the parity ledger to plan a semantic merge."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(r"C:\v2-acceptance\integration-cdeg")
REL = "docs/tasks/v2-agent-core-parity.json"


def stage(n: int) -> dict:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "show", f":{n}:{REL}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return json.loads(out)


ours, theirs = stage(2), stage(3)
print("top keys ours", sorted(ours))
print("top keys theirs", sorted(theirs))
print("same top keys", set(ours) == set(theirs))

for key in sorted(set(ours) | set(theirs)):
    if key == "batches":
        o = [b["id"] for b in ours.get("batches", [])]
        t = [b["id"] for b in theirs.get("batches", [])]
        print("batches ours", len(o), "theirs", len(t), "shared", len(set(o) & set(t)))
        print("  only-ours", sorted(set(o) - set(t)))
        print("  only-theirs", sorted(set(t) - set(o)))
    elif key == "requirements":
        o = {r["id"]: r for r in ours.get("requirements", [])}
        t = {r["id"]: r for r in theirs.get("requirements", [])}
        changed = [rid for rid in set(o) & set(t) if o[rid] != t[rid]]
        print("requirements ours", len(o), "theirs", len(t), "changed-in-both", changed)
        for rid in changed:
            o_keys = {k: o[rid][k] for k in o[rid] if o[rid][k] != t[rid].get(k)}
            print("   ", rid, "diff keys:", sorted(o_keys))
    else:
        if ours.get(key) != theirs.get(key):
            print("key differs:", key)
            print("  ours:", json.dumps(ours.get(key), ensure_ascii=False)[:300])
            print("  theirs:", json.dumps(theirs.get(key), ensure_ascii=False)[:300])
