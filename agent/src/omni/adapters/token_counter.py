"""Versioned UTF-8 token envelope: v1 counts bytes, v2 converts conservatively to tokens."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from ..application.ports import Record
from ..domain.contracts import BusinessError, ToolSpec
from ..domain.images import model_tokens, references
from ..domain.metering import TokenEstimate, serialized_request
from ..domain.settings import Settings

# ASCII text (JSON/code) is ~2.5-4 bytes per token; 3 is the conservative bound.
ASCII_BYTES_PER_TOKEN = 3
# UTF-8 CJK is 3 bytes per character and tokenizers often emit up to two tokens
# per character; two tokens per non-ASCII character stays an upper bound.
NON_ASCII_TOKENS_PER_CHAR = 2
ENVELOPE_TOKENS = 256


class ConservativeTokenCounter:
    def supports(self, mode: str, counter_ref: str, model: str) -> bool:
        return mode == "utf8_upper_bound" and counter_ref in {"utf8-envelope-v1", "utf8-envelope-v2"}

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
        payload = serialized_request(messages, tools)
        if settings["counter_ref"] == "utf8-envelope-v1":
            return TokenEstimate(
                len(payload.encode("utf-8")) + ENVELOPE_TOKENS + sum(costs),
                "upper_bound",
                "utf8-envelope-v1",
            )
        encoded = payload.encode("utf-8")
        ascii_bytes = len(payload.encode("ascii", "ignore"))
        non_ascii_chars = (len(encoded) - ascii_bytes) // 3
        return TokenEstimate(
            -(-ascii_bytes // ASCII_BYTES_PER_TOKEN)
            + NON_ASCII_TOKENS_PER_CHAR * non_ascii_chars
            + ENVELOPE_TOKENS
            + sum(costs),
            "upper_bound",
            "utf8-envelope-v2",
        )
