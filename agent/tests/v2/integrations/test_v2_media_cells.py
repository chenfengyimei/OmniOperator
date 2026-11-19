"""media_cells: addressable frame-cell grid index keeps sub-second anchors, isolation and read-only wiring."""

from __future__ import annotations

import asyncio

import pytest
from omni_test_support.v2.media import (
    BV,
    FakeGateway,
    FakeProcessor,
    NoModel,
    Secrets,
    _context,
    _media_instance,
    _seed,
    _settle,
    open_instance,
)

from omni.application.media import MediaService
from omni.domain.contracts import BusinessError, Outcome
from omni.domain.media import CELL_COLUMNS, cell_anchors, normalize_cell, render_cells

TIMESTAMPS = [0.0, 10.0, 20.0]


# ---------- domain rules ----------


def test_normalize_cell_none_and_bounds():
    assert normalize_cell(None, 3) is None
    assert normalize_cell(0, 3) == 0 and normalize_cell(2, 3) == 2
    assert normalize_cell(None, 0) is None
    for bad in (-1, 3, "1", 1.5, True):
        with pytest.raises(BusinessError) as caught:
            normalize_cell(bad, 3)
        assert caught.value.code == "media_cell_invalid"
    with pytest.raises(BusinessError):
        normalize_cell(0, 0)


def test_cell_anchors_grid_and_subsecond():
    anchors = cell_anchors([0.0, 10.5, 20.25, 30.0, 40.0])
    assert CELL_COLUMNS == 2 and len(anchors) == 5
    assert anchors[0] == {"cell": 0, "row": 0, "column": 0, "time_s": 0.0}
    assert anchors[1] == {"cell": 1, "row": 0, "column": 1, "time_s": 10.5}
    assert anchors[2] == {"cell": 2, "row": 1, "column": 0, "time_s": 20.25}
    assert anchors[3] == {"cell": 3, "row": 1, "column": 1, "time_s": 30.0}
    assert anchors[4] == {"cell": 4, "row": 2, "column": 0, "time_s": 40.0}
    assert cell_anchors([]) == []


def test_render_cells_all_and_selected():
    anchors = cell_anchors(TIMESTAMPS)
    full = render_cells("frames", "source", anchors, None)
    assert "cells=3 columns=2" in full
    assert "[cell 0] row=0 col=0 t=0.000s" in full
    assert "[cell 2] row=1 col=0 t=20.000s" in full
    one = render_cells("frames", "source", anchors, 1)
    assert "[cell 1] row=0 col=1 t=10.000s" in one
    assert "[cell 0]" not in one and "[cell 2]" not in one
    assert "cells=3" in one


# ---------- real instance: anchored frames + model invoke ----------


def test_cells_anchored_frames_and_model_invoke(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 3, "width": 320})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            view = await instance.media.source(agent, str(source["id"]))
            fid = str(view["latest_frames_id"])
            result = await instance.media.cells(agent, str(source["id"]), fid, None)
            assert result["frame_count"] == 3 and result["columns"] == 2
            assert result["width"] == 320 and result["height"] == 180
            cells = result["cells"]
            assert len(cells) == 3
            assert cells[0] == {"cell": 0, "row": 0, "column": 0, "time_s": 0.0}
            assert cells[1]["time_s"] == 10.0 and cells[2]["row"] == 1 and cells[2]["time_s"] == 20.0
            assert "[cell 0] row=0 col=0 t=0.000s" in result["text"]
            res = await instance.media.invoke(
                "media_cells",
                {"source_id": str(source["id"]), "frames_id": fid},
                operation_id="run",
                context=_context(agent),
            )
            assert res.status == Outcome.OK and res.data is not None
            assert res.data["cells"][1]["time_s"] == 10.0
            picked = await instance.media.invoke(
                "media_cells",
                {"source_id": str(source["id"]), "cell": 2},
                operation_id="run",
                context=_context(agent),
            )
            assert picked.status == Outcome.OK and picked.data["selected"] == 2
            assert "[cell 2]" in picked.text and "[cell 0]" not in picked.text
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_cells_latest_frames_without_id(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 2, "width": 320})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            result = await instance.media.cells(agent, str(source["id"]), None, None)
            assert result["frame_count"] == 2 and len(result["cells"]) == 2
            assert result["cells"][1] == {"cell": 1, "row": 0, "column": 1, "time_s": 10.0}
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_cells_invalid_selection_rejects(tmp_path):
    async def scenario():
        instance = await _media_instance(tmp_path)
        try:
            agent, config = await _seed(instance)
            source = await instance.media.register(agent, str(config["id"]), BV)
            job = await instance.media.submit(agent, str(source["id"]), "frames", {"count": 3, "width": 320})
            assert (await _settle(instance, agent, job["id"]))["state"] == "done"
            with pytest.raises(BusinessError) as caught:
                await instance.media.cells(agent, str(source["id"]), None, 3)
            assert caught.value.code == "media_cell_invalid"
            res = await instance.media.invoke(
                "media_cells",
                {"source_id": str(source["id"]), "cell": 9},
                operation_id="run",
                context=_context(agent),
            )
            assert res.status == Outcome.ERROR and res.error_code == "media_cell_invalid"
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


