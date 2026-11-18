"""Foreground commands require separate consent and keep the shared approval/unknown contracts."""

from omni_test_support.v2.foreground import (
    KINDS as KINDS,
)
from omni_test_support.v2.foreground import (
    POLICY as POLICY,
)
from omni_test_support.v2.foreground import (
    VERSION as VERSION,
)
from omni_test_support.v2.foreground import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.foreground import (
    ForegroundBackend as ForegroundBackend,
)
from omni_test_support.v2.foreground import (
    MacForegroundBackend as MacForegroundBackend,
)
from omni_test_support.v2.foreground import (
    actions as actions,
)
from omni_test_support.v2.foreground import (
    asyncio as asyncio,
)
from omni_test_support.v2.foreground import (
    hashlib as hashlib,
)
from omni_test_support.v2.foreground import (
    input_value as input_value,
)
from omni_test_support.v2.foreground import (
    parse_request as parse_request,
)
from omni_test_support.v2.foreground import (
    parse_response as parse_response,
)
from omni_test_support.v2.foreground import (
    permit as permit,
)
from omni_test_support.v2.foreground import (
    pytest as pytest,
)
from omni_test_support.v2.foreground import (
    ready as ready,
)
from omni_test_support.v2.foreground import (
    uuid as uuid,
)


@pytest.mark.parametrize("backend_class", [ForegroundBackend, MacForegroundBackend])
def test_explicit_foreground_consent_binds_run_action_and_boot_scoped_grant(tmp_path, monkeypatch, backend_class):
    monkeypatch.setattr(actions, "ActionsBackend", backend_class)

    async def scenario():
        async with actions.prepared(tmp_path) as (instance, backend, agent, request):
            request.update(kind="type_text", text="owned foreground text")
            with pytest.raises(BusinessError):
                await instance.desktop_actions.submit(agent, request)
            request["takeover"] = True
            receipt, approval = await actions.queued(instance, agent, request)
            assert approval["arguments"]["takeover"] is True and approval["arguments"]["policy"] == POLICY
            assert not backend.foreground_calls and not backend.action_calls
            backend.outcome = {
                "status": "unknown",
                "effect": "unknown",
                "error_code": "desktop_postcondition_unverified",
                "verification": "not_verified",
            }
            await actions.approve(instance, agent, approval)
            run = await actions.wait_state(instance.application, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "action_outcome_unknown"
            wire = backend.foreground_calls[0]
            assert wire["run_id"] == receipt["run_id"] and wire["operation_id"] == approval["action_id"]
            assert wire["grant_id"] == approval["arguments"]["binding_id"] and wire["policy"] == POLICY
            assert (await instance.desktop_actions.submit(agent, request))["duplicate"]
            assert len(backend.foreground_calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("backend_class", [ForegroundBackend, MacForegroundBackend])
def test_view_revoked_during_focus_wait_prevents_native_permit(tmp_path, monkeypatch, backend_class):
    monkeypatch.setattr(actions, "ActionsBackend", backend_class)

    async def scenario():
        async with actions.prepared(tmp_path) as (instance, backend, agent, request):
            request.update(kind="click", text="", takeover=True)
            backend.action_gate.clear()
            receipt, approval = await actions.queued(instance, agent, request)
            await actions.approve(instance, agent, approval)
            await asyncio.wait_for(backend.action_entered.wait(), 5)
            await instance.desktop.forget(agent, request["view_id"])
            backend.action_gate.set()
            run = await actions.wait_state(instance.application, agent, receipt["run_id"], {"failed"})
            assert run["error_code"] == "desktop_view_changed"
            assert not backend.foreground_calls

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,value",
    [
        ("type_text", ""),
        ("type_text", "x" * 129),
        ("type_text", "\ud800"),
        ("type_text", "\n"),
        ("press_key", "Ctrl+Enter"),
        ("press_key", "F12"),
        ("click", "1,2"),
    ],
)
def test_input_contract_rejects_unbounded_or_unapproved_input(kind, value):
    with pytest.raises(BusinessError):
        input_value(kind, value)


def test_readiness_and_permit_are_bound_to_all_authority_ids():
    from omni_test_support.v2.v2_desktop_helpers import target_identity

    request = parse_request(
        {
            "version": VERSION,
            "request_id": str(uuid.uuid4()),
            "operation": "foreground",
            "target": target_identity(),
            "limit": 1,
            "action": {
                "kind": "click",
                "text": "",
                "takeover": True,
                "policy": POLICY,
                "node": {"root": [1], "runtime": [2], "role": "ControlType.Button", "name_sha256": "a" * 64},
                **{k: str(uuid.uuid4()) for k in ("grant_id", "run_id", "operation_id")},
            },
        }
    )
    assert ready(request)["phase"] == "ready" and permit(request)["phase"] == "permit"
    assert {k: v for k, v in ready(request).items() if k != "phase"} == {
        k: v for k, v in permit(request).items() if k != "phase"
    }
    response = {
        "version": VERSION,
        "request_id": request["request_id"],
        "operation": "foreground",
        "ok": True,
        "result": {
            "status": "unknown",
            "effect": "unknown",
            "verification": "not_verified",
            "error_code": "desktop_postcondition_unverified",
        },
        "error_code": None,
    }
    assert parse_response(response, request)["result"]["effect"] == "unknown"
