"""Shared local-vector preflight and computation; ownership stays with the composing lifecycle."""

from __future__ import annotations

import asyncio

from ..domain.contracts import BusinessError
from ..domain.documents import normalized_vector, vector_identity
from .ports import LocalVectorizer, Record


def vector_status(vectorizer: LocalVectorizer | None) -> Record:
    if vectorizer is None:
        return {"available": False, "reason": "local_vectorizer_not_installed", "fallback": "full_text"}
    try:
        vector_identity(vectorizer.version, vectorizer.dimension)
        status = vectorizer.status()
        if type(status["available"]) is not bool:
            raise ValueError("Invalid vector status")
    except Exception:  # noqa: BLE001 -- passive diagnosis never exposes adapter exceptions
        return {"available": False, "reason": "local_vectorizer_invalid", "fallback": "full_text"}
    return {**status, "version": vectorizer.version, "dimension": vectorizer.dimension, "local_only": True}


async def embed_local(
    vectorizer: LocalVectorizer | None, texts: list[str], version: str, dimension: int, *, purpose: str = "document"
) -> list[list[float]]:
    if vectorizer is None:
        raise BusinessError("vector_unavailable", "No local vectorizer is installed", status=503)
    try:
        rows = await asyncio.wait_for(vectorizer.embed(tuple(texts), purpose=purpose), timeout=10)
        if vectorizer.version != version or vectorizer.dimension != dimension or len(rows) != len(texts):
            raise ValueError("Vectorizer identity or result count changed")
        return [normalized_vector(row, dimension) for row in rows]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise BusinessError(
            "vector_unavailable", "Local vector computation failed; full-text retrieval remains available", status=503
        ) from exc
