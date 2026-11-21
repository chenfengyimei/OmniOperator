"""K01.D3(b): opaque prompt-cache routing key without changing the frozen prompt prefix."""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import cast

from omni.adapters.diagnostics import Redactor
from omni.adapters.models import LiteLLMModel, cache_route, prompt_cache_key
from omni.domain.settings import Settings


def settings(**overrides):
    value = {
        "persona": "persona",
        "workspace": "",
        "daily_budget_micro": 1000,
        "call_reservation_micro": 100,
        "max_steps": 4,
        "disabled_tools": [],
        "model_profile_id": "profile",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "credential_ref": "",
        "context_tokens": 128000,
        "output_tokens": 1024,
        "summary_tokens": 512,
        "margin_tokens": 256,
        "counter_mode": "conservative",
        "counter_ref": "fixture",
        "model_revision_id": "revision",
        "model_permission_epoch": 1,
        "agent_id": "agent-1",
        "session_id": "session-1",
        "content_epoch": 1,
    }
    value.update(overrides)
    return cast(Settings, value)


def test_prompt_cache_key_is_opaque_stable_and_epoch_scoped():
    key = prompt_cache_key(settings())
    assert key is not None and re.fullmatch(r"pc_[0-9a-f]{24}", key)
    assert key == prompt_cache_key(settings())
    assert "agent-1" not in key and "session-1" not in key
    assert key != prompt_cache_key(settings(session_id="session-2"))
    assert key != prompt_cache_key(settings(content_epoch=2))
    expected = "pc_" + hashlib.sha256(b"agent-1|session-1|1").hexdigest()[:24]
    assert key == expected


def test_prompt_cache_key_fails_closed_without_run_scope():
    assert prompt_cache_key(settings(content_epoch=None)) is None
    assert prompt_cache_key(settings(agent_id=None)) is None
    assert prompt_cache_key(settings(session_id=3)) is None


def test_cache_route_provider_matrix():
    assert cache_route(settings(model="x-ai/grok-4", base_url="https://api.x.ai/v1"))
    assert cache_route(settings(model="gpt-4o", base_url="https://api.openai.com/v1"))
    assert cache_route(settings(model="openai/gpt-4o", base_url=""))
    assert not cache_route(settings(model="deepseek-chat", base_url="https://api.deepseek.com"))
    assert not cache_route(settings(model="claude-3-7-sonnet", base_url="https://api.anthropic.com"))
    assert not cache_route(settings(model="gemini-2.5-pro", base_url="https://generativelanguage.googleapis.com"))


def test_prepare_injects_route_key_without_touching_messages(monkeypatch):
    monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
    messages = [{"role": "system", "content": "frozen"}, {"role": "user", "content": "go"}]
    model = LiteLLMModel(None, Redactor())
    kwargs = asyncio.run(model._prepare(messages, (), settings()))
    assert kwargs["messages"] == messages
    assert kwargs["extra_body"] == {"prompt_cache_key": prompt_cache_key(settings())}
    assert kwargs["caching"] is False


def test_prepare_skips_content_address_route(monkeypatch):
    monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
    model = LiteLLMModel(None, Redactor())
    kwargs = asyncio.run(
        model._prepare(
            [{"role": "user", "content": "go"}],
            (),
            settings(model="deepseek-chat", base_url="https://api.deepseek.com"),
        )
    )
    assert "extra_body" not in kwargs


def test_prepare_keeps_opencode_session_header_and_no_route_key(monkeypatch):
    monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
    model = LiteLLMModel(None, Redactor())
    kwargs = asyncio.run(
        model._prepare(
            [{"role": "user", "content": "go"}],
            (),
            settings(model="opencode-go", base_url="https://opencode.ai/zen/go/v1"),
        )
    )
    assert kwargs["extra_headers"]["x-opencode-session"] == "profile"
    assert "extra_body" not in kwargs
