"""Reusable extensions fixtures, extracted without collecting test cases."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from omni.adapters.persistence import schema as s
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.extensions import (
    artifact_descriptors,
    catalog,
    catalog_fingerprint,
    parse_manifest,
    tool_artifacts,
)
from omni_test_support.v2.v2_extension_helpers import (
    PUBLIC_TOOL,
    PluginModel,
    authorization,
    configured,
    installation,
    installed,
    plugin_source,
    wait_run,
)

MANIFEST = {
    "format": "omni-python-extension-1",
    "id": "fixture",
    "version": "1",
    "entry": "plugin.py",
    "tools": [
        {
            "name": "echo",
            "description": "Return the approved local value",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "writes": True,
            "timeout_s": 30,
        }
    ],
}

__all__ = [
    "BusinessError",
    "MANIFEST",
    "PUBLIC_TOOL",
    "Path",
    "PluginModel",
    "artifact_descriptors",
    "asyncio",
    "authorization",
    "catalog",
    "catalog_fingerprint",
    "configured",
    "installation",
    "installed",
    "json",
    "open_instance",
    "parse_manifest",
    "plugin_source",
    "pytest",
    "s",
    "select",
    "tool_artifacts",
    "wait_run",
]