def test_media_cells_registered_read_only_in_production(tmp_path):
    async def scenario():
        instance = await open_instance(
            tmp_path,
            model=NoModel(),
            bilibili_backend=FakeGateway(),
            media_processor=FakeProcessor(),
            credential_backend=Secrets(),
        )
        try:
            tools = instance.application._tools
            assert "media_cells" in tools
            assert tools["media_cells"].spec.writes is False
            assert tools["media_cells"].spec.resource == "media"
        finally:
            await instance.close()

    asyncio.run(scenario())


# ---------- stub guards: pairing, isolation, closing, latest fallback ----------


class StubDatabase:
    def __init__(self, exists=True):
        self.calls, self.exists = 0, exists

    async def read(self, operation):
        self.calls += 1
        return operation(self)

    async def write(self, operation):
        raise AssertionError("cells cannot write")

    def media_frames_get(self, agent_id, frames_id):
        if not self.exists or agent_id != "agent" or frames_id != "frames":
            raise BusinessError("media_frames_not_found", "No such frame sheet", status=404)
        return {
            "id": "frames",
            "source_id": "source",
            "object_id": "object",
            "frame_count": 3,
            "width": 320,
            "height": 180,
            "timestamps": TIMESTAMPS,
        }

    def media_source_get(self, agent_id, source_id):
        if agent_id != "agent" or source_id != "source":
            raise BusinessError("media_source_not_found", "No such source", status=404)
        return {"id": "source", "latest_frames_id": "frames" if self.exists else None}


class StubProcessor:
    def status(self):
        return {"available": True}

    async def close(self, timeout_s=30):
        return True


def _stub(tmp_path, exists=True):
    database = StubDatabase(exists)
    service = MediaService(database, None, None, None, StubProcessor(), None, tmp_path)
    return service, database


def test_cells_source_pairing_mismatch_rejects(tmp_path):
    async def scenario():
        service, database = _stub(tmp_path)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.cells("agent", "other-source", "frames", None)
            assert caught.value.code == "media_frames_not_found" and database.calls == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_cells_other_agent_never_reads(tmp_path):
    async def scenario():
        service, database = _stub(tmp_path)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.cells("other-agent", "source", "frames", None)
            assert caught.value.code == "media_frames_not_found" and database.calls == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_cells_closing_rejects_before_any_lookup(tmp_path):
    async def scenario():
        service, database = _stub(tmp_path)
        service.stop_accepting()
        try:
            with pytest.raises(BusinessError) as caught:
                await service.cells("agent", "source", "frames", None)
            assert caught.value.code == "media_closing" and database.calls == 0
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_cells_latest_without_frames_rejects(tmp_path):
    async def scenario():
        service, database = _stub(tmp_path, exists=False)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.cells("agent", "source", None, None)
            assert caught.value.code == "media_frames_not_found" and database.calls == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())


def test_cells_invalid_cell_rejects_after_read(tmp_path):
    async def scenario():
        service, database = _stub(tmp_path)
        try:
            with pytest.raises(BusinessError) as caught:
                await service.cells("agent", "source", "frames", 5)
            assert caught.value.code == "media_cell_invalid" and database.calls == 1
        finally:
            assert await service.close()

    asyncio.run(scenario())
