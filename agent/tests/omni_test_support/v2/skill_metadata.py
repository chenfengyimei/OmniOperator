"""Reusable skill_metadata fixtures, extracted without collecting test cases."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient

from omni.adapters.skill_packages import capture
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni_test_support.v2.routing import Model
from omni_test_support.v2.skills import markdown

DESCRIPTION = "description: Reviewed reusable procedure\n"

BAD = {
    "name": "name: first-skill\nname: test-skill\n" + DESCRIPTION,
    "same_name": "name: test-skill\nname: test-skill\n" + DESCRIPTION,
    "description": "name: test-skill\ndescription: PRIVATE_METADATA_CANARY\n" + DESCRIPTION,
    "platforms": "name: test-skill\n" + DESCRIPTION + "platforms: [linux]\nplatforms: [windows]\n",
    "version": "name: test-skill\n" + DESCRIPTION + "version: old\nversion: current\n",
    "escaped_key": 'name: first-skill\n"na\\u006de": test-skill\n' + DESCRIPTION,
    "aliased_key": "&field name: first-skill\n*field: test-skill\n" + DESCRIPTION,
    "merge_override": "<<: {name: first-skill}\nname: test-skill\n" + DESCRIPTION,
    "merge_sequence": "<<: [{name: first-skill}, {name: test-skill}]\n" + DESCRIPTION,
    "merge_duplicate": "<<: {name: first-skill, name: test-skill}\n" + DESCRIPTION,
    "repeated_merge": "<<: {name: first-skill}\n<<: {name: test-skill}\n" + DESCRIPTION,
}

GOOD = {
    "plain": "name: test-skill\n" + DESCRIPTION + "version: v1\n",
    "flow": "{name: test-skill, description: Reviewed reusable procedure}\n",
    "escaped": '"na\\u006de": test-skill\n' + DESCRIPTION,
    "merge": "<<: {name: test-skill, platforms: [windows]}\n" + DESCRIPTION,
    "alias": "name: &label test-skill\ndescription: *label\n",
    "folded": "name: test-skill\ndescription: >-\n  Reviewed reusable procedure\n",
}


def document(header):
    return ("---\n" + header + "---" + markdown().split("---", 2)[2]).encode()


def payload(raw, format):
    if format == "markdown":
        return raw
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", raw)
            archive.writestr("references/guide.md", "Declared reference text.")
        return output.getvalue()


__all__ = [
    "BAD",
    "BusinessError",
    "DESCRIPTION",
    "GOOD",
    "Model",
    "TestClient",
    "asyncio",
    "capture",
    "document",
    "http_app",
    "io",
    "markdown",
    "open_instance",
    "payload",
    "pytest",
    "yaml",
    "zipfile",
]
