"""Bootstrap registration keeps approval, capture and lifecycle ownership in the kernel."""

import asyncio

import pytest
from omni_test_support.v2.execution import configure, wait_state

from omni.application.capabilities import Capability, CapabilityPlugin, CapabilityRegistry
from omni.composition.instance import open_instance
from omni.domain.contracts import ActionResult, BusinessError, Invocation, ModelReply, Outcome, ToolSpec


def spec(name, *, writes=False):
    return ToolSpec(name, "Fixture tool", {"type": "object", "additionalProperties": False}, writes=writes)


class Adapter:
    def __init__(self, *scoped):
        self.scoped = scoped
        self.captures = []
        self.calls = []
        self.closed = 0

    async def tools_for_run(self, agent_id, run_id):
        self.captures.append((agent_id, run_id))
        return self.scoped

    async def invoke(self, name, arguments, *, operation_id, context):
        self.calls.append((name, context.agent_id, context.run_id, operation_id))
        return ActionResult(Outcome.OK, "Recorded")

    async def close(self, timeout_s):
        self.closed += 1
        return True


def test_registration_is_atomic_and_run_providers_cannot_shadow_owner_or_controls():
    base = Adapter()
    registry = CapabilityRegistry(frozenset({"forbidden"}), (), ())
    registry.register(CapabilityPlugin("base", (Capability(spec("first"), base),)))
    owner = Capability(spec("owner"), Adapter())
    with pytest.raises(ValueError, match="unique"):
        registry.register(CapabilityPlugin("base", owner_tools=(owner,)))
    with pytest.raises(ValueError, match="Duplicate"):
        registry.register(CapabilityPlugin("owner", (Capability(spec("first"), base),), (owner,)))
    with pytest.raises(ValueError, match="reserved"):
        registry.register(CapabilityPlugin("owner", (Capability(spec("forbidden"), base),)))
    registry.register(CapabilityPlugin("owner", owner_tools=(owner,), own_adapters=False))
    with pytest.raises(ValueError, match="lifecycle owner"):
        registry.register_provider("shared", base)
    assert tuple(registry.tools) == ("first",)
    assert registry.owned_adapters() == (base,)

    control = Capability(spec("forbidden"), Adapter())
    provider = Adapter(spec("scoped"))
    registry.register_provider("test", provider)
    registry.seal()
    with pytest.raises(ValueError, match="before start"):
        registry.register(CapabilityPlugin("late", (Capability(spec("scoped"), Adapter()),)))
    captured = asyncio.run(registry.capture("agent", "run", (control,)))
    assert list(captured) == ["first", "forbidden", "scoped"]
    assert provider.captures == [("agent", "run")]
    for name in ("first", "owner", "forbidden"):
        provider.scoped = (spec(name),)
        with pytest.raises(BusinessError) as exc:
            asyncio.run(registry.capture("agent", "fresh", ()))
        assert exc.value.code == "test_tool_collision"
    assert tuple(captured) == ("first", "forbidden", "scoped")


def test_composed_plugin_uses_durable_write_approval_and_one_shutdown(tmp_path):
    adapter = Adapter()
    plugin = CapabilityPlugin(
        "fixture",
        (Capability(spec("fixture_write", writes=True), adapter), Capability(spec("fixture_read"), adapter)),
        (Capability(spec("owner_only", writes=True), adapter),),
    )

    class Model:
        async def complete(self, messages, tools, settings):
            assert tuple(tool.name for tool in tools) == ("fixture_write", "fixture_read")
            if messages[-1]["role"] == "tool":
                return ModelReply("Done", cost_micro=0)
            return ModelReply("", (Invocation("one", "fixture_write", {}),), cost_micro=0)

    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=(), capability_plugins=(plugin,))
        app = instance.application
        try:
            agent = await configure(app, instance._database)
            with pytest.raises(ValueError, match="before start"):
                app.attach_plugin(CapabilityPlugin("late"))
            turn = await app.submit_turn(agent, None, "plugin", "Invoke the tool", mode="agent")
            run = turn["run_id"]
            async with asyncio.timeout(15):
                while not (pending := await app.approvals(agent)):
                    await asyncio.sleep(0.01)
            assert not adapter.calls
            approval = pending[0]
            await app.approve(agent, approval["id"], True, approval["policy_revision"])
            assert (await wait_state(app, agent, run, {"succeeded", "failed"}))["state"] == "succeeded"
            actions = await app.query("actions", agent, run_id=run)
            assert adapter.calls == [("fixture_write", agent, run, actions[0]["id"])]
        finally:
            assert (await instance.close())["complete"]
        assert adapter.closed == 1

    asyncio.run(scenario())
