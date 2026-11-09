"""One bounded, serial credential executor; actual work outlives cancelled request waiters."""

from __future__ import annotations

import asyncio
import contextvars
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from ..application.ports import Record, SecretStore
from ..domain.contracts import BusinessError
from ..domain.credentials import validate_reference, validate_secret
from .diagnostics import Redactor


@dataclass(eq=False)
class CredentialJob:
    reference: str
    reading: bool
    future: Future
    invalidated: bool = False
    waiter_finished: bool = False


class Credentials:
    def __init__(self, backend: SecretStore, redactor: Redactor, *, max_pending: int = 16) -> None:
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError("Credential capacity must be 1 to 64")
        self._backend, self._redactor, self._limit = backend, redactor, max_pending
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni-credential-io")
        self._lock = threading.RLock()
        self._jobs: set[CredentialJob] = set()
        self._closing = self._closed = False
        self._failures = 0
        self._last_error = ""

    def invalidate(self, reference: str) -> None:
        validate_reference(reference)
        with self._lock:
            for job in self._jobs:
                if job.reference == reference and job.reading:
                    job.invalidated = True

    def _retire(self, job: CredentialJob) -> None:
        with self._lock:
            if job.future.done() and job.waiter_finished:
                self._jobs.discard(job)

    async def _execute(self, reference: str, operation: str, value: str | None = None):
        validate_reference(reference)
        if operation == "set":
            validate_secret(value)

        def perform():
            try:
                if operation == "get":
                    result = self._backend.get(reference)
                    if result is not None:
                        if not isinstance(result, str):
                            raise BusinessError(
                                "credential_store_unavailable",
                                "Credential backend returned an invalid value",
                                status=503,
                            )
                        self._redactor.register(result)
                    return result
                if operation == "set":
                    assert value is not None
                    self._redactor.register(value)
                    self._backend.set(reference, value)
                else:
                    self._backend.delete(reference)
                return None
            except BusinessError as exc:
                # Even a trusted backend's public exception can be user-defined.
                code = (
                    exc.code
                    if exc.code
                    in {
                        "credential_store_unavailable",
                        "credential_decryption_failed",
                        "credential_backend_untrusted",
                        "redaction_capacity",
                        "unsafe_vault",
                        "invalid_credential_reference",
                    }
                    else "credential_store_unavailable"
                )
                raise BusinessError(
                    code, "Credential operation could not complete; inspect credential health", status=503
                ) from exc
            except Exception as exc:
                raise BusinessError(
                    "credential_store_unavailable", "Credential store is unavailable", status=503
                ) from exc

        with self._lock:
            if self._closing:
                raise BusinessError("credential_closing", "Credential service is draining", status=503)
            if len(self._jobs) >= self._limit:
                raise BusinessError("credential_busy", "Credential queue is full", status=503, retryable=True)
            if operation != "get":
                self.invalidate(reference)
            future = self._executor.submit(contextvars.copy_context().run, perform)
            job = CredentialJob(reference, operation == "get", future)
            self._jobs.add(job)

        def finished(actual):
            with self._lock:
                error = actual.exception()
                if error is not None:
                    self._failures += 1
                    self._last_error = (
                        error.code if isinstance(error, BusinessError) else "credential_store_unavailable"
                    )
                else:
                    self._last_error = ""
            self._retire(job)

        future.add_done_callback(finished)
        try:
            wrapped = asyncio.wrap_future(future)
            # Retrieve exceptions even after the shielded caller disappears.
            wrapped.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
            result = await asyncio.shield(wrapped)
            with self._lock:
                if job.reading and job.invalidated:
                    raise BusinessError("credential_changed", "Credential authority changed during the read")
            return result
        finally:
            with self._lock:
                job.waiter_finished = True
            self._retire(job)

    async def get(self, reference: str) -> str | None:
        return await self._execute(reference, "get")

    async def set(self, reference: str, value: str) -> None:
        await self._execute(reference, "set", value)

    async def delete(self, reference: str) -> None:
        await self._execute(reference, "delete")

    def stop_accepting(self) -> None:
        with self._lock:
            self._closing = True
            for job in self._jobs:
                if job.reading:
                    job.invalidated = True

    def health(self) -> Record:
        with self._lock:
            return {
                "closing": self._closing,
                "closed": self._closed,
                "pending": len(self._jobs),
                "capacity": self._limit,
                "failures": self._failures,
                "error_code": self._last_error,
            }

    async def close(self, timeout_s: float = 30) -> bool:
        self.stop_accepting()
        with self._lock:
            futures = [asyncio.wrap_future(job.future) for job in self._jobs if not job.future.done()]
        if futures:
            for future in futures:
                future.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
            _, pending = await asyncio.wait(futures, timeout=max(0, timeout_s))
            if pending:
                return False
        with self._lock:
            self._executor.shutdown(wait=False)
            self._closed = True
        return True
