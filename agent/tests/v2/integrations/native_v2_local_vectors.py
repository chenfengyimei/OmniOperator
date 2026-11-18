"""Explicit optional-runtime acceptance: real ONNX graphs/tokenizers and SQLite, no pretrained model or network.

Run explicitly after installing the locked vectors extra; the default offline suite uses the core transport tests.
"""

import asyncio
import hashlib

import pytest

from omni.adapters.local_vectors import LocalVectors
from omni.adapters.vector_files import configure
from omni.composition.instance import open_instance
from omni.domain.contracts import BusinessError
from omni.domain.local_vectors import canonical, fingerprint


def model_bundle(root, *, external=False, query_prefix=""):
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, pre_tokenizers

    root.mkdir()
    vocab = {"[PAD]": 0, "[UNK]": 1, "cat": 2, "dog": 3, "feline": 4, "banana": 5, "fruit": 6}
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    weights = numpy_helper.from_array(
        np.array([[0, 0], [0.1, 0.1], [1, 0], [0.9, 0.1], [0.95, 0.05], [0, 1], [0.1, 0.9]], dtype=np.float32),
        "weights",
    )
    if external:
        weights.ClearField("raw_data")
        weights.data_location = TensorProto.EXTERNAL
        reference = weights.external_data.add()
        reference.key, reference.value = "location", str(root.parent / "private-sentinel")
    graph = helper.make_graph(
        [helper.make_node("Gather", ["weights", "input_ids"], ["last_hidden_state"], axis=0)],
        "isolated-real-inference",
        [
            helper.make_tensor_value_info("input_ids", TensorProto.INT64, ["batch", "tokens"]),
            helper.make_tensor_value_info("attention_mask", TensorProto.INT64, ["batch", "tokens"]),
        ],
        [helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["batch", "tokens", 2])],
        [weights],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    data = {"model.onnx": model.SerializeToString(), "tokenizer.json": tokenizer.to_str().encode()}
    spec = {
        "schema": 1,
        "model_id": "synthetic-onnx-not-pretrained",
        "dimension": 2,
        "max_tokens": 8,
        "pooling": "mean_masked",
        "output": "last_hidden_state",
        "pad_id": 0,
        "pad_token": "[PAD]",
        "query_prefix": query_prefix,
        "document_prefix": "",
        "files": {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in data.items()},
    }
    for name, raw in data.items():
        (root / name).write_bytes(raw)
    (root / "manifest.json").write_bytes(canonical(spec))
    return spec


def test_real_native_pooling_and_overflow_preserve_tail_and_query_prefix(tmp_path):
    async def scenario():
        root = tmp_path / "model"
        spec = model_bundle(root, query_prefix="banana ")
        client = LocalVectors(root, fingerprint(spec), spec)
        try:
            document = (await client.embed(["cat"], purpose="document"))[0]
            query = (await client.embed(["cat"], purpose="query"))[0]
            assert document == [1.0, 0.0] and query[1] > 0.7
            short, long = await client.embed(["cat " * 8, "cat " * 8 + "banana " * 8])
            assert short == [1.0, 0.0] and long[1] > 0.7
            assert client.status()["state"] == "loaded" and client.status()["local_only"]
        finally:
            await client.close()
            assert client._owned is None

    asyncio.run(scenario())


