"""Record owner Web observations inside the kernel's existing admission transaction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..domain.contracts import BusinessError
from ..domain.omniweave import OmniWeaveMode
from .ports import Record, TurnAdmissionObserver, UnitOfWork


@dataclass(frozen=True)
class OmniWeaveService:
    mode: OmniWeaveMode

    def web_admission(self, agent_id: str, key: str, owner_token: str) -> TurnAdmissionObserver | None:
        if self.mode == "off":
            return None
        digest = hashlib.sha256(owner_token.encode()).hexdigest()

        def observe(unit: UnitOfWork, receipt: Record) -> None:
            # A token revoked while the request waited for the writer cannot authorize a new Ticket.
            if not unit.token_valid(digest):
                raise BusinessError("unauthenticated", "A valid owner token is required", status=401)
            unit.omniweave_observe(
                agent_id,
                key,
                str(receipt["run_id"]),
                str(receipt["session_id"]),
                duplicate=receipt["duplicate"] is True,
            )

        return observe
