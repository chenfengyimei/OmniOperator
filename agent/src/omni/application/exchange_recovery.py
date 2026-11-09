"""Explicit offline exchange quarantine and new-directory rescue; no native changes or unknown-outcome erasure."""

from __future__ import annotations

from typing import cast

from ..domain.contracts import BusinessError
from ..domain.releases import digest
from ..domain.service_exchange import progress, validate_exchange
from ..domain.service_isolation import make_isolation, reference, validate_isolation
from .ports import Clock, ExchangeFilesPort, ExchangeRecoveryPort, Ids, Record


class ExchangeRecovery:
    def __init__(self, files: ExchangeFilesPort, steps: ExchangeRecoveryPort, clock: Clock, ids: Ids) -> None:
        self.files, self.steps, self.clock, self.ids = files, steps, clock, ids

    def plan(self, value: object, reason: str, output: str) -> Record:
        exchange = validate_exchange(value)
        with self.files.exclusive(exchange):
            history = self.files.history(exchange)
            _, _, state = progress(exchange, history)
            if not history or state in {"finished", "abandoned", "isolated", "isolating"}:
                raise BusinessError("service_isolation_not_interrupted", "Isolate only an interrupted exchange")
            plan = make_isolation(exchange, len(history), digest(history), reason, self.ids.new(), self.clock.now())
            return self.steps.publish(plan, output)

    def apply(self, value: object, approval: str) -> Record:
        plan = validate_isolation(value)
        if plan["sha256"] != approval:
            raise BusinessError("service_isolation_not_approved", "Approve the full startup-quarantine fingerprint")
        exchange = plan["exchange"]
        ref = cast(Record, reference(plan))
        with self.files.exclusive(exchange):
            history = self.files.history(exchange)
            _, _, state = progress(exchange, history)
            if state in {"isolating", "isolated"}:
                if history[-1]["proof"] != ref:
                    raise BusinessError("service_isolation_foreign", "Continue the exact recorded isolation")
            elif len(history) != plan["event_count"] or digest(history) != plan["history_sha256"]:
                raise BusinessError("service_isolation_stale", "The exchange progressed; review a new isolation plan")
            self.files.claim(exchange)
            if state not in {"isolating", "isolated"}:
                self.files.append(
                    exchange, {"state": "isolating", "stage": "", "recorded_at": self.clock.now(), "proof": ref}
                )
            with self.files.maintenance(exchange):
                self.steps.isolate(exchange)
            if state != "isolated":
                self.files.append(
                    exchange, {"state": "isolated", "stage": "", "recorded_at": self.clock.now(), "proof": ref}
                )
            return {
                "state": "isolated",
                "isolation_id": plan["id"],
                "sha256": plan["sha256"],
                "ready": False,
                "external_effect": "unknown",
                "original_receipts_retained": True,
                "starts_service": False,
                "stops_service": False,
            }

    def rescue_plan(self, value: object, request: Record, output: str) -> Record:
        exchange = validate_exchange(value)
        with self.files.exclusive(exchange):
            history = self.files.history(exchange)
            _, _, state = progress(exchange, history)
            if state != "isolated":
                raise BusinessError("service_rescue_not_isolated", "Complete the reviewed offline quarantine first")
            ref = cast(dict, history[-1]["proof"])
            authority = {
                "exchange_id": exchange["id"],
                "exchange_sha256": exchange["sha256"],
                "exchange_journal": exchange["journal"],
                "isolation_id": ref["isolation_id"],
                "isolation_sha256": ref["isolation_sha256"],
                "release_id": exchange["release"]["id"],
                "release_sha256": exchange["release"]["sha256"],
                "source_deployment_sha256": exchange["release"]["target"]["sha256"],
            }
            return self.steps.rescue_plan(exchange, authority, request, output)
