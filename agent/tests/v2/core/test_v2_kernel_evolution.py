"""Production kernel policy, model failover, tool disclosure and completion receipts."""

import asyncio
import hashlib
import sys

import pytest
from httpx import Response
from omni_test_support.v2.execution import Capability, configure, wait_state

from omni.adapters.models import _rejection
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Invocation, ModelReply, Outcome, ToolSpec
from omni.domain.model_recovery import ModelRejected
from omni.domain.routing import POLICY_DEFAULT


async def kernel_policy(instance, agent, **kernel):
    return await instance.routing.save(agent, {**POLICY_DEFAULT, "kernel": {"background_learning": False, **kernel}}, 0)


async def workspace(instance, agent, path, **updates):
    path.mkdir(exist_ok=True)
    app = instance.application
    current = await app.settings(agent)
    await app.save_settings(agent, {**current["value"], "workspace": str(path), **updates}, current["revision"])


async def settle_turn(app, agent, run, *, approve=True):
    while True:
        if (row := await app.get_run(agent, run))["state"] in {"succeeded", "failed", "interrupted"}:
            return row
        for approval in await app.approvals(agent):
            await app.approve(agent, approval["id"], approve, approval["policy_revision"])
        await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    "usage,status",
    [
        ({"prompt_tokens": 0, "completion_tokens": 0}, 429),
        ({}, 429),
        ({"prompt_tokens": 1, "completion_tokens": 0}, 429),
        ({"prompt_tokens": 0, "completion_tokens": 0}, 500),
    ],
)
def test_vendor_wrapper_requires_matching_explicit_rejection_usage(usage, status):
    class VendorError(Exception):
        def __init__(self, response):
            self.response = response

    outer = VendorError(Response(429))
    sdk = VendorError(Response(status, json={"usage": usage}))
    outer.__context__ = VendorError(Response(429))
    outer.__context__.__context__ = sdk
    rejected = _rejection(outer)
    if status == 429 and usage == {"prompt_tokens": 0, "completion_tokens": 0}:
        assert isinstance(rejected, ModelRejected) and rejected.code == "model_rate_limited"
    else:
        assert rejected is None


@pytest.mark.parametrize("mode", ["rejected", "unknown", "revoked"])
def test_ordered_fallback_preserves_accounting_and_authority(tmp_path, mode):
    class Provider:
        def __init__(self):
            self.seen = []

        async def complete(self, messages, tools, settings):
            self.seen.append(settings["model"])
            if settings["model"] == "backup":
                return ModelReply("Backup succeeded", cost_micro=41, input_tokens=12, output_tokens=3)
            primary_entered.set()
            if mode == "revoked":
                await released.wait()
            if mode == "unknown":
                raise BusinessError("model_call_failed", "Unknown charge")
            raise ModelRejected("model_rate_limited", "Rejected before generation", status=429)

    async def scenario():
        nonlocal primary_entered, released
        primary_entered, released = asyncio.Event(), asyncio.Event()
        model = Provider()
        instance = await open_instance(tmp_path, model=model, capabilities=())
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            added = await instance.configurations.create(agent, "model", "Backup", True, {"model": "backup"})
            assert added["enabled"]
            policy = await kernel_policy(instance, agent, fallback_model_ids=[added["id"]])
            turn = await app.submit_turn(agent, None, "test-fallback", "Please solve the task", mode="agent")
            await asyncio.wait_for(primary_entered.wait(), 12)
            if mode == "revoked":
                await instance.routing.save(
                    agent, {**POLICY_DEFAULT, "kernel": {"fallback_model_ids": []}}, policy["revision"]
                )
                released.set()
            final = await wait_state(app, agent, turn["run_id"], {"failed", "succeeded"})
            calls = sorted(await app.query("calls", agent, run_id=turn["run_id"]), key=lambda call: call["created_at"])
            if mode == "rejected":
                assert final["state"] == "succeeded" and model.seen[-1] == "backup"
                assert [call["cost_micro"] for call in calls] == [0, 41]
                report = await instance.routing.report(agent, turn["run_id"])
                assert report["calls"][-1]["model_revision_id"] == added["revision_id"]
            else:
                assert final["state"] == "failed" and "backup" not in model.seen and len(calls) == 1
                assert calls[0]["state"] == ("unknown" if mode == "unknown" else "settled")
        finally:
            assert (await instance.close())["complete"]

    primary_entered = released = None
    asyncio.run(scenario())


