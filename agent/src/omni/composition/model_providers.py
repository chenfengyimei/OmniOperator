"""Bind model transport ports and their instance lifetime."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ..adapters.diagnostics import Redactor
from ..adapters.models import LiteLLMModel
from ..application.ports import AsyncSecretPort, ModelPort, ModelPreparationPort, ModelStreamingPort
from .lifecycle import Lifetime


@dataclass(frozen=True)
class ModelTransport:
    model: ModelPort
    preparer: ModelPreparationPort | None = None
    streamer: ModelStreamingPort | None = None


def register_model_transport(
    lifetime: Lifetime,
    secrets: AsyncSecretPort,
    redactor: Redactor,
    *,
    model: ModelPort | None = None,
    streamer: ModelStreamingPort | None = None,
    configure_logging: Callable[[tuple[logging.Logger, ...]], None] | None = None,
) -> ModelTransport:
    if model is None:
        provider = LiteLLMModel(secrets, redactor, configure_logging)
        lifetime.own("model_streams", 1, provider.close, provider.stop_accepting)
        return ModelTransport(provider, provider, provider)
    if streamer is not None:
        lifetime.own("model_streams", 1, streamer.close, streamer.stop_accepting)
    return ModelTransport(model, streamer=streamer)
