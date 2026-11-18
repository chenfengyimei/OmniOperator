"""ASR submission validates original parameter types before admitting work or calling ports."""

from __future__ import annotations

import asyncio

import pytest
from omni_test_support.v2.media_admission import fixture

from omni.application.media import ASR_DEFAULT
from omni.domain.contracts import BusinessError


@pytest.mark.parametrize("field", ["model", "device", "compute_type"])
@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_explicit_falsey_plan_fields_are_not_missing_defaults(tmp_path, field, value):
    async def scenario():
        service, db, gateway, processor, gate, _, jobs, _ = fixture(tmp_path, "submit")
        gate.release.set()
        try:
            with pytest.raises(BusinessError) as caught:
                await service.submit("agent", "source", "transcribe", {field: value})
            expected = "media_asr_model_invalid" if field == "model" else "media_asr_plan_invalid"
            assert caught.value.code == expected and caught.value.status == 422
            assert not db.calls and not db.created and not gateway.calls and not processor.calls and not jobs
            assert service.health()["pending"] == 0 and not service._queued_jobs
            assert not list(tmp_path.iterdir())
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [1, 123, 1.5])
def test_numeric_model_names_are_not_coerced_to_valid_strings(tmp_path, value):
    async def scenario():
        service, db, gateway, processor, gate, _, jobs, _ = fixture(tmp_path, "submit")
        gate.release.set()
        try:
            with pytest.raises(BusinessError) as caught:
                await service.submit("agent", "source", "transcribe", {"model": value})
            assert caught.value.code == "media_asr_model_invalid"
            assert not db.calls and not db.created and not gateway.calls and not processor.calls and not jobs
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"model": "fixture-model"},
        {"model": "123"},
        {"device": "auto"},
        {"compute_type": "float32"},
        {"model": "fixture-model", "device": "auto", "compute_type": "float16"},
    ],
)
def test_only_missing_fields_receive_defaults_and_valid_values_are_preserved(tmp_path, params):
    async def scenario():
        service, db, _, _, gate, _, jobs, _ = fixture(tmp_path, "submit")
        gate.release.set()
        try:
            row = await service.submit("agent", "source", "transcribe", params)
            assert row["params"] == {**ASR_DEFAULT, **params}
            assert db.created == [row]
            assert await service.close() and jobs == [row["id"]]
        finally:
            assert await service.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("field", ["model", "device", "compute_type"])
def test_real_database_rejects_bad_plan_without_job_and_allows_explicit_valid_work(tmp_path, field):
    from omni_test_support.v2.media import BV, _media_instance, _seed, _settle

    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            reads = list(instance.media._bilibili.reads)
            with pytest.raises(BusinessError) as caught:
                await instance.media.submit(agent, str(source["id"]), "transcribe", {field: None})
            expected = "media_asr_model_invalid" if field == "model" else "media_asr_plan_invalid"
            assert caught.value.code == expected and caught.value.status == 422
            assert (await instance.media.jobs(agent, str(source["id"]))) == {"items": [], "next_after": None}
            assert instance.media._bilibili.reads == reads and not instance.media._bilibili.downloads
            assert not instance.media._processor.calls
            assert not (await instance.media.source(agent, str(source["id"])))["active_jobs"]
            assert not list(instance.media._staging.glob("*"))
            params = {"model": "fixture-model", "device": "auto", "compute_type": "float32"}
            job = await instance.media.submit(agent, str(source["id"]), "transcribe", params)
            assert job["params"] == params
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            assert len(instance.media._processor.calls) == 1
            assert instance.media._processor.calls[0][2] == params
            jobs = (await instance.media.jobs(agent, str(source["id"])))["items"]
            assert len(jobs) == 1 and jobs[0]["id"] == job["id"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
