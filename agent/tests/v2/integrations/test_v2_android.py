"""Real V2 SQLite, managed Python worker and an isolated fake ADB server; no actual devices."""

from __future__ import annotations

from omni_test_support.v2.android import (
    BOOT as BOOT,
)
from omni_test_support.v2.android import (
    SERIAL as SERIAL,
)
from omni_test_support.v2.android import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.android import (
    FakeAdb as FakeAdb,
)
from omni_test_support.v2.android import (
    Image as Image,
)
from omni_test_support.v2.android import (
    action_request as action_request,
)
from omni_test_support.v2.android import (
    approve as approve,
)
from omni_test_support.v2.android import (
    asynccontextmanager as asynccontextmanager,
)
from omni_test_support.v2.android import (
    asyncio as asyncio,
)
from omni_test_support.v2.android import (
    close_until_quiescent as close_until_quiescent,
)
from omni_test_support.v2.android import (
    connection as connection,
)
from omni_test_support.v2.android import (
    escape as escape,
)
from omni_test_support.v2.android import (
    fake_server as fake_server,
)
from omni_test_support.v2.android import (
    hierarchy as hierarchy,
)
from omni_test_support.v2.android import (
    io as io,
)
from omni_test_support.v2.android import (
    json as json,
)
from omni_test_support.v2.android import (
    open_instance as open_instance,
)
from omni_test_support.v2.android import (
    prepared as prepared,
)
from omni_test_support.v2.android import (
    pytest as pytest,
)
from omni_test_support.v2.android import (
    request_for as request_for,
)
from omni_test_support.v2.android import (
    review_request as review_request,
)
from omni_test_support.v2.android import (
    s as s,
)
from omni_test_support.v2.android import (
    select as select,
)
from omni_test_support.v2.android import (
    struct as struct,
)
from omni_test_support.v2.android import (
    update as update,
)
from omni_test_support.v2.android import (
    uuid as uuid,
)
from omni_test_support.v2.android import (
    wait_state as wait_state,
)


def test_real_managed_worker_observe_capture_and_approved_ascii_use_single_ledger(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            request, view = await request_for(instance, agent, session, grant)
            assert "private-password" not in json.dumps(view)
            assert not any("path" in n or "proof" in n for n in view["nodes"])
            capture = await instance.android.capture(agent, grant["id"], session)
            assert capture["method"] == "adb_screencap" and capture["width"] == 32
            row, content = await instance.objects.get(agent, capture["object_id"])
            assert row["references"] == 1 and content == fake.png
            assert not list((instance.paths.control / "android-captures").glob("*.png"))
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            assert not fake.writes
            await approve(instance, agent, receipt)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "succeeded", run
            assert fake.text == "hello world" and len(fake.writes) == 1
            replay = await instance.android.submit(agent, request)
            assert replay["duplicate"] and replay["run_id"] == receipt["run_id"]
            command = await instance._database.read(lambda u: u.owner_command(agent, receipt["run_id"]))
            assert command["arguments"] is None and command["action_id"]
            assert await instance._database.read(lambda u: u.connection.execute(select(s.model_calls)).first()) is None
            action = await instance._database.read(lambda u: u.action(agent, command["action_id"]))
            assert action["result"]["verification"] == "text_appended"
            assert instance.android.health()["leases"] == 0
            logs = "".join(p.read_text(encoding="utf-8") for p in instance.paths.logs.glob("*.jsonl"))
            assert "private-password" not in logs and "hello world" not in logs

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["boot", "user", "transport", "tree", "revoked", "locked", "dismissed", "config"])
def test_identity_or_authority_changes_block_approved_device_input(tmp_path, fault):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, config, session, grant):
            request, _ = await request_for(instance, agent, session, grant)
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval"})
            if fault == "boot":
                fake.boot = str(uuid.uuid4())
            elif fault == "user":
                fake.user = 10
            elif fault == "transport":
                fake.transport = 8
            elif fault == "tree":
                fake.text = "changed"
            elif fault == "locked":
                fake.locked = True
            elif fault == "revoked":
                await instance.android.revoke(agent, grant["id"], grant["revision"])
            elif fault == "dismissed":
                await instance.android.dismiss(agent, request["observation_id"])
            elif fault == "config":
                await instance.configurations.save(
                    agent, config["id"], config["name"], False, config["value"], config["revision"]
                )
            await approve(instance, agent, receipt)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == "failed" and not fake.writes

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,drop,expected", [("toggle", False, "succeeded"), ("tap", False, "failed"), ("append_ascii", True, "failed")]
)
def test_verified_toggle_generic_tap_and_lost_response_have_honest_outcomes(tmp_path, kind, drop, expected):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            fake.drop_after_input = drop
            request, _ = await request_for(instance, agent, session, grant, kind)
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            await approve(instance, agent, receipt)
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert run["state"] == expected and len(fake.writes) == 1
            if expected == "failed":
                action = await instance._database.read(
                    lambda u: u.action(agent, u.owner_command(agent, receipt["run_id"])["action_id"])
                )
                assert action["state"] == "unknown" and action["effect"] == "unknown"
                fake.drop_after_input = False
                followup, _ = await request_for(instance, agent, session, grant, "toggle")
                next_receipt = await instance.android.submit(agent, followup)
                instance.application.notify()
                await approve(instance, agent, next_receipt)
                second = await wait_state(instance.application, agent, next_receipt["run_id"], {"succeeded", "failed"})
                assert second["state"] == "failed" and len(fake.writes) == 1
                async with asyncio.timeout(5):
                    while instance.application.health()["active_runs"]:
                        await asyncio.sleep(0.01)
                detail = await instance.action_reviews.get(agent, action["id"])
                assert detail["eligible"], detail
                reviewed = await instance.action_reviews.review(
                    agent,
                    action["id"],
                    review_request(detail, "applied", "Owner inspected the isolated fake device input record"),
                )
                assert reviewed["resolved"] and reviewed["state"] == "unknown"
                final_request, _ = await request_for(instance, agent, session, grant, "toggle")
                final_receipt = await instance.android.submit(agent, final_request)
                instance.application.notify()
                await approve(instance, agent, final_receipt)
                final_run = await wait_state(
                    instance.application, agent, final_receipt["run_id"], {"succeeded", "failed"}
                )
                assert final_run["state"] == "succeeded" and len(fake.writes) == 2

    asyncio.run(scenario())


