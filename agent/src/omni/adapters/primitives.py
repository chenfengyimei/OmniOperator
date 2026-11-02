"""Real clocks and full UUID identities, injected by the composition root."""

from __future__ import annotations

import datetime as dt
import time
import uuid


class SystemClock:
    def now(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")

    def monotonic(self) -> float:
        return time.monotonic()


class UUIDs:
    def new(self) -> str:
        return str(uuid.uuid4())
