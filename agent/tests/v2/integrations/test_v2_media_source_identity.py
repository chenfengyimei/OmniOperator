"""Registered video identity gates subtitle/stream dispatch before new media can be published."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from omni_test_support.v2.bilibili_gateway import CONFIG, Secrets

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import BusinessError

BV = "BV1xx411c7mD"
SOURCE = {"bvid": BV, "aid": "123", "cid": "789"}
URL = "https://cdn.bilivideo.com/owned.m4s"


class Gateway(BilibiliGateway):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads = []

    async def _subtitle(self, client, nav, video):
        self.payloads.append(("subtitle", dict(video)))
        return {"language": "zh", "text": "captured source subtitle"}

    async def _streams(self, client, video):
        self.payloads.append(("streams", dict(video)))
        return {"audio": {"url": URL}, "video": {"url": URL}}


def fixture():
    state = {"bvid": BV, "aid": 123, "cid": 789, "title": "Fixture", "duration": 120}
    calls, gate = [], {"events": None}

    async def handle(request):
        calls.append(request)
        if request.url.path.endswith("/nav"):
            data = {"isLogin": True, "mid": 456, "uname": "fixture"}
        elif request.url.path.endswith("/view"):
            if gate["events"] is not None:
                entered, release = gate["events"]
                entered.set()
                await release.wait()
            data = dict(state)
        elif request.url.host == "cdn.bilivideo.com":
            return httpx.Response(200, content=b"owned media bytes")
        else:
            raise AssertionError("Unexpected endpoint")
        return httpx.Response(200, json={"code": 0, "data": data})

    async def no_write(_):
        raise AssertionError("No publications")

    gateway = Gateway(
        Secrets(),
        Redactor(),
        no_write,
        client_factory=lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options),
    )
    return gateway, state, calls, gate


@pytest.mark.parametrize("operation", ["subtitle", "streams"])
@pytest.mark.parametrize("field,value", [("cid", 790), ("aid", 999), ("bvid", "BV2xx411c7mD")])
def test_changed_content_identity_is_rejected_before_payload_dispatch(operation, field, value):
    async def scenario():
        gateway, state, calls, _ = fixture()
        state[field] = value
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, operation, SOURCE)
            assert caught.value.code == "bilibili_source_changed"
            assert not gateway.payloads and len(calls) == 2 and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "streams"])
def test_matching_source_passes_captured_cid_and_preserves_result_shape(operation):
    async def scenario():
        gateway, _, _, _ = fixture()
        try:
            result = await gateway.read_source(CONFIG, operation, SOURCE)
            assert gateway.payloads == [(operation, {"bvid": BV, "cid": "789"})]
            assert result == (
                {"language": "zh", "text": "captured source subtitle"}
                if operation == "subtitle"
                else {"audio": {"url": URL}, "video": {"url": URL}}
            )
            assert SOURCE == {"bvid": BV, "aid": "123", "cid": "789"} and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_ordinary_owner_video_read_still_returns_current_identity():
    async def scenario():
        gateway, state, _, _ = fixture()
        state["cid"] = 790
        try:
            result = await gateway.read(CONFIG, "video", BV)
            assert result["cid"] == "790" and not gateway.payloads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "streams"])
def test_caller_mutation_during_io_cannot_rebind_the_captured_identity(operation):
    async def scenario():
        gateway, state, _, gate = fixture()
        entered, release = asyncio.Event(), asyncio.Event()
        gate["events"] = entered, release
        source, caller = dict(SOURCE), None
        try:
            caller = asyncio.create_task(gateway.read_source(CONFIG, operation, source))
            await asyncio.wait_for(entered.wait(), 8)
            source["cid"], state["cid"] = "790", 790
            release.set()
            with pytest.raises(BusinessError) as caught:
                await caller
            assert caught.value.code == "bilibili_source_changed" and not gateway.payloads
        finally:
            release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("bvid", None), ("aid", None), ("cid", None), ("operation", "video")])
def test_bad_source_contract_is_rejected_without_io(field, value):
    async def scenario():
        gateway, _, calls, _ = fixture()
        source, operation = dict(SOURCE), "streams"
        if field == "operation":
            operation = value
        else:
            source[field] = value
        try:
            with pytest.raises(BusinessError) as caught:
                await gateway.read_source(CONFIG, operation, source)
            assert caught.value.status == 422 and not calls and not gateway._reads
        finally:
            assert await gateway.close()

    asyncio.run(scenario())


def test_closed_source_read_does_not_dispatch():
    async def scenario():
        gateway, _, calls, _ = fixture()
        assert await gateway.close()
        with pytest.raises(BusinessError) as caught:
            await gateway.read_source(CONFIG, "streams", SOURCE)
        assert caught.value.code == "bilibili_busy" and not calls

    asyncio.run(scenario())


def test_cancelled_source_read_retires_the_actual_gateway_owner():
    async def scenario():
        gateway, _, _, gate = fixture()
        entered, release = asyncio.Event(), asyncio.Event()
        gate["events"] = entered, release
        caller = None
        try:
            caller = asyncio.create_task(gateway.read_source(CONFIG, "streams", SOURCE))
            await asyncio.wait_for(entered.wait(), 8)
            assert not await gateway.close(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert not gateway._reads and not gateway.payloads
        finally:
            release.set()
            if caller is not None:
                await asyncio.gather(caller, return_exceptions=True)
            assert await gateway.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "transcribe", "frames"])
def test_service_dispatches_through_bound_source_port(tmp_path, operation):
    from omni_test_support.v2.media import FakeGateway, _media_instance, _seed, _settle

    class Bound(FakeGateway):
        def __init__(self):
            super().__init__()
            self.sources = []

        async def read(self, configuration, action, target, limit=10):
            assert action == "video", "Processing must use the bound source port"
            return await super().read(configuration, action, target, limit)

        async def read_source(self, configuration, action, source):
            self.sources.append(dict(source))
            return await FakeGateway.read(self, configuration, action, str(source["bvid"]))

    async def scenario():
        gateway = Bound()
        instance = await _media_instance(tmp_path, gateway=gateway)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            assert len(gateway.sources) == 1
            assert gateway.sources[0]["aid"] == "170001" and gateway.sources[0]["cid"] == "230001"
            assert gateway.sources[0]["id"] == source["id"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["subtitle", "transcribe", "frames"])
def test_real_media_jobs_never_publish_changed_video_under_registered_source(tmp_path, operation):
    from omni_test_support.v2.media import _media_instance, _seed, _settle

    async def scenario():
        gateway, state, calls, _ = fixture()
        instance = await _media_instance(tmp_path, gateway=gateway)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            failed = []
            for field, value in [("cid", 790), ("aid", 999), ("bvid", "BV2xx411c7mD")]:
                original = state[field]
                state[field] = value
                job = await instance.media.submit(agent, str(source["id"]), operation, {})
                row = await _settle(instance, agent, job["id"])
                assert row["state"] == "failed" and row["error_code"] == "bilibili_source_changed"
                failed.append(job["id"])
                assert not gateway.payloads and not instance.media._processor.calls
                assert not (instance.media._staging / str(job["id"])).exists()
                current = await instance.media.source(agent, str(source["id"]))
                assert current["aid"] == "123" and current["cid"] == "789"
                assert not current["latest_transcript_id"] and not current["latest_frames_id"]
                state[field] = original
            assert all(call.url.host != "cdn.bilivideo.com" for call in calls)
            fresh = await instance.media.submit(agent, str(source["id"]), operation, {})
            assert (await _settle(instance, agent, fresh["id"]))["state"] == "done"
            assert len(gateway.payloads) == 1 and gateway.payloads[0][1]["cid"] == "789"
            for job_id in failed:
                assert (await instance.media.job(agent, str(job_id)))["state"] == "failed"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
