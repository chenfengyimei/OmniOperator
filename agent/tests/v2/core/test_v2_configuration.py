"""Typed configuration, immutable runs, capacity enforcement and authorization regressions."""

import asyncio
from contextlib import nullcontext

import pytest
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_instance, model_uow
from sqlalchemy import update

from omni.adapters.persistence import schema as s
from omni.adapters.token_counter import ConservativeTokenCounter
from omni.application.configuration import ConfigurationService
from omni.application.model_calls import ModelCalls
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError, ModelReply, ToolSpec
from omni.domain.metering import TokenEstimate, serialized_request


def test_typed_configuration_revisions_cas_isolation_and_immutability(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            service = ConfigurationService(db, ConservativeTokenCounter())
            current = await db.read(lambda u: u.settings(agent))
            model = await service.get(agent, current["value"]["model_profile_id"])
            edited = await service.save(agent, model["id"], "Named model", True, {**model["value"], "model": "fake"}, 1)
            assert edited["revision"] == 2 and edited["revision_id"] != model["revision_id"]
            with pytest.raises(BusinessError, match="changed"):
                await service.save(agent, model["id"], "overwrite", False, {}, 1)
            history = (await service.history(agent, model["id"]))["items"]
            assert [row["value"]["model"] for row in history] == ["", "fake"]
            other = (await db.write(lambda u: u.create_agent("other")))["id"]
            with pytest.raises(BusinessError) as error:
                await service.get(other, model["id"])
            assert error.value.status == 404
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.model_options)
                        .where(s.model_options.c.revision_id == edited["revision_id"])
                        .values(model="tampered")
                    )
                )
            for kind, value in [("channel", {"driver": "qq"}), ("mcp", {}), ("preset", {"content": "saved persona"})]:
                row = await service.create(agent, kind, kind, False, value)
                assert (await service.diagnose(agent, row["id"]))["available"] is False
            with pytest.raises(BusinessError, match="credentials"):
                await service.create(agent, "model", "inline secret", True, {"api_key": "secret-canary"})
            with pytest.raises(BusinessError, match="credentials"):
                await service.create(agent, "mcp", "inline argv", True, {"arguments": ["--token=secret-canary"]})
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_token_capacity_compresses_whole_turns_with_output_reservation(tmp_path):
    from omni_test_support.v2.context import Model, prepare, seed

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            await db.write(
                lambda u: model_uow(
                    u, agent, context_tokens=2048, output_tokens=128, summary_tokens=64, margin_tokens=256
                )
            )
            current, _ = await db.write(lambda u: seed(u, agent, 30, "x" * 600))
            model = Model()
            prepared = await prepare(db, agent, current, model)
            assert 1 <= len(model.requests) <= 3
            assert prepared["omitted_count"] > 0 and prepared["mode"] == "truncated"
            settings = await db.read(lambda u: u.run_settings(agent, current["run_id"]))
            calls = await db.read(lambda u: u.query("calls", agent))
            assert len(calls) == len(model.requests)
            for call in calls:
                assert call["output_token_limit"] == 64
                assert call["input_token_estimate"] + 64 + settings["margin_tokens"] <= settings["context_tokens"]
            estimate = await ConservativeTokenCounter().count(prepared["request"], (), settings)
            assert estimate.tokens + 128 + 256 <= 2048
            assert prepared["messages"][-1]["content"] == "current request"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["identity", "quality", "supports_error", "timeout"])
def test_invalid_counter_fails_closed_and_releases_only_unsent_reservation(tmp_path, failure):
    class Counter:
        def supports(self, *args):
            if failure == "supports_error":
                raise RuntimeError("private counter failure canary")
            return True

        async def count(self, *args):
            if failure == "timeout":
                await asyncio.Event().wait()
            return TokenEstimate(
                10,
                "upper_bound" if failure == "quality" else "exact",
                "wrong-v1" if failure == "identity" else "exact-v1",
            )

    class Model:
        async def complete(self, *args):
            pytest.fail("An invalid counter must prevent transmission")

    async def scenario():
        from omni_test_support.v2.context import Observer

        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            row = await db.write(lambda u: model_uow(u, agent, counter_mode="required", counter_ref="exact-v1"))
            turn = await db.write(lambda u: u.accept_turn(agent, None, "one", "input", {}))
            await db.write(lambda u: u.prepare_context(agent, turn["run_id"]))
            settings = await db.read(lambda u: u.run_settings(agent, turn["run_id"]))
            with pytest.raises(BusinessError) as error:
                await ModelCalls(db, Model(), Observer(), Counter()).invoke(
                    agent, turn["run_id"], [{"role": "user", "content": "input"}], (), settings
                )
            assert error.value.code == "token_counter_unavailable"
            call = (await db.read(lambda u: u.query("calls", agent)))[0]
            assert call["state"] == "not_sent" and call["cost_micro"] == 0
            if failure == "supports_error":
                assert not (await ConfigurationService(db, Counter()).diagnose(agent, row["id"]))["available"]

    asyncio.run(scenario())


