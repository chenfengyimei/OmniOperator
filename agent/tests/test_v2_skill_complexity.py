"""Bound logical YAML traversal before alias expansion or object construction."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from fastapi.testclient import TestClient
from test_v2_routing import Model
from test_v2_skill_metadata import document, payload
from test_v2_skills import markdown

from omni.adapters.skill_packages import capture
from omni.composition.http import http_app
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError

FIELDS = "name: test-skill\ndescription: Reviewed procedure\n"


def empty_merges(count):
    return "<<: [" + ", ".join(["{}"] * count) + "]\n" + FIELDS


def fanout(kind, depth=10):
    value = "{name: test-skill}" if kind == "mapping" else "leaf"
    for index in range(depth):
        value = f"[&level{index} {value}, *level{index}]"
        if kind == "mapping":
            value = "{<<: " + value + "}"
    return value


BAD = {
    "129_nodes": empty_merges(122),
    "merge_fanout": "<<: " + fanout("mapping") + "\ndescription: Reviewed procedure\n",
    "merge_cycle": "<<: &loop {<<: *loop}\n" + FIELDS,
    "sequence_cycle": "name: test-skill\ndescription: &loop [*loop]\n",
    "sequence_fanout": "name: test-skill\ndescription: " + fanout("sequence") + "\n",
    "unused_mapping": FIELDS + "unused: " + fanout("mapping") + "\n",
}
GOOD = {
    "merge": "<<: {name: test-skill, description: Reviewed procedure}\nplatforms: [windows, macos, linux]\n",
    "alias": "name: &label test-skill\ndescription: *label\n",
    "deep_empty_merge": "<<: " + "{<<: " * 60 + "{}" + "}" * 60 + "\n" + FIELDS,
}


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("case", BAD)
def test_budget_rejects_before_native_yaml_expansion_and_always_disposes_loader(monkeypatch, format, case):
    raw = document(BAD[case])
    assert len(BAD[case].encode()) < 4096
    calls, disposed = [], []
    original_dispose = yaml.SafeLoader.dispose

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Native YAML expansion started before the complexity check")

    def dispose(loader):
        disposed.append(True)
        return original_dispose(loader)

    # The old-source control stops before allocating an expanded alias graph.
    monkeypatch.setattr(yaml.SafeLoader, "flatten_mapping", forbidden)
    monkeypatch.setattr(yaml.SafeLoader, "construct_document", forbidden)
    monkeypatch.setattr(yaml.SafeLoader, "dispose", dispose)
    with pytest.raises(BusinessError) as caught:
        capture(payload(raw, format), format)
    assert caught.value.code == "invalid_skill" and caught.value.status == 422
    assert not calls and disposed == [True]


@pytest.mark.parametrize("format", ["markdown", "zip"])
@pytest.mark.parametrize("case", GOOD)
def test_ordinary_merges_aliases_and_deep_bounded_yaml_preserve_semantics_and_bytes(format, case):
    raw = document(GOOD[case])
    result = capture(payload(raw, format), format)
    assert result.metadata["name"] == "test-skill"
    assert result.metadata["description"] == ("test-skill" if case == "alias" else "Reviewed procedure")
    assert all(result.metadata[platform] for platform in ("windows", "macos", "linux"))
    assert dict(result.files)["SKILL.md"] == raw


@pytest.mark.parametrize("format", ["markdown", "zip"])
def test_exact_128_logical_nodes_remain_accepted_without_rewriting_the_source(format):
    # Root, merge key/sequence and two scalar key/value pairs contribute seven nodes.
    raw = document(empty_merges(121))
    result = capture(payload(raw, format), format)
    assert result.metadata["description"] == "Reviewed procedure"
    assert dict(result.files)["SKILL.md"] == raw


@pytest.mark.parametrize("operation", ["create", "edit", "zip_import"])
def test_real_database_refuses_over_budget_yaml_before_publication_and_bounded_retry_succeeds(
    tmp_path, monkeypatch, operation
):
    async def scenario():
        instance = await open_instance(tmp_path, model=Model(), capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            old = await instance.skills.save(agent, markdown()) if operation == "edit" else None
            bad = document(empty_merges(122))
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
            valid = document(empty_merges(121)).decode()
            if old:
                assert (await instance.skills.get(agent, old["id"]))["content"] == markdown()
                fresh = await instance.skills.save(agent, valid, old["id"], old["revision"])
            else:
                fresh = await instance.skills.save(agent, valid)
            assert fresh["state"] == "disabled" and fresh["content_revision"] == (2 if old else 1)
            assert (await instance.skills.get(agent, fresh["id"]))["content"] == valid
            assert not (await instance.skills.available(agent))["items"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("edit", [False, True])
def test_owner_http_returns_422_without_changing_revision_and_accepts_exact_boundary(tmp_path, edit):
    with TestClient(http_app(tmp_path, model=Model())) as client:
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}/skills"
        original = client.post(base, json={"content": markdown()}).json() if edit else None
        before = client.get(base).json()
        request = {"content": document(empty_merges(122)).decode()}
        if original:
            request.update(skill_id=original["id"], revision=original["revision"])
        response = client.post(base, json=request)
        assert response.status_code == 422 and "invalid_skill" in response.text
        assert response.headers["cache-control"] == "no-store" and client.get(base).json() == before
        request["content"] = document(empty_merges(121)).decode()
        fresh = client.post(base, json=request)
        assert fresh.status_code == 201 and fresh.json()["state"] == "disabled"
        assert fresh.json()["content_revision"] == (2 if edit else 1)
