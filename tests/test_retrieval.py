import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from omni.config import Embedding
from omni.core.retrieval import HybridMemory
from omni.core.session import ResourceLeases
from omni.llm.embedding import EmbeddingError, EmbeddingProvider
from omni.memory.state import StateStore
from omni.memory.store import MemoryStore
from omni.memory.usage import UsageLedger
from omni.memory.vectors import VectorIndex


@pytest.fixture
def components(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    state = StateStore(tmp_path / "state.db")
    ledger = UsageLedger(state)
    config = Embedding(enabled=True, model="fixture-v1", dimensions=2)
    calls = []
    def transport(config, texts):
        calls.extend(texts)
        return [[1., 0.] if any(w in text for w in ("拿铁", "咖啡")) else [0., 1.] for text in texts], 5
    provider = EmbeddingProvider(config, ledger, transport=transport)
    index = VectorIndex(tmp_path / "memory.db", model=provider.fingerprint, dimensions=2, scopes=["private", "group:1"])
    hybrid = HybridMemory(store, index, provider, ResourceLeases(state))
    yield store, state, provider, index, hybrid, calls
    hybrid.close()
    store.close()
    state.close()


def test_index_semantic_recall_and_scope_before_return(components):
    store, _, _, index, hybrid, calls = components
    store.save_fact("喜欢无糖拿铁")
    store.save_fact("隐私拿铁偏好", scope="group:1")
    store.save_fact("另一群咖啡", scope="group:2")
    assert hybrid.index_pending() == 2
    assert "另一群咖啡" not in calls  # 未授权 scope 不会送 embedding
    assert hybrid.search("咖啡") == ["喜欢无糖拿铁"]
    assert hybrid.search("咖啡", scope="group:1") == ["隐私拿铁偏好"]
    assert index.search([1., 0.], scope="private")[0]["source_hash"]


def test_forget_clears_vector_in_same_transaction_and_pending_cannot_resurrect(components):
    store, _, _, index, hybrid, _ = components
    fact_id = store.save_fact("喜欢无糖拿铁")
    pending = index.pending(8)[0]
    hybrid.index_pending()
    assert index.status()["indexed"] == 1
    assert store.forget_fact(fact_id)
    assert index.status()["indexed"] == 0
    assert not index.put(pending, [1., 0.])
    assert hybrid.search("咖啡") == []


def test_forget_while_query_in_flight_does_not_return_lexical_snapshot(components):
    store, _, provider, _, hybrid, _ = components
    fact_id = store.save_fact("咖啡拿铁")
    hybrid.index_pending()
    def delete_during_request(config, texts):
        store.forget_fact(fact_id)
        return [[1., 0.]], 1
    provider._transport = delete_during_request
    assert hybrid.search("咖啡") == []


def test_model_revision_rebuild_and_restart_resume(components, tmp_path):
    store, _, provider, index, hybrid, _ = components
    store.save_fact("咖啡")
    hybrid.index_pending()
    changed = provider.config.model_copy(update={"revision": "2", "dimensions": 3})
    next_provider = EmbeddingProvider(changed, provider._ledger)
    next_index = VectorIndex(tmp_path / "memory.db", model=next_provider.fingerprint, dimensions=3, scopes=["private"])
    try:
        assert next_index.pending(8)
        assert next_index.search([1., 0., 0.], scope="private") == []
        row = next_index.pending(8)[0]
        next_index.put(row, [1., 0., 0.])
    finally:
        next_index.close()
    reopened = VectorIndex(tmp_path / "memory.db", model=next_provider.fingerprint, dimensions=3, scopes=["private"])
    try:
        assert not reopened.pending(8)
    finally:
        reopened.close()


@pytest.mark.parametrize("vectors", [[[1.]], [[math.nan, 0.]], [[0., 0.]], []])
def test_invalid_vectors_fall_back_to_fts(components, vectors):
    store, _, provider, _, hybrid, _ = components
    store.save_fact("咖啡关键词")
    provider._transport = lambda *_: (vectors, 1)
    assert hybrid.search("咖啡") == ["咖啡关键词"]
    assert "降级" in hybrid.last_error


def test_extension_loading_disabled_after_initialization(components):
    _, _, _, index, _, _ = components
    assert index.available
    import sqlite3
    with pytest.raises(sqlite3.OperationalError, match="not authorized"):
        index._conn.execute("SELECT load_extension('untrusted')")


def test_missing_extension_keeps_fts(components, tmp_path, monkeypatch):
    import sqlite_vec
    monkeypatch.setattr(sqlite_vec, "load", lambda _: (_ for _ in ()).throw(OSError("missing")))
    store, state, provider, _, _, calls = components
    store.save_fact("本地关键词")
    index = VectorIndex(tmp_path / "memory.db", model="missing", dimensions=2, scopes=["private"])
    hybrid = HybridMemory(store, index, provider, ResourceLeases(state))
    try:
        assert hybrid.search("关键词") == ["本地关键词"]
        assert not calls
        assert not index.available
    finally:
        hybrid.close()


def test_api_privacy_budget_and_usage(components):
    _, _, provider, _, _, calls = components
    with pytest.raises(ValidationError):
        Embedding(enabled=True, provider="api", model="configured", endpoint="https://example.test/v1")
    config = Embedding(enabled=True, provider="api", model="configured", endpoint="https://example.test/v1",
                       dimensions=2, send_to_cloud=True, daily_usd=0.00001, input_usd_per_m=1)
    cloud = EmbeddingProvider(config, provider._ledger, transport=provider._transport)
    assert cloud.embed(["hi"]) == [[0., 1.]]
    assert len(calls) == 1
    with pytest.raises(EmbeddingError, match="预算"):
        cloud.embed(["too expensive to fit"])
    assert len(calls) == 1
    record = provider._ledger._state._read("SELECT purpose,cost_micro FROM llm_calls ORDER BY id DESC LIMIT 1")
    assert record[0]["purpose"] == "embedding" and record[0]["cost_micro"] == 5


def test_ollama_real_loopback_http_contract(components):
    _, _, provider, _, _, _ = components
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps({"embeddings": [[0.2, 0.3]], "prompt_eval_count": 2}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = provider.config.model_copy(update={"endpoint": f"http://127.0.0.1:{server.server_port}"})
        local = EmbeddingProvider(config, provider._ledger)
        assert local.embed(["中文查询"]) == [[0.2, 0.3]]
        assert requests[0][0] == "/api/embed"
        assert requests[0][1] == {"model": "fixture-v1", "input": ["中文查询"], "truncate": False}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_local_provider_cannot_point_at_remote_server():
    with pytest.raises(ValidationError, match="回环"):
        Embedding(endpoint="https://example.test")


def test_litellm_embedding_adapter_orders_indexes_and_passes_config(components, monkeypatch):
    from types import SimpleNamespace
    import litellm
    _, _, provider, _, _, _ = components
    called = []
    def fake_embedding(**kwargs):
        called.append(kwargs)
        return SimpleNamespace(data=[{"index": 1, "embedding": [0., 1.]}, {"index": 0, "embedding": [1., 0.]}],
                               usage=SimpleNamespace(prompt_tokens=2))
    monkeypatch.setattr(litellm, "embedding", fake_embedding)
    monkeypatch.setenv("TEST_EMBEDDING_KEY", "fixture-only")
    config = Embedding(enabled=True, provider="api", model="openai/fixture", endpoint="https://example.test/v1",
                       api_key_env="TEST_EMBEDDING_KEY", send_to_cloud=True, dimensions=2,
                       daily_usd=0.01, input_usd_per_m=1)
    cloud = EmbeddingProvider(config, provider._ledger)
    assert cloud.embed(["a", "b"]) == [[1., 0.], [0., 1.]]
    assert called[0]["num_retries"] == 0
    assert called[0]["api_key"] == "fixture-only"
    assert called[0]["api_base"] == "https://example.test/v1"
