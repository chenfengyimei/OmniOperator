"""Prepare reviewable service deployments and combine independent proofs; never installs or starts services."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ..domain.service_control import running_postcondition
from ..domain.service_deployment import deployment_make, deployment_plan
from .ports import Clock, DeploymentFilesPort, Ids, Record, ServiceQueryPort, ServiceReadinessPort


class ServiceDeployment:
    def __init__(self, files: DeploymentFilesPort, clock: Clock, ids: Ids) -> None:
        self.files, self.clock, self.ids = files, clock, ids

    def capture(self, output: str) -> Record:
        return self.files.write_capture(self.files.capture(), output)

    def plan(self, request: str, runtime: str, output: str) -> Record:
        plan = deployment_make(self.files.read(request), self.files.read(runtime), self.ids.new(), self.clock.now())
        return self.files.write_plan(cast(Record, plan), output)

    def materialize(self, plan: str, destination: str) -> Record:
        return self.files.materialize(self.files.read(plan), destination)

    def verify(self, bundle: str) -> Record:
        return self.files.verify(bundle)

    async def status(self, verification: Record, manager: ServiceQueryPort, readiness: ServiceReadinessPort) -> Record:
        result: dict[str, object] = {
            "verification": {key: value for key, value in verification.items() if key != "plan"},
            "manager": {"available": False, "state": "unknown", "error_code": "deployment_not_eligible"},
            "runtime": {"ready": False, "error_code": "deployment_not_eligible"},
            "ready": False,
        }
        if verification.get("valid") is not True or verification.get("eligible") is not True:
            return cast(Record, result)
        plan = cast(Record, deployment_plan(verification["plan"]))
        result["manager"] = await manager.query(plan)
        result["runtime"] = await readiness.probe(plan)
        native, running = result["manager"], result["runtime"]
        # Installed, live and definition identity are separate facts. Unknown is never success.
        result["ready"] = running_postcondition(
            plan, cast(Mapping[str, object], native), cast(Mapping[str, object], running)
        )
        return cast(Record, result)
