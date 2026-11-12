"""Local conservative token estimate; it never fetches model metadata or credentials."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from ..application.ports import Record
from ..domain.contracts import BusinessError, ToolSpec
from ..domain.images import model_tokens, references
from ..domain.metering import TokenEstimate, serialized_request
from ..domain.settings import Settings


class ConservativeTokenCounter:
    def supports(self, mode: str, counter_ref: str, model: str) -> bool:
        return mode == "utf8_upper_bound" and counter_ref == "utf8-envelope-v1"

    async def count(self, messages: Sequence[Record], tools: Sequence[ToolSpec], settings: Settings) -> TokenEstimate:
        if not self.supports(settings["counter_mode"], settings["counter_ref"], settings["model"]):
            raise BusinessError(
                "token_counter_unavailable", "The required model tokenizer is not installed", status=503
            )
        # Bytes bound visible byte-tokenizable content, plus an explicit envelope
        # allowance. Hidden provider prompts are not covered by this estimate.
        images = references(cast(Sequence[dict[str, object]], messages))
        costs = [int(cast(int, r["token_cost"])) for r in images]
        if any(cost != model_tokens(settings["model"]) for cost in costs):
            raise BusinessError("image_metering_invalid", "Image token policy does not match the model")
        return TokenEstimate(
            len(serialized_request(messages, tools).encode("utf-8")) + 256 + sum(costs),
            "upper_bound",
            "utf8-envelope-v1",
        )
