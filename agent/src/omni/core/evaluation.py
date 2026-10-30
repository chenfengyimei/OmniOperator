"""固定样本检索评测；不把 mock 模型的排序表现当成真实语义收益。"""
from __future__ import annotations

import statistics
import time


def evaluate_retrieval(search, cases: list[dict], *, repeats: int = 3, k: int = 5) -> dict:
    rows = []
    for iteration in range(repeats):
        for case in cases:
            started = time.perf_counter()
            hits = search(case["query"], limit=k, scope=case["scope"])
            expected = case["expected"]
            rank = hits.index(expected) + 1 if expected in hits else None
            rows.append({"id": case["id"], "repeat": iteration + 1, "rank": rank,
                         "passed": bool(rank) if expected else not hits,
                         "retrieved": hits, "latency_ms": (time.perf_counter() - started) * 1000})
    positive = [r for r in rows if next(c for c in cases if c["id"] == r["id"])["expected"]]
    return {"k": k, "repeats": repeats, "samples": len(cases),
            "recall_at_k": sum(r["rank"] is not None for r in positive) / max(1, len(positive)),
            "mrr": sum(1 / r["rank"] if r["rank"] else 0 for r in positive) / max(1, len(positive)),
            "latency_median_ms": statistics.median(r["latency_ms"] for r in rows),
            "cases": rows}
