"""Local conservative token estimate; it never fetches model metadata or credentials."""

from __future__ import annotations

from ..domain.contracts import BusinessError
from ..domain.images import model_tokens, references
from ..domain.metering import TokenEstimate, serialized_request


class ConservativeTokenCounter:
    def supports(self, mode: str, counter_ref: str, model: str) -> bool:
        return mode == "utf8_upper_bound" and counter_ref == "utf8-envelope-v1"

    async def count(self, messages, tools, settings) -> TokenEstimate:
        if not self.supports(settings["counter_mode"], settings["counter_ref"], settings["model"]):
            raise BusinessError(
                "token_counter_unavailable", "The required model tokenizer is not installed", status=503
            )
        # Bytes bound visible byte-tokenizable content, plus an explicit envelope
        # allowance. Hidden provider prompts are not covered by this estimate.
        images = references(messages)
        if any(r["token_cost"] != model_tokens(settings["model"]) for r in images):
            raise BusinessError("image_metering_invalid", "Image token policy does not match the model")
        return TokenEstimate(
            len(serialized_request(messages, tools).encode("utf-8")) + 256 + sum(r["token_cost"] for r in images),
            "upper_bound",
            "utf8-envelope-v1",
        )
