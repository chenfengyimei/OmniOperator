"""Malformed DEFLATE members return a skill validation error before any installed file publication."""

from __future__ import annotations

from omni_test_support.v2.skill_zip_errors import (
    FILES as FILES,
)
from omni_test_support.v2.skill_zip_errors import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.skill_zip_errors import (
    Model as Model,
)
from omni_test_support.v2.skill_zip_errors import (
    SkillPackages as SkillPackages,
)
from omni_test_support.v2.skill_zip_errors import (
    TestClient as TestClient,
)
from omni_test_support.v2.skill_zip_errors import (
    archive_bytes as archive_bytes,
)
from omni_test_support.v2.skill_zip_errors import (
    asyncio as asyncio,
)
from omni_test_support.v2.skill_zip_errors import (
    capture as capture,
)
from omni_test_support.v2.skill_zip_errors import (
    damaged as damaged,
)
from omni_test_support.v2.skill_zip_errors import (
    http_app as http_app,
)
from omni_test_support.v2.skill_zip_errors import (
    io as io,
)
from omni_test_support.v2.skill_zip_errors import (
    markdown as markdown,
)
from omni_test_support.v2.skill_zip_errors import (
    open_instance as open_instance,
)
from omni_test_support.v2.skill_zip_errors import (
    pytest as pytest,
)
from omni_test_support.v2.skill_zip_errors import (
    struct as struct,
)
from omni_test_support.v2.skill_zip_errors import (
    zipfile as zipfile,
)
from omni_test_support.v2.skill_zip_errors import (
    zlib as zlib,
)


@pytest.mark.parametrize("target", FILES)
@pytest.mark.parametrize("kind", ["block", "length"])
def test_real_deflate_fault_maps_to_422_and_closes_retained_entry_and_archive(monkeypatch, target, kind):
    raw = damaged(target, kind)
    with io.BytesIO(raw) as source, zipfile.ZipFile(source) as archive, pytest.raises(zlib.error):
        archive.read(target)
    entries, archives = [], []
    original_open = zipfile.ZipFile.open

    def opened(archive, *args, **kwargs):
        entry = original_open(archive, *args, **kwargs)
        entries.append(entry)
        archives.append(archive)
        return entry

    monkeypatch.setattr(zipfile.ZipFile, "open", opened)
    with pytest.raises(BusinessError) as caught:
        capture(raw, "zip")
    assert caught.value.code == "invalid_skill" and caught.value.status == 422
    assert "PRIVATE_SKILL_ZIP_TEXT" not in str(caught.value) and caught.value.__cause__ is None
    assert entries and all(entry.closed for entry in entries) and all(archive.fp is None for archive in archives)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_valid_archives_keep_exact_main_reference_bytes_and_metadata(compression):
    result = capture(archive_bytes(compression), "zip")
    assert dict(result.files) == FILES and result.metadata["name"] == "test-skill"


@pytest.mark.parametrize("target", FILES)
def test_existing_crc_rejection_still_uses_the_same_business_error(target):
    raw = damaged(target, "crc")
    with io.BytesIO(raw) as source, zipfile.ZipFile(source) as archive, pytest.raises(zipfile.BadZipFile, match="CRC"):
        archive.read(target)
    with pytest.raises(BusinessError) as caught:
        capture(raw, "zip")
    assert caught.value.code == "invalid_skill" and caught.value.status == 422


@pytest.mark.parametrize("target", FILES)
def test_async_codec_retires_corrupt_parse_and_accepts_a_fresh_valid_package(target):
    async def scenario():
        codec = SkillPackages()
        try:
            with pytest.raises(BusinessError) as caught:
                await codec.parse(damaged(target), "zip")
            assert caught.value.code == "invalid_skill" and not codec._pending
            assert dict((await codec.parse(archive_bytes(), "zip")).files) == FILES
            assert not codec._pending
        finally:
            assert await codec.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_real_sqlite_import_rejects_before_file_publication_and_valid_zip_can_follow(tmp_path, monkeypatch, edit):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            old = await instance.skills.save(agent, markdown()) if edit else None
            obj = await instance.objects.publish(agent, damaged("references/guide.md"), "application/zip")
            before = await instance.skills.page(agent)
            published, original_publish = [], instance.skills._objects.publish

            async def publish(*args, **kwargs):
                published.append(args)
                return await original_publish(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(instance.skills._objects, "publish", publish)
                with pytest.raises(BusinessError) as caught:
                    await instance.skills.import_object(
                        agent, obj["id"], "zip", old["id"] if old else None, old["revision"] if old else 0
                    )
                assert caught.value.code == "invalid_skill" and caught.value.status == 422 and not published
            assert await instance.skills.page(agent) == before
            if old:
                assert (await instance.skills.get(agent, old["id"]))["content"] == markdown()
            valid = await instance.objects.publish(agent, archive_bytes(), "application/zip")
            fresh = await instance.skills.import_object(
                agent, valid["id"], "zip", old["id"] if old else None, old["revision"] if old else 0
            )
            assert fresh["state"] == "disabled" and fresh["content_revision"] == (2 if edit else 1)
            assert (await instance.skills.get(agent, fresh["id"], path="references/guide.md"))[
                "content"
            ].encode() == FILES["references/guide.md"]
            exported = await instance.skills.export(agent, fresh["id"])
            _, raw = await instance.objects.get(agent, exported["object_id"])
            assert dict(capture(raw, "zip").files) == FILES
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_http_import_returns_422_without_decoder_details_or_revision_changes_and_valid_retry_succeeds(tmp_path, edit):
    with TestClient(http_app(tmp_path, model=Model()), raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        old = client.post(base + "/skills", json={"content": markdown()}).json() if edit else None
        uploaded = client.post(
            base + "/objects", content=damaged("SKILL.md", "length"), headers={"content-type": "application/zip"}
        )
        assert uploaded.status_code == 201
        before = client.get(base + "/skills").json()
        request = {"object_id": uploaded.json()["id"], "format": "zip"}
        if old:
            request.update(skill_id=old["id"], revision=old["revision"])
        response = client.post(base + "/skills/import", json=request)
        assert response.status_code == 422 and "invalid_skill" in response.text
        assert "PRIVATE_SKILL_ZIP_TEXT" not in response.text and "decompress" not in response.text
        assert response.headers["cache-control"] == "no-store" and client.get(base + "/skills").json() == before
        uploaded = client.post(base + "/objects", content=archive_bytes(), headers={"content-type": "application/zip"})
        assert uploaded.status_code == 201
        request["object_id"] = uploaded.json()["id"]
        fresh = client.post(base + "/skills/import", json=request)
        assert fresh.status_code == 201 and fresh.json()["state"] == "disabled"
        assert fresh.json()["content_revision"] == (2 if edit else 1)