def test_real_native_rejects_external_tensor_files_without_loading_them(tmp_path):
    async def scenario():
        root = tmp_path / "model"
        spec = model_bundle(root, external=True)
        (tmp_path / "private-sentinel").write_bytes(b"private-native-vector-canary")
        client = LocalVectors(root, fingerprint(spec), spec)
        try:
            with pytest.raises(BusinessError) as failure:
                await client.embed(["cat"])
            assert failure.value.code == "vector_external_data_forbidden"
            assert not client.status()["available"] and client._owned is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_real_native_startup_build_search_erasure_and_model_change_fallback(tmp_path):
    async def scenario():
        root, home = tmp_path / "model", tmp_path / "home"
        spec = model_bundle(root)
        configure(home, root, fingerprint(spec))
        instance = await open_instance(home, capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            assert instance.documents.vector_status()["state"] == "configured_unverified"
            cat = await instance.documents.save(agent, "animals", "cat dog")
            banana = await instance.documents.save(agent, "food", "banana fruit")
            for row in (cat, banana):
                assert (await instance.documents.rebuild(agent, row["id"], "vector"))["state"] == "ready"
            search = await instance.documents.search(agent, "feline")
            assert search["mode"] == "hybrid" and search["items"][0]["document_id"] == cat["id"]
            other = (await instance._database.write(lambda u: u.create_agent("Other")))["id"]
            assert not (await instance.documents.search(other, "feline"))["items"]
            await instance.documents.erase(agent, cat["id"], cat["revision"])
            assert all(
                r["document_id"] != cat["id"] for r in (await instance.documents.search(agent, "feline"))["items"]
            )
            # A cached process must stop serving the declared model after its file identity changes.
            raw = (root / "model.onnx").read_bytes()
            (root / "model.onnx").write_bytes(raw)
            result = await instance.documents.search(agent, "banana")
            assert result["mode"] == "full_text" and result["degraded_reason"] == "vector_bundle_changed"
            assert result["items"][0]["document_id"] == banana["id"]
            assert instance.health()["ready"] and not instance.documents.vector_status()["available"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["replace", "erase"])
def test_real_native_late_result_cannot_publish_for_changed_source(tmp_path, operation):
    async def scenario():
        root, home = tmp_path / "model", tmp_path / "home"
        spec = model_bundle(root)
        configure(home, root, fingerprint(spec))
        instance = await open_instance(home, capabilities=())
        release, entered = asyncio.Event(), asyncio.Event()
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            source = await instance.documents.save(agent, "source", "cat dog")
            client = instance.documents._vectorizer
            original = client._exchange

            async def delayed(method, args):
                result = await original(method, args)
                if method == "embed":
                    entered.set()
                    await release.wait()
                return result

            client._exchange = delayed
            build = asyncio.create_task(instance.documents.rebuild(agent, source["id"], "vector"))
            await asyncio.wait_for(entered.wait(), 10)
            if operation == "replace":
                await instance.documents.save(
                    agent, "source", "banana", document_id=source["id"], revision=source["revision"]
                )
            else:
                await instance.documents.erase(agent, source["id"], source["revision"])
            release.set()
            result = await build
            assert result["state"] == "invalidated" and result["error_code"] == (
                "source_erased" if operation == "erase" else "source_changed"
            )
            assert all(
                r["document_id"] != source["id"] for r in (await instance.documents.search(agent, "feline"))["items"]
            )
        finally:
            release.set()
            await instance.close()

    asyncio.run(scenario())


def test_real_native_new_model_identity_requires_explicit_index_rebuild(tmp_path):
    async def scenario():
        root, home = tmp_path / "model", tmp_path / "home"
        spec = model_bundle(root)
        configure(home, root, fingerprint(spec))
        instance = await open_instance(home, capabilities=())
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            row = await instance.documents.save(agent, "source", "cat dog")
            await instance.documents.rebuild(agent, row["id"], "vector")
            previous = instance.documents.vector_status()["version"]
            assert (await instance.documents.search(agent, "feline"))["mode"] == "hybrid"
        finally:
            await instance.close()
        spec["query_prefix"] = "banana "
        (root / "manifest.json").write_bytes(canonical(spec))
        configure(home, root, fingerprint(spec))
        instance = await open_instance(home, capabilities=())
        try:
            assert instance.documents.vector_status()["version"] != previous
            result = await instance.documents.search(agent, "feline")
            assert result["mode"] == "full_text" and not result["items"]
            assert result["degraded_reason"] == "vector_index_missing_or_stale"
            await instance.documents.rebuild(agent, row["id"], "vector")
            assert (await instance.documents.search(agent, "feline"))["mode"] == "hybrid"
        finally:
            await instance.close()

    asyncio.run(scenario())
