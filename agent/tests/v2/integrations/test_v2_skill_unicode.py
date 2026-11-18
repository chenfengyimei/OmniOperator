"""Decoded skill strings must remain UTF-8 before publication, SQLite or owner HTTP."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from omni_test_support.v2.routing import Model
from omni_test_support.v2.skill_metadata import document, payload
from omni_test_support.v2.skills import markdown

from omni.adapters.skill_packages import capture
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.skills import document as validate_document

ESCAPES = [r"\uD800", r"\uDFFF", r"\uD83D\uDE80"]
VALID_DESCRIPTION = "中文流程 🚀"


def content(field, value):
    description = value if field == "description" else "Reviewed procedure"
    header = f'name: test-skill\ndescription: "{description}"\n'
    if field == "version":
        header += f'version: "{value}"\n'
    return document(header)


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("field", ["description", "version"])
@pytest.mark.parametrize("escape", ESCAPES)
def test_yaml_decoded_surrogates_are_rejected_even_when_source_bytes_are_valid_utf8(format, field, escape):
    raw = content(field, "PRIVATE_SKILL_TEXT" + escape)
    assert raw.decode("utf-8").encode("utf-8") == raw
    with pytest.raises(BusinessError) as caught:
        capture(payload(raw, format), format)
    assert caught.value.code == "invalid_skill" and caught.value.status == 422
    assert "PRIVATE_SKILL_TEXT" not in str(caught.value)


@pytest.mark.parametrize("field", ["description", "version", "body"])
@pytest.mark.parametrize("value", [chr(0xD800), chr(0xDFFF), chr(0xD83D) + chr(0xDE80)])
def test_domain_rejects_unencodable_metadata_and_body_without_replacement_or_normalization(field, value):
    metadata = {"name": "test-skill", "description": "Reviewed procedure"}
    body = markdown().split("---", 2)[2].strip()
    if field == "body":
        body += value
    else:
        metadata[field] = value
    with pytest.raises(BusinessError) as caught:
        validate_document(metadata, body)
    assert caught.value.code == "invalid_skill" and caught.value.status == 422


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("field", ["description", "version"])
@pytest.mark.parametrize("source,decoded", [("中文流程 🚀", "中文流程 🚀"), (r"\U0001F680", "🚀"), ("é", "é")])
def test_valid_non_ascii_scalar_escape_and_combining_text_preserve_semantics_and_source_bytes(
    format, field, source, decoded
):
    raw = content(field, source)
    result = capture(payload(raw, format), format)
    assert result.metadata["name"] == "test-skill"
    assert result.metadata["description"] == (decoded if field == "description" else "Reviewed procedure")
    assert dict(result.files)["SKILL.md"] == raw


@pytest.mark.parametrize("field,limit", [("description", 120), ("version", 40)])
def test_multibyte_exact_character_limits_remain_accepted(field, limit):
    raw = content(field, "🚀" * limit)
    result = capture(raw, "markdown")
    assert dict(result.files)["SKILL.md"] == raw
    if field == "description":
        assert result.metadata["description"] == "🚀" * 120


@pytest.mark.parametrize("operation", ["create", "edit", "zip_import"])
def test_real_database_rejects_bad_unicode_before_files_or_revisions_and_valid_unicode_roundtrips(
    tmp_path, monkeypatch, operation
):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            old = await instance.skills.save(agent, markdown()) if operation == "edit" else None
            bad = content("version" if operation == "zip_import" else "description", "PRIVATE_SKILL_TEXT" + ESCAPES[0])
            obj = (
                await instance.objects.publish(agent, payload(bad, "zip"), "application/zip")
                if operation == "zip_import"
                else None
            )
            before = await instance.skills.page(agent)
            published, original_publish = [], instance.skills._objects.publish

            async def publish(*args, **kwargs):
                published.append(args)
                return await original_publish(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(instance.skills._objects, "publish", publish)
                with pytest.raises(BusinessError) as caught:
                    if obj:
                        await instance.skills.import_object(agent, obj["id"], "zip")
                    elif old:
                        await instance.skills.save(agent, bad.decode(), old["id"], old["revision"])
                    else:
                        await instance.skills.save(agent, bad.decode())
                assert caught.value.code == "invalid_skill" and caught.value.status == 422
                assert not published
            assert await instance.skills.page(agent) == before
            valid = content("description", VALID_DESCRIPTION).decode()
            if old:
                assert (await instance.skills.get(agent, old["id"]))["content"] == markdown()
                fresh = await instance.skills.save(agent, valid, old["id"], old["revision"])
            else:
                fresh = await instance.skills.save(agent, valid)
            assert fresh["description"] == VALID_DESCRIPTION and fresh["state"] == "disabled"
            assert fresh["content_revision"] == (2 if old else 1)
            assert (await instance.skills.get(agent, fresh["id"]))["content"] == valid
            exported = await instance.skills.export(agent, fresh["id"])
            _, archive = await instance.objects.get(agent, exported["object_id"])
            parsed = capture(archive, "zip")
            assert parsed.metadata["description"] == VALID_DESCRIPTION
            assert dict(parsed.files)["SKILL.md"] == valid.encode()
            assert not (await instance.skills.available(agent))["items"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_owner_http_returns_422_for_decoded_surrogates_and_preserves_existing_revision(tmp_path, edit):
    with TestClient(http_app(tmp_path, model=Model()), raise_server_exceptions=False) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/skills"
        original = client.post(base, json={"content": markdown()}).json() if edit else None
        before = client.get(base).json()
        request = {"content": content("description", "PRIVATE_SKILL_TEXT" + ESCAPES[0]).decode()}
        if original:
            request.update(skill_id=original["id"], revision=original["revision"])
        response = client.post(base, json=request)
        assert response.status_code == 422 and "invalid_skill" in response.text
        assert "PRIVATE_SKILL_TEXT" not in response.text and response.headers["cache-control"] == "no-store"
        assert client.get(base).json() == before
        request["content"] = content("description", VALID_DESCRIPTION).decode()
        fresh = client.post(base, json=request)
        assert fresh.status_code == 201 and fresh.json()["description"] == VALID_DESCRIPTION
        assert fresh.json()["state"] == "disabled" and fresh.json()["content_revision"] == (2 if edit else 1)
