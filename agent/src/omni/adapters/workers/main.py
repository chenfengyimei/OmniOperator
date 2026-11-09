"""Capability subprocess. Requests carry scope; no database or Runtime is imported."""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import os
import platform
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

from ...domain.contracts import ActionResult, Effect, Outcome
from ..object_store import publish_file
from .protocol import MAX_FRAME, encode_response
from .protocol import request as decode_request


@dataclass
class Effects:
    dispatched: bool = False


def workspace_path(root_text: str, relative: str) -> Path:
    if not root_text or not Path(root_text).is_absolute():
        raise ValueError("Configure an absolute workspace first")
    root = Path(root_text).resolve(strict=True)
    fragment = Path(relative)
    private_names = {".git", ".venv", ".ssh", ".aws", ".azure", "owner.token", "bili_cookies.json"}
    if any(
        part.lower() in private_names or part.lower() == ".env" or part.lower().startswith(".env.")
        for part in fragment.parts
    ):
        raise ValueError("Private runtime or credential paths cannot be exposed by file tools")
    if fragment.is_absolute() or any(part in {"..", ""} or ":" in part for part in fragment.parts):
        raise ValueError("Path must be relative to the configured workspace")
    path = root / fragment
    for part in (path, *path.parents):
        if not part.is_relative_to(root):
            break
        if part.is_symlink() or part.is_junction():
            raise ValueError("Workspace links are not permitted")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path escapes workspace")
    if path.exists() and (not path.is_dir() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ValueError("Workspace files must be single-link regular files")
    return path


class Worker:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}

    async def invoke(self, name: str, args: dict, context: dict) -> ActionResult:
        effects = Effects()
        try:
            if name == "system_info":
                return ActionResult(
                    Outcome.OK,
                    json.dumps(
                        {
                            "system": platform.system(),
                            "machine": platform.machine(),
                            "python": platform.python_version(),
                        },
                        ensure_ascii=False,
                    ),
                )
            if name.startswith("files_"):
                return self._files(name, args, context, effects)
            if name == "shell_execute":
                return await self._shell(args, context, effects)
            if name.startswith("browser_"):
                return await self._web(name, args, context, effects)
            return ActionResult(
                Outcome.ERROR, "Capability is not implemented by this worker", error_code="unsupported_capability"
            )
        except Exception:  # noqa: BLE001 -- worker failures preserve dispatch certainty without leaking native payloads
            return ActionResult(
                Outcome.UNKNOWN if effects.dispatched else Outcome.ERROR,
                "Capability outcome requires verification"
                if effects.dispatched
                else "Capability request was rejected before dispatch",
                Effect.UNKNOWN if effects.dispatched else Effect.NONE,
                error_code="capability_outcome_unknown" if effects.dispatched else "invalid_capability_request",
            )

    def _files(self, name: str, args: dict, context: dict, effects: Effects) -> ActionResult:
        path = workspace_path(context["workspace"], str(args.get("path", ".")))
        if name == "files_list":
            rows = []
            for item in heapq.nsmallest(200, path.iterdir(), key=lambda p: p.name):
                if item.is_symlink() or item.is_junction():
                    continue
                if item.is_file() and item.stat().st_nlink != 1:
                    continue
                rows.append(
                    {
                        "name": item.name,
                        "directory": item.is_dir(),
                        "bytes": item.stat().st_size if item.is_file() else None,
                    }
                )
            return ActionResult(Outcome.OK, json.dumps(rows, ensure_ascii=False), data={"entries": rows})
        if name == "files_read":
            if path.stat().st_size > 256 * 1024:
                raise ValueError("File exceeds text read limit")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            return ActionResult(
                Outcome.OK,
                f"sha256={digest}\n" + content.decode("utf-8-sig"),
                verification=digest,
                data={"sha256": digest},
            )
        if name == "files_write":
            payload = str(args["content"]).encode("utf-8")
            if len(payload) > 256 * 1024:
                raise ValueError("File exceeds write limit")
            expected = args.get("expected_sha256")
            if path.exists():
                if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("File changed or overwrite was not authorized with its current fingerprint")
            elif expected:
                raise ValueError("Expected file does not exist")
            if not path.parent.is_dir():
                raise ValueError("Parent directory must already exist")
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".omni-write-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Revalidate after staging and before publishing.
                workspace_path(context["workspace"], str(args["path"]))
                current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                if current != expected:
                    raise ValueError("File fingerprint changed while preparing the write")
                effects.dispatched = True
                publish_file(Path(temporary), path, replace=True)
            finally:
                Path(temporary).unlink(missing_ok=True)
            digest = hashlib.sha256(payload).hexdigest()
            return ActionResult(Outcome.OK, "File saved", Effect.APPLIED, verification=digest, data={"sha256": digest})
        raise ValueError("Unknown file capability")

    async def _shell(self, args: dict, context: dict, effects: Effects) -> ActionResult:
        argv = args.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > 100
            or not all(isinstance(item, str) and "\0" not in item for item in argv)
        ):
            raise ValueError("Provide a bounded argv array; shell interpolation is not supported")
        cwd = workspace_path(context["workspace"], str(args.get("cwd", ".")))
        effects.dispatched = True
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain(stream):
            output = bytearray()
            truncated = False
            while data := await stream.read(8192):
                remaining = max(0, 256 * 1024 - len(output))
                truncated = truncated or len(data) > remaining
                output.extend(data[:remaining])
            return output.decode("utf-8", errors="replace"), truncated

        (stdout, stdout_truncated), (stderr, stderr_truncated) = await asyncio.gather(
            drain(process.stdout), drain(process.stderr)
        )
        code = await process.wait()
        if stdout_truncated:
            stdout = "[stdout truncated at 262144 bytes]\n" + stdout
        if stderr_truncated:
            stderr = "[stderr truncated at 262144 bytes]\n" + stderr
        return ActionResult(
            Outcome.OK if code == 0 else Outcome.ERROR,
            stdout + ("\n" + stderr if stderr else ""),
            Effect.APPLIED,
            error_code="" if code == 0 else "command_failed",
            data={
                "exit_code": code,
                "truncated": stdout_truncated or stderr_truncated,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            },
        )

    async def _web(self, name: str, args: dict, context: dict, effects: Effects) -> ActionResult:
        from urllib.parse import urlsplit

        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch()
        assert self._browser is not None
        agent = context["agent_id"]
        if agent not in self._contexts:
            self._contexts[agent] = await self._browser.new_context(accept_downloads=False)
            self._pages[agent] = await self._contexts[agent].new_page()
        page = self._pages[agent]
        if name == "browser_navigate":
            url = str(args["url"])
            target = urlsplit(url)
            if target.scheme not in {"http", "https"} or target.username or target.password:
                raise ValueError("Browser navigation requires a plain HTTP(S) URL")
            effects.dispatched = True
            await page.goto(url, timeout=30_000, wait_until="domcontentloaded")
            return ActionResult(
                Outcome.OK,
                (await page.locator("body").inner_text())[:80_000],
                Effect.APPLIED,
                verification=page.url,
                data={"title": await page.title(), "url": page.url},
            )
        if name == "browser_read":
            return ActionResult(Outcome.OK, (await page.locator("body").inner_text())[:80_000], data={"url": page.url})
        if name in {"browser_click", "browser_fill"}:
            if page.url != args.get("expected_url"):
                raise ValueError("Page changed since approval")
            locator = page.locator(str(args["selector"]))
            if await locator.count() != 1:
                raise ValueError("Selector must identify exactly one element")
            if name == "browser_fill":
                effects.dispatched = True
                await locator.fill(str(args["text"]), timeout=10_000)
            else:
                effects.dispatched = True
                await locator.click(timeout=10_000)
            return ActionResult(Outcome.OK, "Browser action completed", Effect.APPLIED, verification=page.url)
        raise ValueError("Unknown browser capability")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


async def serve() -> None:
    worker = Worker()
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1)
            if not line:
                return
            if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                return
            try:
                request = decode_request(line)
            except ValueError:
                return
            result = await worker.invoke(request["method"], request["arguments"], request["context"])
            frame = encode_response(request["id"], result)
            sys.stdout.buffer.write(frame)
            sys.stdout.buffer.flush()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(serve())
