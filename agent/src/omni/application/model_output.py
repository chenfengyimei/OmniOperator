"""Bounded previews owned by active model calls; durable messages remain the only completed output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

MAX_PREVIEW_BYTES = 32768


class _Row(TypedDict):
    call_id: str
    revision: int
    text: str
    truncated: bool
    _authority: Mapping[str, object]


class ModelOutput:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], _Row] = {}

    def begin(self, agent_id: str, run_id: str, call_id: str, authority: Mapping[str, object]) -> None:
        key = (agent_id, run_id)
        if key in self._active or len(self._active) >= 4:
            raise RuntimeError("Live output requires a unique active run and bounded concurrency")
        self._active[key] = {
            "call_id": call_id,
            "revision": 0,
            "text": "",
            "truncated": False,
            "_authority": authority,
        }

    def append(self, agent_id: str, run_id: str, call_id: str, delta: str) -> None:
        row = self._active.get((agent_id, run_id))
        if row is None or row["call_id"] != call_id:
            return
        raw = (row["text"] + delta).encode("utf-8")
        row.update(
            text=raw[-MAX_PREVIEW_BYTES:].decode("utf-8", errors="ignore"),
            revision=row["revision"] + 1,
            truncated=row["truncated"] or len(raw) > MAX_PREVIEW_BYTES,
        )

    def get(self, agent_id: str, run_id: str) -> dict[str, object] | None:
        row = self._active.get((agent_id, run_id))
        return {key: value for key, value in row.items() if key != "_authority"} if row else None

    def authority(self, agent_id: str, run_id: str) -> Mapping[str, object] | None:
        row = self._active.get((agent_id, run_id))
        return row["_authority"] if row else None

    def clear(self, agent_id: str, run_id: str, call_id: str = "") -> None:
        row = self._active.get((agent_id, run_id))
        if row is not None and (not call_id or row["call_id"] == call_id):
            self._active.pop((agent_id, run_id))
