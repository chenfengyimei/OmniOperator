"""Bounded previews owned by active model calls; durable messages remain the only completed output."""

from __future__ import annotations

MAX_PREVIEW_BYTES = 32768


class ModelOutput:
    def __init__(self):
        self._active: dict[tuple[str, str], dict] = {}

    def begin(self, agent_id: str, run_id: str, call_id: str, authority: dict):
        key = (agent_id, run_id)
        if key in self._active or len(self._active) >= 4:
            raise RuntimeError("Live output requires a unique active run and bounded concurrency")
        self._active[key] = {"call_id": call_id, "revision": 0, "text": "", "truncated": False, "_authority": authority}

    def append(self, agent_id: str, run_id: str, call_id: str, delta: str):
        row = self._active.get((agent_id, run_id))
        if row is None or row["call_id"] != call_id:
            return
        raw = (row["text"] + delta).encode("utf-8")
        row.update(
            text=raw[-MAX_PREVIEW_BYTES:].decode("utf-8", errors="ignore"),
            revision=row["revision"] + 1,
            truncated=row["truncated"] or len(raw) > MAX_PREVIEW_BYTES,
        )

    def get(self, agent_id: str, run_id: str) -> dict | None:
        row = self._active.get((agent_id, run_id))
        return {k: v for k, v in row.items() if k != "_authority"} if row else None

    def authority(self, agent_id: str, run_id: str):
        row = self._active.get((agent_id, run_id))
        return row["_authority"] if row else None

    def clear(self, agent_id: str, run_id: str, call_id: str = ""):
        row = self._active.get((agent_id, run_id))
        if row is not None and (not call_id or row["call_id"] == call_id):
            self._active.pop((agent_id, run_id))
