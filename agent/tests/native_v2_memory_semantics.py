"""Explicit optional-runtime acceptance using a real synthetic ONNX graph, not pretrained quality."""

import asyncio

from native_v2_local_vectors import model_bundle
from test_v2_memory_organization import request
from test_v2_memory_semantics import NoModel

from omni.adapters.local_vectors import LocalVectors
from omni.composition.instance import open_instance
from omni.domain.local_vectors import fingerprint


def test_real_native_memory_recall_and_source_bound_related_proposal(tmp_path):
    async def scenario():
        bundle = tmp_path / "bundle"
        spec = model_bundle(bundle)
        vectors = LocalVectors(bundle, fingerprint(spec), spec)
        instance = await open_instance(tmp_path / "instance", model=NoModel(), vectorizer=vectors, capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            facts = [await instance.application.remember(agent, text) for text in ("cat", "feline", "banana")]
            assert all(r["state"] == "ready" for r in (await instance.memory_semantics.index(agent))["items"])
            result = await instance.memory_semantics.search(agent, "dog")
            assert result["mode"] == "hybrid" and {r["content"] for r in result["items"][:2]} == {"cat", "feline"}
            related = await instance.memory_semantics.propose(agent, request(facts[:2]))
            assert related["valid"] and related["candidate_count"] == 1
            await instance.application.forget(agent, facts[0]["id"])
            assert not (await instance.memory_semantics.get(agent, related["id"]))["valid"]
        finally:
            assert (await instance.close())["complete"]
        assert vectors._owned is None

    asyncio.run(scenario())
