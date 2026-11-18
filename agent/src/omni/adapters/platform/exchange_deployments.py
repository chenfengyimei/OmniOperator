"""Verify a reviewed source for removal or signed boot draining; never execute or import the source installation."""

from __future__ import annotations

from pathlib import Path

from ...application.ports import Record
from ...domain.contracts import BusinessError
from ...domain.releases import ReleasePlan
from .deployment_files import CurrentDeploymentFiles
from omni.adapters.platform.releases.release_files import ReleaseFiles


class ExchangeSourceDeployment(CurrentDeploymentFiles):
    def __init__(self, plan: ReleasePlan, files: ReleaseFiles) -> None:
        self.plan, self.files = plan, files

    def verify(self, bundle: str | Path) -> Record:
        if str(bundle) != self.plan["source"]["request"]["bundle_dir"]:
            raise BusinessError("service_exchange_source_changed", "Only the reviewed source removal is admitted")
        self.files.installations(self.plan)
        return {
            "valid": True,
            "eligible": True,
            "plan": self.plan["source"],
            "eligibility": "reviewed_source_maintenance",
        }
