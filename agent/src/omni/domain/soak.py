"""V2 observational soak coverage and owner limits; no sampling, clocks, files or acceptance execution."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import cast

from .contracts import BusinessError

MINIMUM_SECONDS = 72 * 3600
MAX_SAMPLES = 65000
RESOURCES = (
    "main_rss_bytes",
    "descendant_rss_sum_bytes",
    "threads",
    "descriptors",
    "children",
    "cleanup_pending",
)
COUNTERS = ("failed_runs", "unknown_model_calls")
LIMIT_KEYS = {*RESOURCES, *("new_" + value for value in COUNTERS)}


def invalid() -> BusinessError:
    return BusinessError(
        "invalid_v2_soak", "Use complete bounded V2 soak metadata and explicit owner limits", status=422
    )


def number(value: object, *, integer: bool = False) -> int | float:
    if type(value) not in ({int} if integer else {int, float}):
        raise invalid()
    selected = cast(int | float, value)
    if not math.isfinite(selected) or not 0 <= selected <= 2**63 - 1:
        raise invalid()
    return selected


def limits(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(cast(dict[object, object], value)) != LIMIT_KEYS:
        raise invalid()
    return {key: int(number(item, integer=True)) for key, item in cast(dict[str, object], value).items()}


def assess(manifest: Mapping[str, object], rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Require a complete continuous 72h window; failures, gaps and backwards clocks remain blockers on resume."""
    policy = limits(manifest["limits"])
    interval = number(manifest["interval_s"], integer=True)
    if not 10 <= interval <= 3600:
        raise invalid()
    blockers: set[str] = set()
    maxima = dict.fromkeys(RESOURCES, 0)
    baseline: dict[str, int] | None = None
    last_counters: dict[str, int] | None = None
    previous: tuple[float, float] | None = None
    first: tuple[float, float] | None = None
    count = failures = 0
    for row in rows:
        count += 1
        if count > MAX_SAMPLES or row.get("sequence") != count:
            raise invalid()
        elapsed, wall_elapsed = float(number(row["elapsed_s"])), float(number(row["wall_elapsed_s"]))
        if first is None:
            first = elapsed, wall_elapsed
            if elapsed > interval or wall_elapsed > interval:
                blockers.add("initial_sample_gap")
        if previous is not None:
            gap, wall_gap = elapsed - previous[0], wall_elapsed - previous[1]
            if gap <= 0 or wall_gap <= 0:
                blockers.add("clock_not_increasing")
            if max(gap, wall_gap) > interval * 2.5:
                blockers.add("sample_gap")
            if abs(gap - wall_gap) > max(5, interval / 2):
                blockers.add("clock_discontinuity")
        previous = elapsed, wall_elapsed
        if row.get("ok") is not True:
            failures += 1
            blockers.add("sample_failed")
            continue
        resources = cast(Mapping[str, object], row["resources"])
        counters = cast(Mapping[str, object], row["counters"])
        for key in RESOURCES:
            current = int(number(resources[key], integer=True))
            maxima[key] = max(maxima[key], current)
            if current > policy[key]:
                blockers.add("limit_" + key)
        current_counters = {key: int(number(counters[key], integer=True)) for key in COUNTERS}
        if baseline is None:
            baseline = current_counters
        for key in COUNTERS:
            delta = current_counters[key] - baseline[key]
            if delta < 0 or last_counters is not None and current_counters[key] < last_counters[key]:
                blockers.add("counter_regressed_" + key)
            elif delta > policy["new_" + key]:
                blockers.add("limit_new_" + key)
        last_counters = current_counters
    observed = min(previous[0] - first[0], previous[1] - first[1]) if previous is not None and first is not None else 0
    if count < 2 or observed < MINIMUM_SECONDS:
        blockers.add("duration_below_72h")
    if manifest.get("sampler_mode") != "native_v2_hmac_http":
        blockers.add("injected_sampler")
    if manifest.get("clock_mode") != "native_monotonic_wall":
        blockers.add("injected_clock")
    return {
        "format": "omni-v2-soak-report-1",
        "samples": count,
        "failed_samples": failures,
        "observed_seconds": max(0, observed),
        "required_seconds": MINIMUM_SECONDS,
        "resource_maxima": maxima,
        "initial_counters": baseline,
        "blockers": sorted(blockers),
        "observational_soak_passed": not blockers,
        "whole_v2_complete": False,
        "production_release_ready": False,
        "functional_workloads_and_real_accounts_accepted": False,
        "prior_unknown_results_automatically_closed": False,
    }