def test_cross_agent_views_grants_and_immutable_device_binding(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, _, agent, _, session, grant):
            request, _ = await request_for(instance, agent, session, grant)
            other = await instance._database.write(lambda u: u.create_agent("Other"))
            with pytest.raises(BusinessError):
                await instance.android.observe(other["id"], grant["id"])
            with pytest.raises(BusinessError):
                await instance.android.submit(other["id"], request)
            with pytest.raises(BusinessError, match="Operation violates a data constraint"):
                await instance._database.write(
                    lambda u: u.connection.execute(
                        update(s.android_grants)
                        .where(s.android_grants.c.id == grant["id"])
                        .values(device_sha256="a" * 64)
                    )
                )
            assert await instance._database.read(lambda u: u.android_authorize(agent, grant["id"]))

    asyncio.run(scenario())


def test_bounded_hierarchy_and_requests_reject_password_text_entities_and_shell_injection():
    fake = FakeAdb()
    parsed = hierarchy(fake.xml(), "org.fixture")
    assert len(parsed["nodes"]) == 3
    assert "private-password" not in json.dumps(parsed)
    with pytest.raises(ValueError):
        hierarchy(b'<?xml version="1.0"?><!DOCTYPE x><hierarchy></hierarchy>', "org.fixture")
    with pytest.raises(BusinessError):
        connection({"driver": "android", "endpoint": "http://example.com:5037", "account": SERIAL})
    request = {
        "consent": True,
        **{k: str(uuid.uuid4()) for k in ["grant_id", "observation_id", "node_id", "session_id", "request_id"]},
        "kind": "append_ascii",
    }
    for text in ["中文", "'; touch bad", "100%", "newline\n", "a" * 257]:
        with pytest.raises(BusinessError):
            action_request({**request, "text": text})


