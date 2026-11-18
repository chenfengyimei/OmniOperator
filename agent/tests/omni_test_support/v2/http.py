"""Reusable http fixtures, extracted without collecting test cases."""

import json
import logging
import time

from fastapi.testclient import TestClient

from omni.application.objects import ObjectService
from omni.composition.http import http_app
from omni.domain.contracts import ModelReply
from omni.system.catalog import build_index


class LocalModel:
    async def complete(self, messages, tools, settings):
        return ModelReply("verified reply", cost_micro=0)


__all__ = [
    "LocalModel",
    "ModelReply",
    "ObjectService",
    "TestClient",
    "build_index",
    "http_app",
    "json",
    "logging",
    "time",
]
