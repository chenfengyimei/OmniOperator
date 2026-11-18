"""Reusable macos_services fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import plistlib
from pathlib import Path

import pytest

from omni.adapters.platform.services import service_query as q
from omni.adapters.platform.services import service_registration as r
from omni.adapters.platform.services.macos_service import (
    VERSION,
    MacOSServiceInspection,
    disabled_override,
    loaded_definition,
)
from omni.domain.contracts import BusinessError
from omni.domain.service_deployment import manager_identity
from omni_test_support.paths import PRODUCT_ROOT as PRODUCT_ROOT
from omni_test_support.v2.service_deployment import plan


def job(value, **changes):
    return {**plistlib.loads(r.render(value)[manager_identity(value)["artifact"]]), **changes}


def native_response(value, nonce, *, loaded=True, data=None, **changes):
    return {
        "version": VERSION,
        "domain": manager_identity(value)["domain"],
        "nonce": nonce,
        "label": manager_identity(value)["name"],
        "uid": value["request"]["principal"]["id"],
        "complete": True,
        "loaded": loaded,
        "job": job(value, PID=0) if data is None and loaded else data,
        "error_code": "",
        **changes,
    }


def acceptance_script():
    path = PRODUCT_ROOT / "scripts/accept_macos_services.py"
    spec = importlib.util.spec_from_file_location("macos_service_acceptance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


__all__ = [
    "BusinessError",
    "MacOSServiceInspection",
    "Path",
    "VERSION",
    "acceptance_script",
    "asyncio",
    "disabled_override",
    "importlib",
    "job",
    "json",
    "loaded_definition",
    "manager_identity",
    "native_response",
    "plan",
    "plistlib",
    "pytest",
    "q",
    "r",
]
