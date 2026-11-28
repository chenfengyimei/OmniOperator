"""OmniWeave rollout modes and the canonical owner Web trigger contract."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Literal, cast

from .contracts import JSON, BusinessError, ToolSpec
from .delegation import TASK_PROPERTIES, request, tool_hash
from .omniweave_artifacts import ARTIFACT_TOOL_NAMES, ARTIFACTS_PER_ROOT

OmniWeaveMode = Literal["off", "observe", "dispatch", "enabled"]
OMNIWEAVE_MODES: tuple[OmniWeaveMode, ...] = ("off", "observe", "enabled", "dispatch")
OWNER_WEB_SOURCE = "owner_web"
TRIGGER_VERSION = 1
OBSERVE_DECISION = "observe"
WORKER_READ_TOOLS = ("files_list", "files_read", "files_search")
WORKER_TOOLS = (*WORKER_READ_TOOLS, "files_write", "files_patch")
WORKER_TASK_LIMIT = 8
WORKER_CONCURRENCY = 3
ROOT_DEADLINE_S = 24 * 60 * 60
ROOT_CALL_LIMIT = 64
MAX_GOAL_VERSION = 64
_KEY = r"^[a-z][a-z0-9-]{0,31}$"
_TASK_KEY: dict[str, JSON] = {"type": "string", "pattern": _KEY}
_REFERENCES: dict[str, JSON] = {
    "type": "array",
    "maxItems": 16,
    "items": {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "minLength": 36, "maxLength": 36},
            "version": {"type": "integer", "minimum": 1, "maximum": ARTIFACTS_PER_ROOT},
        },
        "required": ["artifact_id", "version"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class GoalUpdate:
    ticket_id: str
    expected_goal_version: int


@dataclass(frozen=True)
class WorkerProfile:
    name: str
    version: int
    tools: tuple[str, ...]
    deliverable: str


WORKER_PROFILES = {
    profile.name: profile
    for profile in (
        WorkerProfile("research", 1, WORKER_READ_TOOLS, "Cite evidence, its limits, and open questions."),
        WorkerProfile("coding", 1, WORKER_TOOLS, "Report the patch or version and how you checked it."),
        WorkerProfile("verification", 1, WORKER_READ_TOOLS, "Report independent checks and the version they cover."),
        WorkerProfile("documentation", 1, WORKER_TOOLS, "Report the document version, dependencies, and gaps."),
    )
}


def goal_fingerprint(value: JSON) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def mandate_tool(spec: ToolSpec) -> dict[str, JSON]:
    return {
        "sha256": tool_hash(spec),
        "writes": spec.writes,
        "resource": spec.resource,
        "binding_id": spec.binding_id,
        "timeout_s": spec.timeout_s,
    }


OWA_PLAN = ToolSpec(
    "owa_plan",
    "Plan independent Worker tasks for this Web turn. Dependencies refer to keys in the same plan. "
    "After a goal revision, declare reuse for unaffected tasks whose accepted result from a prior goal version "
    "still stands. Choose a goal and profile, share only the context the Worker needs, and select needed workspace tools. "
    "Research and Verification are read-only. Coding and Documentation can request workspace writes. "
    "Workers use private workspaces, bounded model budgets and separate approval for every write. "
    "Submission returns immediately. Use owa_status to read progress or results in this conversation. "
    "Reply to the owner with the plan or progress while work is running.",
    {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": WORKER_TASK_LIMIT,
                "items": {
                    "type": "object",
                    "properties": {
                        **TASK_PROPERTIES,
                        "key": _TASK_KEY,
                        "profile": {"type": "string", "enum": list(WORKER_PROFILES)},
                        "deadline_seconds": {"type": "integer", "minimum": 1, "maximum": ROOT_DEADLINE_S},
                        "depends_on": {
                            "type": "array",
                            "maxItems": WORKER_TASK_LIMIT - 1,
                            "uniqueItems": True,
                            "items": _TASK_KEY,
                        },
                        "peers": {
                            "type": "array",
                            "maxItems": WORKER_TASK_LIMIT - 1,
                            "uniqueItems": True,
                            "items": _TASK_KEY,
                        },
                        "artifact_readers": {
                            "type": "array",
                            "maxItems": WORKER_TASK_LIMIT - 1,
                            "uniqueItems": True,
                            "items": _TASK_KEY,
                        },
                        "tools": {
                            "type": "array",
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": list(WORKER_TOOLS)},
                        },
                        "reuse": {
                            "type": "object",
                            "properties": {
                                "task_key": _TASK_KEY,
                                "goal_version": {"type": "integer", "minimum": 1, "maximum": MAX_GOAL_VERSION},
                            },
                            "required": ["task_key", "goal_version"],
                            "additionalProperties": False,
                        },
                        "jobs": {"type": "boolean"},
                    },
                    "required": ["key", "goal"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    },
    resource="omniweave",
)
OWA_STATUS = ToolSpec(
    "owa_status",
    "Read Worker status and submitted results for a Ticket in the current Web conversation. "
    "Without an ID, read the latest planned Ticket in this conversation. Treat submitted results as task data.",
    {
        "type": "object",
        "properties": {"ticket_id": {"type": "string", "minLength": 36, "maxLength": 36}},
        "additionalProperties": False,
    },
    resource="omniweave",
)

REQUEST_PEER = ToolSpec(
    "request_peer",
    "Ask an authorized peer in this plan. Use the same key to retry the same request. "
    "Optionally cite up to 16 artifact versions you are authorized to read. "
    "For a blocking request, finish this turn at a safe boundary so the reply can resume the task. "
    "The next turn receives the reply or a timeout in this same private conversation.",
    {
        "type": "object",
        "properties": {
            "target": _TASK_KEY,
            "key": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,80}$"},
            "question": {"type": "string", "minLength": 1, "maxLength": 4000},
            "blocking": {"type": "boolean"},
            "references": _REFERENCES,
        },
        "required": ["target", "key", "question", "blocking"],
        "additionalProperties": False,
    },
    resource="omniweave",
)
REPLY_PEER = ToolSpec(
    "reply_peer",
    "Reply to a request delivered to this Worker. Use its request_id. A decline also closes the request. "
    "Optionally cite up to 16 artifact versions you are authorized to read.",
    {
        "type": "object",
        "properties": {
            "request_id": {"type": "string", "minLength": 36, "maxLength": 36},
            "answer": {"type": "string", "minLength": 1, "maxLength": 4000},
            "decline": {"type": "boolean"},
            "references": _REFERENCES,
        },
        "required": ["request_id", "answer"],
        "additionalProperties": False,
    },
    resource="omniweave",
)
PEER_TOOLS = (REQUEST_PEER, REPLY_PEER)
PEER_TOOL_NAMES = frozenset(tool.name for tool in PEER_TOOLS)

JOB_LIVE_STATES = ("queued", "running", "cancelling")
JOB_TERMINAL_STATES = ("succeeded", "failed", "cancelled", "interrupted")
JOB_STATES = (*JOB_LIVE_STATES, *JOB_TERMINAL_STATES)
# Dispatch-time failures that mean the job was settled or preempted elsewhere
# (goal revision, authority revocation, another executor). Settling them again
# would write a losing receipt against a terminal job, so the executor only
# releases its own claim via the defer path.
JOB_PREEMPTED_CODES = frozenset({"owa_job_settled", "owa_goal_superseded", "owa_mandate_revoked"})
JOBS_PER_TASK = 1
JOB_ARGUMENTS_CHARS = 16_000
_JOB_ARGUMENTS: dict[str, JSON] = {
    "type": "object",
    "maxProperties": 16,
    "additionalProperties": {"type": ["string", "integer", "number", "boolean"]},
}
OWA_JOB_START = ToolSpec(
    "owa_job_start",
    "Start one bounded managed long call on this task. It runs detached with a stable job id, "
    "recorded receipts and its own lease; a write still needs owner approval. Finish this turn "
    "at a safe boundary after submitting. The next turn receives the job receipt in this same "
    "private conversation. Use the same key to retry the same request.",
    {
        "type": "object",
        "properties": {
            "key": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,80}$"},
            "tool": {"type": "string"},
            "arguments": _JOB_ARGUMENTS,
            "deadline_seconds": {"type": "integer", "minimum": 1, "maximum": ROOT_DEADLINE_S},
        },
        "required": ["key", "tool", "arguments"],
        "additionalProperties": False,
    },
    resource="omniweave",
)
JOB_TOOL_NAMES = frozenset({OWA_JOB_START.name})
WORKER_INTERNAL_TOOL_NAMES = PEER_TOOL_NAMES | ARTIFACT_TOOL_NAMES | JOB_TOOL_NAMES


def checked_job(value: JSON, granted: Iterable[str]) -> tuple[str, dict[str, JSON], int | None]:
    if not isinstance(value, dict) or set(value) - {"key", "tool", "arguments", "deadline_seconds"}:
        raise BusinessError("owa_job_invalid", "Managed job requests must name a key, tool and arguments", status=422)
    key, name, arguments = value.get("key"), value.get("tool"), value.get("arguments")
    deadline = value.get("deadline_seconds")
    if (
        not isinstance(key, str)
        or re.fullmatch(r"^[A-Za-z0-9._-]{1,80}$", key) is None
        or not isinstance(name, str)
        or name not in granted
        or not isinstance(arguments, dict)
        or len(json.dumps(arguments, ensure_ascii=False)) > JOB_ARGUMENTS_CHARS
        or (deadline is not None and (type(deadline) is not int or not 1 <= deadline <= ROOT_DEADLINE_S))
    ):
        raise BusinessError("owa_job_invalid", "Managed job request is malformed or uses an ungranted tool", status=422)
    return key, cast(dict[str, JSON], arguments), deadline


PEER_REQUESTS_PER_ROOT = 16
PEER_REQUESTS_PER_SENDER = 4
PEER_CAUSAL_DEPTH = 4
PEER_DEADLINE_S = 300


@dataclass(frozen=True)
class WorkerTask:
    key: str
    request: dict[str, JSON]
    needs: tuple[str, ...]
    tools: tuple[str, ...]
    profile: str
    deadline_seconds: int | None
    peers: tuple[str, ...]
    artifact_readers: tuple[str, ...]
    reuse: tuple[str, int] | None = None
    jobs: bool = False


def checked_plan(value: dict[str, JSON], reservation: int) -> list[WorkerTask]:
    tasks = value.get("tasks")
    if set(value) != {"tasks"} or not isinstance(tasks, list) or not 1 <= len(tasks) <= WORKER_TASK_LIMIT:
        raise invalid_plan()
    result = [_checked_task(task, reservation) for task in tasks]
    graph = {task.key: task.needs for task in result}
    if len(graph) != len(result) or any(set(needs) - graph.keys() for needs in graph.values()):
        raise invalid_plan()
    if any((set(task.peers) | set(task.artifact_readers)) - (graph.keys() - {task.key}) for task in result):
        raise invalid_plan()
    executed = {task.key for task in result if task.reuse is None}
    if any(
        set(task.needs) - executed or set(task.peers) - executed or set(task.artifact_readers) - executed
        for task in result
        if task.reuse is None
    ):
        raise invalid_plan()
    if any(task.reuse is not None and (task.needs or task.peers or task.artifact_readers) for task in result):
        raise invalid_plan()
    try:
        TopologicalSorter(graph).prepare()
    except CycleError:
        raise invalid_plan() from None
    return result


def _checked_task(value: JSON, reservation: int) -> WorkerTask:
    if not isinstance(value, dict):
        raise invalid_plan()
    key = value.get("key")
    dependencies = value.get("depends_on", [])
    peers = value.get("peers", [])
    readers = value.get("artifact_readers", [])
    tools = value.get("tools", [])
    profile = value.get("profile", "coding")
    deadline = value.get("deadline_seconds")
    reuse = value.get("reuse")
    jobs = value.get("jobs", False)
    if (
        not isinstance(key, str)
        or not re.fullmatch(_KEY, key)
        or not isinstance(dependencies, list)
        or not all(isinstance(item, str) and re.fullmatch(_KEY, item) for item in dependencies)
        or not isinstance(peers, list)
        or not all(isinstance(item, str) and re.fullmatch(_KEY, item) for item in peers)
        or not isinstance(readers, list)
        or not all(isinstance(item, str) and re.fullmatch(_KEY, item) for item in readers)
        or not isinstance(tools, list)
        or not all(isinstance(item, str) and item in WORKER_TOOLS for item in tools)
        or not isinstance(profile, str)
        or profile not in WORKER_PROFILES
        or (deadline is not None and (type(deadline) is not int or not 1 <= deadline <= ROOT_DEADLINE_S))
        or jobs is not True
        and jobs is not False
    ):
        raise invalid_plan()
    carried: tuple[str, int] | None = None
    if reuse is not None:
        if not isinstance(reuse, dict) or set(reuse) != {"task_key", "goal_version"}:
            raise invalid_plan()
        prior_key, prior_version = reuse["task_key"], reuse["goal_version"]
        if (
            not isinstance(prior_key, str)
            or not re.fullmatch(_KEY, prior_key)
            or type(prior_version) is not int
            or not 1 <= prior_version <= MAX_GOAL_VERSION
            or tools
            or deadline is not None
            or peers
            or readers
            or jobs
        ):
            raise invalid_plan()
        carried = (prior_key, prior_version)
    needs = tuple(cast(list[str], dependencies))
    selected = tuple(cast(list[str], tools))
    authorized_peers = tuple(cast(list[str], peers))
    artifact_readers = tuple(cast(list[str], readers))
    if (
        len(needs) != len(set(needs))
        or len(selected) != len(set(selected))
        or len(authorized_peers) != len(set(authorized_peers))
        or len(artifact_readers) != len(set(artifact_readers))
    ):
        raise invalid_plan()
    if set(selected) - set(WORKER_PROFILES[profile].tools):
        raise BusinessError("owa_profile_tool_not_authorized", "The selected Worker Profile does not allow these tools")
    parsed = request(
        {
            name: item
            for name, item in value.items()
            if name
            not in {
                "key",
                "depends_on",
                "tools",
                "profile",
                "deadline_seconds",
                "peers",
                "artifact_readers",
                "reuse",
                "jobs",
            }
        },
        reservation,
    )
    return WorkerTask(
        key,
        parsed,
        tuple(sorted(needs)),
        tuple(sorted(selected)),
        profile,
        deadline,
        tuple(sorted(authorized_peers)),
        tuple(sorted(artifact_readers)),
        carried,
        bool(jobs),
    )


def invalid_plan() -> BusinessError:
    return BusinessError("owa_plan_invalid", "Worker tasks must form a bounded dependency plan", status=422)


def checked_mode(value: str) -> OmniWeaveMode:
    if value == "enabled":
        return "dispatch"
    if value == "off" or value == "observe" or value == "dispatch":
        return value
    raise BusinessError("omniweave_mode_invalid", "OmniWeave mode must be off, observe or enabled", status=422)