def test_tool_discovery_offers_captured_tools_on_next_call_only(tmp_path):
    class Adapter:
        def __init__(self):
            self.invoked = []

        async def invoke(self, name, arguments, **kwargs):
            self.invoked.append(name)
            return ActionResult(Outcome.OK, "Found the required artifact")

        async def close(self, timeout_s):
            return True

    class DiscoveringModel:
        async def complete(self, messages, tools, settings):
            names = {spec.name for spec in tools}
            if not any(row["role"] == "tool" for row in messages):
                assert "discover_tools" in names and "find_fixture" not in names and "revoked_fixture" not in names
                return ModelReply(
                    "",
                    (
                        Invocation("lookup", "discover_tools", {"query": "find_fixture"}),
                        Invocation("too-soon", "find_fixture", {}),
                    ),
                    cost_micro=0,
                )
            if messages[-1].get("tool_call_id") == "too-soon":
                assert "find_fixture" in names and "revoked_fixture" not in names
                assert "outside" in messages[-1]["content"]
                return ModelReply("", (Invocation("allowed", "find_fixture", {}),), cost_micro=0)
            return ModelReply("done", cost_micro=0)

    async def scenario():
        adapter = Adapter()
        specs = [
            ToolSpec(f"fixture_{i}", "describe unrelated tool " + "word " * 110, {"type": "object"}) for i in range(56)
        ] + [
            ToolSpec("find_fixture", "Find the artifact", {"type": "object"}),
            ToolSpec("revoked_fixture", "Disallowed tool", {"type": "object"}),
        ]
        instance = await open_instance(
            tmp_path / "home", model=DiscoveringModel(), capabilities=tuple(Capability(spec, adapter) for spec in specs)
        )
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            await workspace(instance, agent, tmp_path / "workspace", disabled_tools=["revoked_fixture"])
            await kernel_policy(instance, agent, tool_discovery=True)
            receipt = await app.submit_turn(agent, None, "discover", "Find the artifact using a tool", mode="agent")
            final = await wait_state(app, agent, receipt["run_id"], {"succeeded", "failed"})
            assert final["state"] == "succeeded", final
            assert adapter.invoked == ["find_fixture"]
            report = await instance.routing.report(agent, receipt["run_id"])
            first = report["calls"][0]
            assert first["tool_bytes"] < 8192 < 32768 < first["full_tool_bytes"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "followup,expected",
    [
        ("read", "verified"),
        ("wrong-file", "needs_review"),
        ("rewrite", "needs_review"),
        ("passing-test", "verified"),
        ("failing-test", "needs_review"),
    ],
)
def test_completion_uses_actual_worker_receipts_even_in_step_limit_wrapup(tmp_path, followup, expected):
    class Writer:
        def __init__(self):
            self.commands = [
                ("files_write", {"path": "answer.txt", "content": "verified contents"}),
            ]
            if followup in {"read", "rewrite", "wrong-file"}:
                self.commands.append(
                    ("files_read", {"path": "other.txt" if followup == "wrong-file" else "answer.txt"})
                )
            if followup == "rewrite":
                self.commands.append(
                    (
                        "files_write",
                        {
                            "path": "answer.txt",
                            "content": "subsequent edit",
                            "expected_sha256": hashlib.sha256(b"verified contents").hexdigest(),
                        },
                    )
                )
            if followup.endswith("-test"):
                self.commands.append(
                    (
                        "shell_execute",
                        {
                            "argv": [
                                sys.executable,
                                "-I",
                                "-c",
                                "raise SystemExit(" + ("0" if followup == "passing-test" else "1") + ")",
                            ],
                            "verify_paths": ["answer.txt"],
                        },
                    )
                )

        async def complete(self, messages, tools, settings):
            if not self.commands:
                return ModelReply("Everything passed.", cost_micro=0)
            name, args = self.commands.pop(0)
            assert name in {tool.name for tool in tools}
            return ModelReply("", (Invocation("action" + str(len(self.commands)), name, args),), cost_micro=0)

    async def scenario():
        model = Writer()
        instance = await open_instance(tmp_path / "home", model=model)
        try:
            app = instance.application
            agent = await configure(app, instance._database)
            work = tmp_path / "workspace"
            await workspace(instance, agent, work, max_steps=len(model.commands))
            (work / "other.txt").write_text("verified contents", encoding="utf-8")
            await kernel_policy(instance, agent)
            turn = await app.submit_turn(agent, None, "check", "Write and verify", mode="agent")
            async with asyncio.timeout(30):
                final = await settle_turn(app, agent, turn["run_id"])
            assert final["state"] == "succeeded", final
            completion = final["completion"]
            assert completion["state"] == expected, completion
            files = [row for row in completion["items"] if row.get("target") == "answer.txt"]
            assert (
                len(files) == 1
                and files[0]["version"] == hashlib.sha256((work / "answer.txt").read_bytes()).hexdigest()
            )
            if expected == "verified":
                assert files[0]["check_action_id"] and files[0]["state"] == "verified"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
