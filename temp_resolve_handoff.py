"""Resolve the single HANDOFF merge conflict by keeping both appended sections."""

from __future__ import annotations

from pathlib import Path

path = Path(r"C:\v2-acceptance\integration-cdeg\docs\HANDOFF.md")
lines = path.read_text(encoding="utf-8").splitlines()

start = next(i for i, line in enumerate(lines) if line.startswith("<<<<<<<"))
mid = next(i for i, line in enumerate(lines) if line.startswith("======="))
end = next(i for i, line in enumerate(lines) if line.startswith(">>>>>>>"))

ours = lines[start + 1 : mid]
theirs = lines[mid + 1 : end]
merged = lines[:start] + ours + [""] + theirs + lines[end + 1 :]
text = "\n".join(merged) + "\n"
assert "<<<<<<<" not in text and ">>>>>>>" not in text and "\n=======\n" not in text
path.write_text(text, encoding="utf-8")
print("ours", len(ours), "theirs", len(theirs), "total", len(merged))
