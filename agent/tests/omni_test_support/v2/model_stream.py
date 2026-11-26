"""Reusable model_stream fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from omni.adapters.model_stream import PreparedStream
from omni.adapters.persistence import schema as s
from omni.application.engine import Capability
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Effect, ModelReply, Outcome, ToolSpec
from omni.domain.model_stream import MAX_TEXT_BYTES, StreamAssembly
from omni_test_support.v2.credentials import Backend
from omni_test_support.v2.execution import wait_state
from omni_test_support.v2.v2_configuration_helpers import model_instance


def use_offline_sdk_tokenizer(monkeypatch):
    import tiktoken
    from litellm import utils
    from litellm.litellm_core_utils import token_counter

    # Provider usage comes from the wire. Keep the SDK's incidental estimates
    # independent of optional encoding files and the machine's tokenizer cache.
    encoding = tiktoken.Encoding(
        name="omni_fixture_bytes",
        pat_str=r"(?s).",
        mergeable_ranks={bytes([value]): value for value in range(256)},
        special_tokens={},
    )
    monkeypatch.setattr(utils, "get_default_encoding", lambda: encoding)
    monkeypatch.setattr(token_counter, "get_default_encoding", lambda: encoding)
    monkeypatch.setattr(token_counter, "get_encoding", lambda _name: encoding)


def chunk(content=None, *, finish=None, calls=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if calls is not None:
        delta["tool_calls"] = calls
    return {"model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def usage():
    return {"model": "fixture", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 3}}


class StreamingModel:
    def __init__(self, *, finish="stop", missing_usage=False):
        self.frames = [chunk("partial中文<unsafe>"), chunk(" complete"), chunk(finish=finish)]
        if not missing_usage:
            self.frames.append(usage())
        self.release, self.entered, self.close_release, self.close_entered = (asyncio.Event() for _ in range(4))
        self.release.set()
        self.close_release.set()
        self.prepared = set()
        self.send_count = self.closed_count = self.nonstream_count = 0
        self.failure = self.close_failure = self.closing = False

    async def complete(self, messages, tools, settings):
        self.nonstream_count += 1
        return ModelReply("summary", cost_micro=0)

    async def prepare_stream(self, messages, tools, settings):
        owner = self

        class Native:
            def __init__(self):
                self.index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index == 1:
                    owner.entered.set()
                    await owner.release.wait()
                    if owner.failure:
                        raise OSError("private-provider-canary")
                if self.index == len(owner.frames):
                    raise StopAsyncIteration
                value = owner.frames[self.index]
                self.index += 1
                return SimpleNamespace(model_dump=lambda: value)

            async def aclose(self):
                owner.close_entered.set()
                await owner.close_release.wait()
                if owner.close_failure:
                    raise OSError("private-close-canary")
                owner.closed_count += 1

        class Wrapper:
            def __init__(self):
                self.completion_stream = Native()

            def __aiter__(self):
                return self

            async def __anext__(self):
                return await self.completion_stream.__anext__()

            async def aclose(self):
                if self.completion_stream is not None:
                    await self.completion_stream.aclose()

        async def send():
            owner.send_count += 1
            return Wrapper()

        result = PreparedStream(send, lambda payload: 5, lambda: self.prepared.discard(result))
        self.prepared.add(result)
        return result

    def health(self):
        return {
            "enabled": True,
            "owned": len(self.prepared),
            "cleanup_pending": sum(p.cleanup_pending for p in self.prepared),
        }

    def stop_accepting(self):
        self.closing = True

    async def close(self, timeout_s=30):
        self.stop_accepting()
        for item in tuple(self.prepared):
            if item.active:
                return False
            try:
                await item.close()
            except BusinessError:
                return False
        return not self.prepared


async def opened(tmp_path, model, capabilities=()):
    instance = await open_instance(tmp_path, model=model, model_streamer=model, capabilities=capabilities)
    agent = (await instance.application.query("agents", ""))[0]["id"]
    await model_instance(instance, agent)
    return instance, agent


__all__ = [
    "ActionResult",
    "Backend",
    "BusinessError",
    "Capability",
    "Effect",
    "MAX_TEXT_BYTES",
    "ModelReply",
    "Outcome",
    "PreparedStream",
    "SimpleNamespace",
    "StreamAssembly",
    "StreamingModel",
    "ToolSpec",
    "asyncio",
    "chunk",
    "json",
    "model_instance",
    "open_instance",
    "opened",
    "pytest",
    "s",
    "select",
    "usage",
    "wait_state",
]
