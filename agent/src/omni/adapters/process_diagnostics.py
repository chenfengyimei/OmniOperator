"""Process-owned logging and exception hooks; installed only by the executable host."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from types import TracebackType
from typing import Any

from .diagnostics import JsonLogSink


class _HostFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("omni.v2") or record.levelno >= logging.WARNING


class ProcessDiagnostics:
    """No stdout redirection, exception-object retention or duplicate hook chaining."""

    _owner = threading.Lock()

    def __init__(self, sink: JsonLogSink, loop: asyncio.AbstractEventLoop) -> None:
        if loop.is_closed():
            raise RuntimeError("Process diagnostics requires an open event loop")
        if not self._owner.acquire(blocking=False):
            raise RuntimeError("Process diagnostics already has an owner")
        self._closed = False
        self._sink, self._loop = sink, loop
        self._sys, self._thread, self._unraisable = sys.excepthook, threading.excepthook, sys.unraisablehook
        self._async = loop.get_exception_handler()
        # Heterogeneous native hook signatures; each handler is typed individually below.
        self._hooks: tuple[Any, Any, Any, Any] = (
            self._process_error,
            self._thread_error,
            self._unraisable_error,
            self._async_error,
        )
        self.counts = {"process": 0, "thread": 0, "unraisable": 0, "asyncio": 0}
        self._filter = _HostFilter()
        self._logger = logging.getLogger("omni.v2.unhandled")
        self._saved: list[tuple[logging.Logger, list[logging.Handler], bool]] = []
        self._captured: set[logging.Logger] = set()
        root = logging.getLogger()
        # Existing library handlers can bypass root redaction or print twice.
        # Save them without closing; restore the host's previous setup on close.
        self.capture((root, *(v for v in list(root.manager.loggerDict.values()) if isinstance(v, logging.Logger))))
        root.addHandler(sink)
        sink.addFilter(self._filter)
        sys.excepthook, threading.excepthook, sys.unraisablehook = self._hooks[:3]
        loop.set_exception_handler(self._hooks[3])

    def capture(self, loggers: tuple[logging.Logger, ...]) -> None:
        """Explicitly attach loggers installed by a lazily imported SDK."""
        for logger in loggers:
            if logger not in self._captured:
                self._saved.append((logger, logger.handlers[:], logger.propagate))
                self._captured.add(logger)
            logger.handlers = []
            logger.propagate = True

    def _record(
        self, event: str, kind: type[BaseException] | None, value: BaseException | None, tb: TracebackType | None
    ) -> None:
        if kind and issubclass(kind, (SystemExit, KeyboardInterrupt, asyncio.CancelledError)):
            return
        self.counts[event] += 1
        self._logger.error(
            "Unhandled execution failure",
            exc_info=(kind, value, tb) if kind and value is not None else None,
            extra={"event": "unhandled." + event, "error_code": "unhandled_exception"},
        )

    def _process_error(self, kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        if issubclass(kind, (SystemExit, KeyboardInterrupt)):
            self._sys(kind, value, tb)
        else:
            self._record("process", kind, value, tb)

    def _thread_error(self, args: threading.ExceptHookArgs) -> None:
        self._record("thread", args.exc_type, args.exc_value, args.exc_traceback)

    def _unraisable_error(self, args: Any) -> None:
        # args.object, err_msg and callback repr may contain user content.
        self._record("unraisable", args.exc_type, args.exc_value, args.exc_traceback)

    def _async_error(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        self._record(
            "asyncio", type(exc) if isinstance(exc, BaseException) else None, exc, getattr(exc, "__traceback__", None)
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if sys.excepthook is self._hooks[0]:
            sys.excepthook = self._sys
        if threading.excepthook is self._hooks[1]:
            threading.excepthook = self._thread
        if sys.unraisablehook is self._hooks[2]:
            sys.unraisablehook = self._unraisable
        if self._loop.get_exception_handler() is self._hooks[3]:
            self._loop.set_exception_handler(self._async)
        self._sink.removeFilter(self._filter)
        for logger, handlers, propagate in self._saved:
            logger.handlers, logger.propagate = handlers, propagate
        self._saved.clear()
        self._captured.clear()
        self._owner.release()
