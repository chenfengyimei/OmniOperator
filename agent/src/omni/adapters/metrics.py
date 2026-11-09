"""Fixed-cardinality in-process measurements; never stores request identities or text."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from ..application.ports import Record
from ..domain.contracts import JSON, Outcome

OPERATIONS = frozenset(
    {
        "request",
        "database_read",
        "database_write",
        "turn",
        "model",
        "summary",
        "tool",
        "delivery",
        "object",
        "maintenance",
        "desktop",
    }
)
OUTCOMES = frozenset(value.value for value in Outcome)
BOUNDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


@dataclass
class _Histogram:
    count: int = 0
    total: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(BOUNDS))


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, str], _Histogram] = {}
        self._dropped = 0

    def observe(self, operation: str, outcome: str, seconds: float) -> bool:
        with self._lock:
            if (
                operation not in OPERATIONS
                or outcome not in OUTCOMES
                or type(seconds) not in (int, float)
                or not math.isfinite(seconds)
                or seconds < 0
            ):
                self._dropped += 1
                return False
            entry = self._values.get((operation, outcome))
            if entry is None:
                entry = self._values[(operation, outcome)] = _Histogram()
            if not math.isfinite(entry.total + seconds):
                self._dropped += 1
                return False
            entry.count += 1
            entry.total += seconds
            for index, bound in enumerate(BOUNDS):
                if seconds <= bound:
                    entry.buckets[index] += 1
            return True

    def snapshot(self) -> Record:
        with self._lock:
            series: list[JSON] = []
            for (operation, outcome), entry in sorted(self._values.items()):
                buckets: list[JSON] = [
                    {"le": bound, "count": count} for bound, count in zip(BOUNDS, entry.buckets, strict=True)
                ]
                buckets.append({"le": "+Inf", "count": entry.count})
                series.append(
                    {
                        "name": "omni_operation_duration_seconds",
                        "type": "histogram",
                        "labels": {"operation": operation, "outcome": outcome},
                        "count": entry.count,
                        "sum": entry.total,
                        "buckets": buckets,
                    }
                )
            return {
                "schema": 1,
                "scope": "this boot",
                "max_series": len(OPERATIONS) * len(OUTCOMES),
                "dropped": self._dropped,
                "series": series,
            }
