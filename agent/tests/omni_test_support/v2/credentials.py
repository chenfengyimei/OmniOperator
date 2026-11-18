"""Reusable credentials fixtures, extracted without collecting test cases."""

import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from omni.adapters.credentials import Credentials
from omni.adapters.diagnostics import Redactor
from omni.adapters.persistence.credential_repository import CredentialRepositories
from omni.adapters.platform.runtime.paths import InstanceLock
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.v2_configuration_helpers import model_instance


class Backend:
    def __init__(self):
        self.values = {"fixture/key": "private-secret-canary"}
        self.started, self.release = threading.Event(), threading.Event()
        self.events = []
        self.block = ""
        self.failure = False

    def _enter(self, operation):
        self.events.append(operation)
        if self.block == operation:
            self.started.set()
            assert self.release.wait(10)
        if self.failure:
            raise OSError("private-native-backend-canary")

    def get(self, reference):
        self._enter("get")
        return self.values.get(reference)

    def set(self, reference, value):
        self._enter("set")
        self.values[reference] = value

    def delete(self, reference):
        self._enter("delete")
        self.values.pop(reference, None)


async def entered(backend):
    async with asyncio.timeout(10):
        while not backend.started.is_set():
            await asyncio.sleep(0.01)


__all__ = [
    "Backend",
    "BusinessError",
    "CredentialRepositories",
    "Credentials",
    "InstanceLock",
    "Redactor",
    "SimpleNamespace",
    "asyncio",
    "entered",
    "model_instance",
    "open_instance",
    "pytest",
    "sys",
    "threading",
]
