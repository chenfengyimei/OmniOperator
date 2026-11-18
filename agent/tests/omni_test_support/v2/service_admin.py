"""Reusable service_admin fixtures, extracted without collecting test cases."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from omni.adapters.platform.services import service_admin as backend
from omni.adapters.platform.services.service_admin_bundle import materialize_admin_bundle
from omni.adapters.platform.services.service_admin_policy import RootServicePolicy
from omni.adapters.platform.services.service_templates import render
from omni.adapters.platform.services.systemd_state import systemd_state
from omni.adapters.workers import service_admin_boot as boot
from omni.composition.cli import parser
from omni.domain.contracts import BusinessError
from omni.domain.service_admin import (
    admin_operation,
    decode,
    encode,
    request,
    response,
    socket_path,
    validate_request,
    validate_response,
)
from omni.domain.service_deployment import manager_identity
from omni.domain.service_operations import make_operation
from omni_test_support.v2.service_deployment import plan
from omni_test_support.v2.service_query import systemd

SHA = "a" * 64


def operation(action="install"):
    return make_operation(
        plan("linux-system"), action, "830a07ac-f0e8-4a61-896a-f6ba73d76465", "2026-11-07T00:00:00+00:00"
    )


class Policy:
    def close(self):
        self.native_lock_released = True

    def __init__(self, action="install", installed=False):
        self.operation = operation(action)
        self.approval = self.operation
        self.plan = self.operation["deployment"]
        self.manager = manager_identity(self.plan)
        self.installed, self.enabled = installed, installed
        self.dispatched_once = False
        self.calls = []
        self.reject_paths = False
        self.drift = False

    def authorize(self):
        self.calls.append("authorize")

    def select(self, key):
        assert key == "" and self.approval == self.operation

    def program(self):
        return "/fixed/root/systemctl"

    def definition(self):
        return self.installed, (
            b"foreign" if self.drift else render(self.plan)["omni.service"]
        ) if self.installed else None

    def dispatched(self):
        return self.dispatched_once

    def reject_dropins(self, output):
        self.calls.append("reject_dropins")
        if self.reject_paths:
            raise BusinessError("service_admin_dropins_present", "No override is authorized")

    def record(self, phase, result):
        self.calls.append(phase)
        if phase == "dispatch":
            self.dispatched_once = True

    def publish_definition(self):
        assert self.dispatched_once
        self.calls.append("publish")
        self.installed = True

    def remove_definition(self):
        assert self.dispatched_once
        self.calls.append("remove")
        self.installed = False


class Commands:
    def __init__(self, policy):
        self.policy = policy
        self.calls = []
        self.fail = ""
        self.running = False
        self.cleanup = True

    def run(self, argv):
        self.calls.append(argv)
        assert argv[0] == "/fixed/root/systemctl" and argv[1] == "--system"
        assert not {"start", "stop", "restart", "--now", "--force", "sudo"} & set(argv)
        command = argv[2]
        if command == self.fail:
            raise OSError("credential=never expose")
        if "--property=UnitPath" in argv:
            return 0, b"/etc/systemd/system /usr/lib/systemd/system\n"
        if command == "show":
            return (0 if self.policy.installed else 4), systemd(
                self.policy.plan,
                LoadState="loaded" if self.policy.installed else "not-found",
                MainPID="42" if self.running else "0",
                ActiveState="active" if self.running else "inactive",
                SubState="running" if self.running else "dead",
                FragmentPath=self.policy.manager["installed_path"] if self.policy.installed else "",
            )
        if command == "is-enabled":
            return (
                (0, b"enabled\n")
                if self.policy.enabled
                else (1, b"disabled\n" if self.policy.installed else b"not-found\n")
            )
        if command == "enable":
            self.policy.enabled = True
        if command == "disable":
            self.policy.enabled = False
        return 0, b""

    def close(self, timeout_s=2):
        return self.cleanup


__all__ = [
    "BusinessError",
    "Commands",
    "Policy",
    "RootServicePolicy",
    "SHA",
    "admin_operation",
    "backend",
    "boot",
    "decode",
    "encode",
    "hashlib",
    "json",
    "make_operation",
    "manager_identity",
    "materialize_admin_bundle",
    "operation",
    "parser",
    "plan",
    "pytest",
    "render",
    "request",
    "response",
    "socket_path",
    "subprocess",
    "sys",
    "systemd",
    "systemd_state",
    "validate_request",
    "validate_response",
]
