"""Skill metadata has one value per field; invalid imports cannot alter reviewed revisions."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
import yaml
from fastapi.testclient import TestClient
from test_v2_routing import Model
from test_v2_skills import markdown

from omni.adapters.skill_packages import capture
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

DESCRIPTION = "description: Reviewed reusable procedure\n"
BAD = {
    "name": "name: first-skill\nname: test-skill\n" + DESCRIPTION,
    "same_name": "name: test-skill\nname: test-skill\n" + DESCRIPTION,
    "description": "name: test-skill\ndescription: PRIVATE_METADATA_CANARY\n" + DESCRIPTION,
    "platforms": "name: test-skill\n" + DESCRIPTION + "platforms: [linux]\nplatforms: [windows]\n",
    "version": "name: test-skill\n" + DESCRIPTION + "version: old\nversion: current\n",
    "escaped_key": 'name: first-skill\n"na\\u006de": test-skill\n' + DESCRIPTION,
    "aliased_key": "&field name: first-skill\n*field: test-skill\n" + DESCRIPTION,
    "merge_override": "<<: {name: first-skill}\nname: test-skill\n" + DESCRIPTION,
    "merge_sequence": "<<: [{name: first-skill}, {name: test-skill}]\n" + DESCRIPTION,
    "merge_duplicate": "<<: {name: first-skill, name: test-skill}\n" + DESCRIPTION,
    "repeated_merge": "<<: {name: first-skill}\n<<: {name: test-skill}\n" + DESCRIPTION,
}
GOOD = {
    "plain": "name: test-skill\n" + DESCRIPTION + "version: v1\n",
    "flow": "{name: test-skill, description: Reviewed reusable procedure}\n",
    "escaped": '"na\\u006de": test-skill\n' + DESCRIPTION,
    "merge": "<<: {name: test-skill, platforms: [windows]}\n" + DESCRIPTION,
    "alias": "name: &label test-skill\ndescription: *label\n",
    "folded": "name: test-skill\ndescription: >-\n  Reviewed reusable procedure\n",
}


def document(header):
    return ("---\n" + header + "---" + markdown().split("---", 2)[2]).encode()


def payload(raw, format):
    if format == "markdown":
        return raw
    with io.BytesIO() as output:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", raw)
            archive.writestr("references/guide.md", "Declared reference text.")
        return output.getvalue()


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("case", BAD)
def test_duplicate_metadata_fields_are_rejected_instead_of_overwritten(format, case):
    with pytest.raises(BusinessError) as caught:
        capture(payload(document(BAD[case]), format), format)
    assert caught.value.code == "invalid_skill" and caught.value.status == 422
    assert "PRIVATE_METADATA_CANARY" not in str(caught.value)


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("case", GOOD)
def test_unambiguous_yaml_preserves_metadata_and_original_package_bytes(format, case):
    raw = document(GOOD[case])
    result = capture(payload(raw, format), format)
    assert result.metadata["name"] == "test-skill"
    assert result.metadata["description"] == ("test-skill" if case == "alias" else "Reviewed reusable procedure")
    assert result.metadata["windows"] is True
    assert result.metadata["macos"] is (case != "merge") and result.metadata["linux"] is (case != "merge")
    expected = {"SKILL.md": raw}
    if format == "zip":
        expected["references/guide.md"] = b"Declared reference text."
    assert dict(result.files) == expected
    # Parsing a skill must not change PyYAML's shared constructor behavior.
    assert yaml.safe_load("name: first\nname: second\n") == {"name": "second"}


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("header", ["[name, description]\n", "? [name, description]\n: invalid\n", "true: bad\n"])
def test_non_mapping_and_non_string_metadata_keys_keep_precise_validation_errors(format, header):
    with pytest.raises(BusinessError) as caught:
        capture(payload(document(header), format), format)
    assert caught.value.code == "invalid_skill" and caught.value.status == 422


@pytest.mark.parametrize("operation", ["create", "edit", "zip_import"])
def test_real_database_rejects_ambiguous_metadata_before_publication_and_allows_explicit_valid_work(
    tmp_path, monkeypatch, operation
):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            old = await instance.skills.save(agent, markdown()) if operation == "edit" else None
            bad = document(BAD["description"])
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
            if old:
                assert (await instance.skills.get(agent, old["id"]))["content"] == markdown()
                fresh = await instance.skills.save(
                    agent, markdown(text="Explicit new reviewed procedure."), old["id"], old["revision"]
                )
                assert fresh["content_revision"] == 2
            else:
                fresh = await instance.skills.save(agent, markdown())
                assert fresh["content_revision"] == 1
            assert fresh["state"] == "disabled" and not (await instance.skills.available(agent))["items"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_owner_http_rejects_duplicate_fields_without_exposing_values_or_changing_revision(tmp_path, edit):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/skills"
        original = client.post(base, json={"content": markdown()}).json() if edit else None
        before = client.get(base).json()
        request = {"content": document(BAD["description"]).decode()}
        if original:
            request.update(skill_id=original["id"], revision=original["revision"])
        response = client.post(base, json=request)
        assert response.status_code == 422 and "invalid_skill" in response.text
        assert "PRIVATE_METADATA_CANARY" not in response.text
        assert client.get(base).json() == before and response.headers["cache-control"] == "no-store"
        request["content"] = markdown(text="Explicit valid retry after the rejected input.")
        fresh = client.post(base, json=request)
        assert fresh.status_code == 201 and fresh.json()["state"] == "disabled"
        assert fresh.json()["content_revision"] == (2 if edit else 1)
