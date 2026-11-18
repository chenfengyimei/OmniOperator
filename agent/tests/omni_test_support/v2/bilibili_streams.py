"""Reusable bilibili_streams fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import copy

import httpx
import pytest

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets
from omni_test_support.v2.bilibili_response_bounds import BV, NAV, SOURCE

HOST = "https://cdn.bilivideo.com/"

AUDIO = {"baseUrl": HOST + "complete-audio.m4s", "bandwidth": 64, "size": 16}

VIDEO = {"baseUrl": HOST + "complete-video.m4s", "bandwidth": 128, "size": 16}

PARTS = [{"url": HOST + f"fragment-{index}.flv", "size": 4, "order": index} for index in (1, 2)]

MERGED = {"url": HOST + "complete-merged.mp4", "size": 16}

FULL = b"complete fixture"


def fixture(play, merged_play):
    state, calls = {"play": copy.deepcopy(play), "merged": copy.deepcopy(merged_play)}, []

    def handle(request):
        calls.append(request)
        if request.url.host == "cdn.bilivideo.com":
            return httpx.Response(200, content=FULL if request.url.path.startswith("/complete-") else b"part")
        if request.url.path.endswith("/nav"):
            data = NAV
        elif request.url.path.endswith("/view"):
            data = {**SOURCE, "title": "Fixture", "duration": 120}
        elif request.url.path == "/x/player/playurl":
            assert request.url.params["bvid"] == BV and request.url.params["cid"] == SOURCE["cid"]
            data = state["play" if request.url.params["fnval"] == "16" else "merged"]
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_delivery(_):
        raise AssertionError("No publications")

    gateway = BilibiliGateway(
        Secrets(),
        Redactor(),
        no_delivery,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls


__all__ = [
    "AUDIO",
    "BV",
    "BilibiliGateway",
    "BusinessError",
    "CONFIG",
    "FULL",
    "HOST",
    "MERGED",
    "NAV",
    "PARTS",
    "Redactor",
    "SOURCE",
    "Secrets",
    "VIDEO",
    "asyncio",
    "copy",
    "fixture",
    "httpx",
    "pytest",
]