def test_vendor_adapter_receives_explicit_output_cap_without_network(monkeypatch):
    import sys
    from types import SimpleNamespace

    from omni.adapters.diagnostics import Redactor
    from omni.adapters.models import LiteLLMModel

    captured = {}

    class Response:
        def model_dump(self):
            return {
                "choices": [{"message": {"content": "answer"}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 7},
            }

    async def complete(**kwargs):
        captured.update(kwargs)
        return Response()

    fake = SimpleNamespace(acompletion=complete, ModelResponse=Response, completion_cost=lambda **kwargs: 0)
    monkeypatch.setitem(sys.modules, "litellm", fake)
    # The vendor module is replaced before opening the adapter's explicit offline gate.
    monkeypatch.delenv("OMNI_TEST_OFFLINE", raising=False)
    settings = {
        "model": "fake-model",
        "base_url": "https://model.example.test/v1",
        "credential_ref": "",
        "output_tokens": 77,
    }
    reply = asyncio.run(LiteLLMModel(None, Redactor()).complete([{"role": "user", "content": "test"}], (), settings))
    assert reply.output_tokens == 7
    assert captured["max_tokens"] == 77 and captured["api_base"] == settings["base_url"]
    assert captured["num_retries"] == 0


def test_import_only_previews_and_preset_application_does_not_save_settings(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            service = ConfigurationService(db, ConservativeTokenCounter())
            initial = await db.read(lambda u: u.settings(agent))
            document = {
                "schema": 1,
                "kind": "preset",
                "name": "Imported",
                "enabled": True,
                "value": {"content": "draft persona"},
            }
            assert service.preview(document) == document
            assert (await service.page(agent, "preset"))["items"] == []
            preset = await service.create(agent, **{k: document[k] for k in ("kind", "name", "enabled", "value")})
            applied = await service.preset(agent, preset["id"])
            assert applied["value"]["content"] == "draft persona"
            assert await db.read(lambda u: u.settings(agent)) == initial
            await service.archive(agent, preset["id"], 1)
            assert (await service.history(agent, preset["id"]))["items"][0]["value"]["content"] == "draft persona"
            with pytest.raises(BusinessError) as error:
                await service.get(agent, preset["id"])
            assert error.value.status == 410
            with pytest.raises(BusinessError, match="another active model"):
                await service.archive(agent, initial["value"]["model_profile_id"], 1)

    asyncio.run(scenario())


def test_configuration_graph_rejects_cross_agent_links_and_rewriting_run_capture(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            other = (await db.write(lambda u: u.create_agent("Other")))["id"]
            other_cfg = await db.read(lambda u: u.settings(other))
            other_model = await db.read(lambda u: u.configuration_get(other, other_cfg["value"]["model_profile_id"]))
            turn = await db.write(lambda u: u.accept_turn(agent, None, "one", "input", {}))
            own = await db.write(lambda u: model_uow(u, agent, "future"))
            for patch in (
                {"model_revision_id": other_model["revision_id"]},
                {"model_revision_id": own["revision_id"]},
                {"policy_epoch": 999},
            ):
                with pytest.raises(BusinessError, match="constraint"):
                    await db.write(
                        lambda u: u.connection.execute(
                            update(s.runs).where(s.runs.c.id == turn["run_id"]).values(**patch)
                        )
                    )
            with pytest.raises(BusinessError, match="constraint"):
                await db.write(
                    lambda u: u.connection.execute(
                        update(s.settings)
                        .where(s.settings.c.agent_id == agent)
                        .values(model_profile_id=other_model["id"])
                    )
                )
            assert (await db.read(lambda u: u.run_settings(agent, turn["run_id"])))["model"] == ""
            service = ConfigurationService(db, ConservativeTokenCounter())
            await service.create(agent, "preset", "ＡＢＣ", False, {})
            with pytest.raises(BusinessError, match="constraint"):
                await service.create(agent, "preset", "abc", False, {})
            assert (await db.integrity())["foreign_keys"] == 0

    asyncio.run(scenario())


def test_run_uses_immutable_configuration_and_reenable_does_not_restore_old_authority(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            row = await db.write(lambda u: model_uow(u, agent, "before"))
            receipt = await db.write(lambda u: u.accept_turn(agent, None, "run", "input", {}))
            before = await db.read(lambda u: u.run_settings(agent, receipt["run_id"]))
            await db.write(
                lambda u: u.configuration_put(
                    agent,
                    row["id"],
                    row["name"],
                    True,
                    {**row["value"], "model": "after", "output_tokens": 512},
                    row["revision"],
                )
            )
            assert (await db.read(lambda u: u.run_settings(agent, receipt["run_id"])))["model"] == "before"
            assert before["output_tokens"] == 2048
            await db.read(lambda u: u.assert_model_authorized(agent, receipt["run_id"]))
            row = await db.read(lambda u: u.configuration_get(agent, row["id"]))
            row = await db.write(
                lambda u: u.configuration_put(agent, row["id"], row["name"], False, row["value"], row["revision"])
            )
            await db.write(
                lambda u: u.configuration_put(agent, row["id"], row["name"], True, row["value"], row["revision"])
            )
            with pytest.raises(BusinessError) as error:
                await db.read(lambda u: u.assert_model_authorized(agent, receipt["run_id"]))
            assert error.value.code == "model_configuration_revoked"
            fresh = await db.write(lambda u: u.accept_turn(agent, None, "fresh", "new", {}))
            assert (await db.read(lambda u: u.run_settings(agent, fresh["run_id"])))["model"] == "after"
            columns = await db.read(
                lambda u: set(row[1] for row in u.connection.exec_driver_sql("PRAGMA table_info(runs)"))
            )
            assert "config_snapshot" not in columns

    asyncio.run(scenario())


def test_ordinary_settings_edit_preserves_approval_but_permission_change_revokes_it(tmp_path):
    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            turn = await db.write(lambda u: u.accept_turn(agent, None, "one", "input", {}))
            await db.write(lambda u: u.transition(agent, turn["run_id"], "running"))
            spec = ToolSpec("write", "test", {}, True)
            first = await db.write(lambda u: u.prepare_action(agent, turn["run_id"], spec, {}, 1))
            approval = (await db.read(lambda u: u.pending_approvals(agent)))[0]
            cfg = await db.read(lambda u: u.settings(agent))
            updated = await db.write(
                lambda u: u.settings_put(agent, {**cfg["value"], "persona": "new persona"}, cfg["revision"])
            )
            assert updated["policy_revision"] == 1 and updated["revision"] == 2
            await db.write(lambda u: u.resolve_approval(agent, approval["id"], True, 1))
            await db.write(lambda u: u.authorize_action(agent, first["id"]))
            changed = await db.write(
                lambda u: u.settings_put(agent, {**updated["value"], "disabled_tools": ["write"]}, 2)
            )
            assert changed["policy_revision"] == 2
            with pytest.raises(BusinessError, match="changed"):
                await db.write(lambda u: u.prepare_action(agent, turn["run_id"], spec, {}, 1))

    asyncio.run(scenario())


async def wait_run(app, agent, identity):
    async with asyncio.timeout(10):
        while True:
            row = await app.get_run(agent, identity)
            if row["state"] in {"succeeded", "failed", "cancelled"}:
                return row
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("case", ["utf8_overflow", "counter_required", "output_overflow", "exact"])
def test_model_capacity_and_output_limits_are_enforced_before_success(tmp_path, case):
    class Counter:
        def supports(self, mode, reference, model):
            return mode == "required" and reference == "exact-test-v1"

        async def count(self, messages, tools, settings):
            return TokenEstimate(50, "exact", "exact-test-v1")

    class Model:
        calls = 0

        async def complete(self, messages, tools, settings):
            self.calls += 1
            assert settings["output_tokens"] == 64
            return ModelReply(
                "answer", input_tokens=50, output_tokens=65 if case == "output_overflow" else 1, cost_micro=7
            )

    async def scenario():
        model = Model()
        instance = await open_instance(
            tmp_path, model=model, capabilities=(), token_counter=Counter() if case == "exact" else None
        )
        try:
            app = instance.application
            agent = (await app.query("agents", ""))[0]["id"]
            options = {"context_tokens": 1024, "output_tokens": 64, "summary_tokens": 64, "margin_tokens": 128}
            if case in {"counter_required", "exact"}:
                options.update(counter_mode="required", counter_ref="exact-test-v1")
            await model_instance(instance, agent, **options)
            turn = await app.submit_turn(agent, None, "case", "中" * 400 if case == "utf8_overflow" else "short")
            result = await wait_run(app, agent, turn["run_id"])
            codes = {
                "utf8_overflow": "token_budget_exceeded",
                "counter_required": "token_counter_unavailable",
                "output_overflow": "model_output_limit_exceeded",
            }
            if case == "exact":
                assert result["state"] == "succeeded"
            else:
                assert result["state"] == "failed" and result["error_code"] == codes[case]
            assert model.calls == (1 if case in {"exact", "output_overflow"} else 0)
            calls = await app.query("calls", agent)
            if model.calls:
                assert calls[0]["state"] == "settled" and calls[0]["cost_micro"] == 7
                assert calls[0]["output_token_limit"] == 64
            if case == "exact":
                assert calls[0]["input_token_estimate"] == 50 and calls[0]["counting_method"] == "exact"
            if case == "counter_required":
                cfg = await app.settings(agent)
                assert not (await instance.configurations.diagnose(agent, cfg["value"]["model_profile_id"]))[
                    "available"
                ]
        finally:
            await instance.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("erase", [False, True])
def test_token_count_race_rechecks_sources_and_model_authority_before_send(tmp_path, erase):
    class Observer:
        def context(self, **fields):
            return nullcontext()

        def span(self, name):
            return nullcontext()

    class Counter:
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()

        def supports(self, *args):
            return True

        async def count(self, *args):
            self.started.set()
            await self.release.wait()
            return TokenEstimate(20, "upper_bound", "utf8-envelope-v1")

    class Model:
        calls = 0

        async def complete(self, *args):
            self.calls += 1
            return ModelReply("unexpected", cost_micro=0)

    async def scenario():
        async with opened(tmp_path, Clock()) as (_, db, _, agent):
            row = await db.write(lambda u: model_uow(u, agent))
            turn = await db.write(lambda u: u.accept_turn(agent, None, "one", "input", {}))
            await db.write(lambda u: u.prepare_context(agent, turn["run_id"]))
            cfg = await db.read(lambda u: u.run_settings(agent, turn["run_id"]))
            model, counter = Model(), Counter()
            calls = ModelCalls(db, model, Observer(), counter)
            task = asyncio.create_task(
                calls.invoke(agent, turn["run_id"], [{"role": "user", "content": "input"}], (), cfg)
            )
            async with asyncio.timeout(10):
                await counter.started.wait()
            if erase:
                message = (await db.read(lambda u: u.messages(agent, turn["session_id"])))[0]
                await db.write(lambda u: u.erase_message(agent, message["id"]))
            else:
                await db.write(
                    lambda u: u.configuration_put(agent, row["id"], row["name"], False, row["value"], row["revision"])
                )
            counter.release.set()
            with pytest.raises(BusinessError):
                await task
            assert model.calls == 0
            ledger = (await db.read(lambda u: u.query("calls", agent)))[0]
            assert ledger["state"] == "not_sent" and ledger["cost_micro"] == 0

    asyncio.run(scenario())


def _envelope(ref: str) -> dict[str, object]:
    return {"counter_mode": "utf8_upper_bound", "counter_ref": ref, "model": "fixture"}


def test_utf8_envelope_v2_lets_a_declared_32k_config_finish_normal_tool_rounds():
    """R4: declared capacity is tokens; v1 counted bytes so a 29 KB prompt could not fit 32K."""

    async def scenario():
        messages = ({"role": "user", "content": "x" * 29_808},)
        v1 = await ConservativeTokenCounter().count(messages, (), _envelope("utf8-envelope-v1"))  # type: ignore[arg-type]
        v2 = await ConservativeTokenCounter().count(messages, (), _envelope("utf8-envelope-v2"))  # type: ignore[arg-type]
        assert v1.tokens == len(serialized_request(messages, ())) + 256 and v1.counter_id == "utf8-envelope-v1"
        assert v2.counter_id == "utf8-envelope-v2" and v2.tokens < 12_000
        assert v2.tokens + 2048 + 1024 <= 32768
        assert v1.tokens + 2048 + 1024 > 32768

    asyncio.run(scenario())


def test_utf8_envelope_v2_keeps_cjk_conservative_and_still_fails_closed():
    async def scenario():
        cjk = ({"role": "user", "content": "中" * 1000},)
        estimate = await ConservativeTokenCounter().count(cjk, (), _envelope("utf8-envelope-v2"))  # type: ignore[arg-type]
        assert estimate.tokens >= 2000
        oversized = ({"role": "user", "content": "x" * 200_000},)
        huge = await ConservativeTokenCounter().count(oversized, (), _envelope("utf8-envelope-v2"))  # type: ignore[arg-type]
        assert not ModelCalls.fits(huge, {"context_tokens": 32768, "output_tokens": 2048, "margin_tokens": 1024})  # type: ignore[arg-type]

    asyncio.run(scenario())
