"""Deterministic service artifacts. Rendering never installs or controls a service."""

from __future__ import annotations

import json
import ntpath
import plistlib
import posixpath
import subprocess
from xml.etree import ElementTree as ET

from ...domain.service_deployment import manager_identity, service_argv, startup, validate_plan

TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _windows(plan: dict) -> bytes:
    request, manager, argv = plan["request"], manager_identity(plan), service_argv(plan)

    def element(parent: ET.Element, name: str, text: str | None = None, **attributes: str) -> ET.Element:
        child = ET.SubElement(parent, name, attributes)
        child.text = text
        return child

    root = ET.Element("Task", {"xmlns": TASK_NAMESPACE, "version": "1.4"})
    registration = element(root, "RegistrationInfo")
    element(registration, "Description", "OmniOperator V2; reviewed plan " + plan["sha256"])
    element(registration, "URI", "\\" + manager["name"])
    triggers = element(root, "Triggers")
    trigger = element(triggers, "LogonTrigger")
    element(trigger, "Enabled", "true")
    element(trigger, "UserId", request["principal"]["id"])
    principals = element(root, "Principals")
    principal = element(principals, "Principal", id="Owner")
    element(principal, "UserId", request["principal"]["id"])
    element(principal, "LogonType", "InteractiveToken")
    element(principal, "RunLevel", "LeastPrivilege")
    settings = element(root, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "false"),
        ("StartWhenAvailable", "false"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT0S"),
    ):
        element(settings, name, value)
    actions = element(root, "Actions", Context="Owner")
    execute = element(actions, "Exec")
    element(execute, "Command", argv[0])
    element(execute, "Arguments", subprocess.list2cmdline(argv[1:]))
    element(execute, "WorkingDirectory", request["working_directory"])
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _macos(plan: dict) -> bytes:
    request, manager = plan["request"], manager_identity(plan)
    value = {
        "Label": manager["name"],
        "ProgramArguments": service_argv(plan),
        "WorkingDirectory": request["working_directory"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ExitTimeOut": 45,
        "Umask": 0o077,
        "StandardOutPath": posixpath.join(request["home"], "logs", "service.stdout.log"),
        "StandardErrorPath": posixpath.join(request["home"], "logs", "service.stderr.log"),
        "EnvironmentVariables": {"PYTHONUTF8": "1"},
    }
    if request["target"] == "macos-system":
        value["UserName"] = request["principal"]["name"]
    else:
        value["LimitLoadToSessionType"] = "Aqua"
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def _systemd_quote(value: str, *, argument: bool = False) -> str:
    # Unit specifiers apply to every setting; dollar expansion applies to
    # ExecStart arguments. This is systemd's C quoting, never a shell command.
    value = value.replace("%", "%%")
    if argument:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _linux(plan: dict) -> bytes:
    request = plan["request"]
    lines = [
        "[Unit]",
        "Description=OmniOperator V2 " + request["instance_id"],
        "",
        "[Service]",
        "Type=exec",
        "ExecStart=" + " ".join(_systemd_quote(part, argument=True) for part in service_argv(plan)),
        "WorkingDirectory=" + _systemd_quote(request["working_directory"]),
        "User=" + request["principal"]["id" if request["target"] == "linux-system" else "name"],
        "UMask=0077",
        "KillMode=mixed",
        "TimeoutStopSec=45",
        "Restart=no",
        "Environment=PYTHONUTF8=1",
        "StandardOutput=journal",
        "StandardError=journal",
    ]
    lines += [
        "",
        "[Install]",
        "WantedBy=" + ("multi-user.target" if request["target"] == "linux-system" else "default.target"),
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _commands(plan: dict) -> dict:
    request, manager = plan["request"], manager_identity(plan)
    target = request["target"]
    windows = target == "windows-user"
    join = ntpath.join if windows else posixpath.join
    source = join(request["bundle_dir"], manager["artifact"])
    installed, name = manager["installed_path"], manager["name"]
    if windows:
        return {
            "install": [["schtasks.exe", "/Create", "/TN", name, "/XML", source]],
            "uninstall_after_verified_shutdown": [["schtasks.exe", "/Delete", "/TN", name]],
            "query": [["schtasks.exe", "/Query", "/TN", name, "/XML"]],
        }
    if target.startswith("macos-"):
        install = [["/bin/cp", "-n", source, installed]]
        if target.endswith("-system"):
            install += [["/usr/sbin/chown", "root:wheel", installed], ["/bin/chmod", "0644", installed]]
        else:
            install.append(["/bin/chmod", "0600", installed])
        # Offline registration owns the application lock. Loading RunAtLoad here
        # would race a process that cannot acquire that lock.
        install.append(["/bin/launchctl", "enable", manager["domain"] + "/" + name])
        return {
            "install": install,
            "activate_after_maintenance_and_new_review": [
                ["/bin/launchctl", "bootstrap", manager["domain"], installed]
            ],
            "uninstall_after_verified_shutdown": [
                ["/bin/launchctl", "disable", manager["domain"] + "/" + name],
                ["/bin/launchctl", "bootout", manager["domain"] + "/" + name],
                ["/bin/rm", "--", installed],
            ],
            "query": [["/bin/launchctl", "print", manager["domain"] + "/" + name]],
        }
    systemctl = ["/usr/bin/systemctl"] + ([] if target.endswith("-system") else ["--user"])
    install = [["/bin/cp", "-n", source, installed]]
    if target.endswith("-system"):
        install.append(["/usr/bin/chown", "0:0", installed])
    install += [
        ["/bin/chmod", "0644" if target.endswith("-system") else "0600", installed],
        [*systemctl, "daemon-reload"],
        [*systemctl, "enable", name],
    ]
    return {
        "install": install,
        "uninstall_after_verified_shutdown": [
            [*systemctl, "disable", name],
            ["/bin/rm", "--", installed],
            [*systemctl, "daemon-reload"],
        ],
        "query": [[*systemctl, "show", name]],
    }


def render(plan: object) -> dict[str, bytes]:
    value = validate_plan(plan)
    request, manager = value["request"], manager_identity(value)
    target = request["target"]
    service = (
        _windows(value) if target == "windows-user" else _macos(value) if target.startswith("macos-") else _linux(value)
    )
    notes = {
        "format": "omni-service-instructions-1",
        "plan_sha256": value["sha256"],
        "manager": manager,
        "startup_argv": service_argv(value),
        "commands_not_executed": _commands(value),
        "requirements": [
            "This bundle is a review artifact. No command is executed by rendering or materialization.",
            "Verify package, interpreter, instance identity, bundle location, ownership and file fingerprints before installation.",
            "Initialize this independent V2 home and its private logs directory before registration; never initialize from a service template.",
            "Only immutable, non-editable programs may start. Preview bundles from an editable checkout cannot run.",
            "Review each proposed command independently; cp -n refusal must not be followed by chmod/chown of a pre-existing foreign file.",
            "Create and verify the installation directory as the correct user or administrator; system targets require deployment privileges.",
            "Service registration may start work now or at the next login. No restart, start or stop is authorized by this file.",
            "Require matching instance, boot, PID creation identity, plan, build and readiness. A running task or open port is insufficient.",
            "Never automatically repeat an installation or external write with an unknown outcome; query and reconcile its identity first.",
            "Verify graceful shutdown and absence of cleanup residuals before uninstalling; do not remove instance data or secret files.",
            "OS shutdown timeouts and forced termination are not proof of successful application settlement.",
            "The operating-system manager does not provide a sandbox; system/headless operation does not grant desktop-session access.",
            "Windows task visibility settings do not hide a Python console window.",
            "No passwords, tokens or key contents are stored here; master_key_file is only an explicit external reference.",
        ],
    }
    return {
        "deployment.json": _json(value),
        "startup.json": _json(startup(value)),
        manager["artifact"]: service,
        "README.txt": _json(notes),
    }
