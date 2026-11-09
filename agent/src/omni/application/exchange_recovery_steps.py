"""Reuse release snapshots and fences for explicit quarantine/rescue; never clear a child unknown or call a manager."""

from __future__ import annotations

from typing import cast

from ..domain.recovery_authority import RecoverySpec
from ..domain.service_exchange import ExchangePlan
from ..domain.service_isolation import IsolationPlan
from .ports import ExchangeFilesPort, Record
from .releases import Releases


class ExchangeRecoverySteps:
    def __init__(self, files: ExchangeFilesPort, release: Releases) -> None:
        self.files, self.release = files, release

    def publish(self, plan: IsolationPlan, output: str) -> Record:
        return self.files.publish_isolation(plan, output)

    def isolate(self, plan: ExchangePlan) -> None:
        original = plan["release"]
        self.release.files.installations(original)
        with self.release.files.exclusive(original):
            self.release.files.isolate(original)

    def rescue_plan(self, plan: ExchangePlan, authority: dict, request: Record, output: str) -> Record:
        original = plan["release"]
        for side in ("source", "target"):
            self.release.files.require_fence(original, side)
        prepared = self.release.prepared(self.release.files.history(original))
        with self.release.files.exclusive(original):
            witness = self.release.data.witness(original, "target")
        recovery = cast(
            RecoverySpec,
            {
                "release_sha256": original["sha256"],
                "snapshot": prepared["backup"],
                "manifest_sha256": prepared["backup_manifest_sha256"],
                "isolation": authority,
                "restore_policy": "latest_failed",
                "expected_source_witness": witness,
            },
        )
        return self.release.plan(original["target"], request, output, recovery)
