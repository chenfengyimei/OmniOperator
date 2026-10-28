"""可切换 embedding 出口：本地 Ollama / LiteLLM API；费用账本从 bootstrap 注入。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.request
from typing import Callable

from ..config import Embedding
from .budget import Ledger, RunBudget


class EmbeddingError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise EmbeddingError("本地 embedding 不接受重定向")


def _local_request(config: Embedding, texts: list[str]) -> tuple[list[list[float]], int | None]:
    payload = json.dumps({"model": config.model, "input": texts, "truncate": False}).encode()
    request = urllib.request.Request(config.endpoint.rstrip("/") + "/api/embed", data=payload,
                                     headers={"Content-Type": "application/json"})
    # 回环连接不读代理环境，禁止把私人记忆意外转发给代理。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=config.timeout_s) as response:
        raw = response.read(4_000_001)
    if len(raw) > 4_000_000:
        raise EmbeddingError("embedding 响应超限")
    data = json.loads(raw)
    return data["embeddings"], data.get("prompt_eval_count")


def _api_request(config: Embedding, texts: list[str]) -> tuple[list[list[float]], int | None]:
    import litellm
    key = os.environ.get(config.api_key_env, "") if config.api_key_env else None
    if config.api_key_env and not key:
        raise EmbeddingError("embedding API 密钥环境变量未配置")
    response = litellm.embedding(model=config.model, input=texts, api_base=config.endpoint,
                                api_key=key, timeout=config.timeout_s, num_retries=0)
    rows = sorted(response.data, key=lambda r: r["index"])
    if [r["index"] for r in rows] != list(range(len(texts))):
        raise EmbeddingError("embedding API 返回了重复或缺失索引")
    usage = getattr(response, "usage", None)
    tokens = getattr(usage, "prompt_tokens", None)
    return [r["embedding"] for r in rows], tokens


class EmbeddingProvider:
    def __init__(self, config: Embedding, ledger: Ledger, *, daily_limit_usd: float = 0,
                 transport: Callable | None = None):
        self.config = config
        self._ledger = ledger
        self._transport = transport or (_local_request if config.provider == "local" else _api_request)
        limits = [v for v in (config.daily_usd, daily_limit_usd) if v > 0]
        self._limit_micro = int(min(limits) * 1_000_000) if limits else 0
        identity = {k: getattr(config, k) for k in ("provider", "model", "revision", "dimensions", "endpoint")}
        self.fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def embed(self, texts: list[str], *, run_id=None, session_id=None, run_budget: RunBudget | None = None) -> list[list[float]]:
        cfg = self.config
        if not cfg.enabled or not cfg.model:
            raise EmbeddingError("embedding 未启用")
        if not texts or len(texts) > cfg.batch_size or any(not t or len(t) > cfg.max_text_chars for t in texts):
            raise EmbeddingError("embedding 输入数量或长度不合法")
        if cfg.provider == "api" and not (cfg.send_to_cloud and self._limit_micro and cfg.input_usd_per_m):
            raise EmbeddingError("云端 embedding 未授权或缺少预算")
        # UTF-8 字节数作为保守上界（避免中文低估），输入不落调用日志。
        estimate = sum(len(t.encode("utf-8")) for t in texts)
        reserved = math.ceil(estimate * cfg.input_usd_per_m) if cfg.provider == "api" else 0
        day = dt.date.today().isoformat()
        if run_budget is not None and not run_budget.try_reserve(reserved):
            raise EmbeddingError("embedding 单次运行预算不足")
        try:
            ok, _ = self._ledger.reserve(day=day, micro=reserved, limit_micro=self._limit_micro)
        except Exception:
            if run_budget is not None:
                run_budget.release(reserved)
            raise EmbeddingError("embedding 费用账本不可用") from None
        if not ok:
            if run_budget is not None:
                run_budget.release(reserved)
            raise EmbeddingError("embedding 每日预算不足")
        started = time.monotonic()
        charged, tokens, outcome = reserved, None, "error"
        try:
            vectors, tokens = self._transport(cfg, texts)
            if len(vectors) != len(texts):
                raise EmbeddingError("embedding 返回数量不一致")
            for vector in vectors:
                if len(vector) != cfg.dimensions or any(not math.isfinite(float(v)) or abs(float(v)) > 1e10 for v in vector):
                    raise EmbeddingError("embedding 维度或数值非法")
                if not any(float(v) for v in vector):
                    raise EmbeddingError("embedding 返回零向量")
            if tokens is not None and (not isinstance(tokens, int) or tokens < 0):
                raise EmbeddingError("embedding 用量非法")
            charged = math.ceil(tokens * cfg.input_usd_per_m) if tokens is not None and cfg.provider == "api" else reserved
            outcome = "ok"
            return [[float(v) for v in vector] for vector in vectors]
        except Exception:
            raise EmbeddingError("embedding 调用失败或输出不合法，已退回关键词检索") from None
        finally:
            self._ledger.settle(day=day, reserved_micro=reserved, charged_micro=charged)
            if run_budget is not None:
                run_budget.settle(reserved, charged)
            self._ledger.record(run_id=run_id, session_id=session_id, purpose="embedding", model=cfg.model,
                                prompt_tokens=tokens, completion_tokens=0, cost_micro=charged,
                                cost_source="reported" if tokens is not None else "reserved", outcome=outcome,
                                error_code="" if outcome == "ok" else "embedding_failed",
                                duration_ms=int((time.monotonic() - started) * 1000))
