"""Restart checkpoints contain identities and digests of persisted receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .contracts import JSON

MAX_AUTO_RESUMES = 2
MAX_CHECKPOINT_ACTIONS = 80
RECOVERABLE_TOOLS = frozenset({"files_read", "files_list", "files_search", "files_write", "files_patch", "system_info"})


def arguments_digest(arguments: Mapping[str, JSON]) -> str:
    payload = json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