def test_cancelled_waiter_and_revocation_keep_actual_observation_owned(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, _, grant):
            fake.dump_entered.clear()
            fake.allow_dump.clear()
            waiter = asyncio.create_task(instance.android.observe(agent, grant["id"]))
            await asyncio.wait_for(fake.dump_entered.wait(), 5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert instance.android.health()["pending"] == 1
            assert not instance.android.quiescent()
            await instance.android.revoke(agent, grant["id"], grant["revision"])
            fake.allow_dump.set()
            for _ in range(100):
                if instance.android.quiescent():
                    break
                await asyncio.sleep(0.02)
            assert instance.android.quiescent()
            assert instance.android.health()["observations"] == 0
            assert not fake.writes

    asyncio.run(scenario())


def test_capture_revocation_after_frame_is_taken_publishes_no_object(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            fake.allow_capture.clear()
            task = asyncio.create_task(instance.android.capture(agent, grant["id"], session))
            await asyncio.wait_for(fake.capture_entered.wait(), 5)
            await instance.android.revoke(agent, grant["id"], grant["revision"])
            fake.allow_capture.set()
            with pytest.raises(BusinessError) as caught:
                await task
            assert caught.value.code in {
                "android_authority_invalid",
                "android_grant_changed",
                "android_grant_revoked",
            }
            assert not list((instance.paths.control / "android-captures").glob("*.png"))
            rows = await instance._database.read(lambda u: u.connection.execute(select(s.session_captures)).all())
            assert rows == []
            assert instance.android.quiescent()
            with pytest.raises(BusinessError) as denied:
                await instance.android.capture(agent, grant["id"], session)
            assert denied.value.code in {"android_authority_invalid", "android_grant_revoked"}

    asyncio.run(scenario())


def test_cancelled_capture_waiter_keeps_real_capture_owned_and_publishes_once(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            fake.allow_capture.clear()
            waiter = asyncio.create_task(instance.android.capture(agent, grant["id"], session))
            await asyncio.wait_for(fake.capture_entered.wait(), 5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert instance.android.health()["pending"] == 1
            assert not instance.android.quiescent()
            fake.allow_capture.set()
            for _ in range(100):
                if instance.android.quiescent():
                    break
                await asyncio.sleep(0.02)
            assert instance.android.quiescent()
            row = await instance._database.read(
                lambda u: u.connection.execute(select(s.session_captures)).mappings().one()
            )
            assert row["content_epoch"] == 1 and row["method"] == "adb_screencap"
            stored, content = await instance.objects.get(agent, row["object_id"])
            assert stored["references"] == 1 and content == fake.png
            assert not list((instance.paths.control / "android-captures").glob("*.png"))

    asyncio.run(scenario())


def test_revoked_grant_after_input_dispatch_does_not_retract_or_replay(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            request, _ = await request_for(instance, agent, session, grant)
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            await wait_state(instance.application, agent, receipt["run_id"], {"waiting_approval"})
            fake.allow_input.clear()
            await approve(instance, agent, receipt)
            await asyncio.wait_for(fake.input_entered.wait(), 5)
            await instance.android.revoke(agent, grant["id"], grant["revision"])
            fake.allow_input.set()
            run = await wait_state(instance.application, agent, receipt["run_id"], {"succeeded", "failed"})
            assert fake.text == "hello world" and len(fake.writes) == 1
            assert run["state"] == "succeeded"
            command = await instance._database.read(lambda u: u.owner_command(agent, receipt["run_id"]))
            action = await instance._database.read(lambda u: u.action(agent, command["action_id"]))
            assert action["effect"] == "applied"
            with pytest.raises(BusinessError):
                await request_for(instance, agent, session, grant)
            assert len(fake.writes) == 1

    asyncio.run(scenario())


def test_capture_bad_png_fails_cleanly_and_next_capture_succeeds(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            valid = fake.png
            fake.png = b"not-a-png"
            with pytest.raises(BusinessError) as caught:
                await instance.android.capture(agent, grant["id"], session)
            assert caught.value.code == "android_capture_failed"
            assert not list((instance.paths.control / "android-captures").glob("*.png"))
            rows = await instance._database.read(lambda u: u.connection.execute(select(s.session_captures)).all())
            assert rows == []
            fake.png = valid
            capture = await instance.android.capture(agent, grant["id"], session)
            assert capture["method"] == "adb_screencap"
            stored, content = await instance.objects.get(agent, capture["object_id"])
            assert stored["references"] == 1 and content == valid
            assert not list((instance.paths.control / "android-captures").glob("*.png"))

    asyncio.run(scenario())


def test_cancelled_run_and_shutdown_retain_instance_until_worker_is_reclaimed(tmp_path):
    async def scenario():
        async with prepared(tmp_path) as (instance, fake, agent, _, session, grant):
            request, _ = await request_for(instance, agent, session, grant)
            receipt = await instance.android.submit(agent, request)
            instance.application.notify()
            fake.dump_entered.clear()
            fake.allow_dump.clear()
            await approve(instance, agent, receipt)
            await asyncio.wait_for(fake.dump_entered.wait(), 5)
            await instance.application.cancel(agent, receipt["run_id"])
            report = await instance.close(0.02)
            assert not report["complete"]
            assert instance.android.health()["pending"] >= 1
            fake.allow_dump.set()
            report = await asyncio.wait_for(close_until_quiescent(instance.close, 10), 15)
            assert report["complete"] and not fake.writes

    asyncio.run(scenario())
