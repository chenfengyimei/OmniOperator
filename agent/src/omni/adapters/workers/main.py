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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

from ...domain.contracts import JSON, ActionResult, Effect, Outcome
from ...domain.files_patch import PatchError, patch_replace
from ...domain.files_search import (
    count_matches,
    match_lines,
    match_name,
    match_path_glob,
    normalize_context,
    normalize_file_glob,
    normalize_offset,
    normalize_order,
    normalize_output_mode,
    normalize_query,
)
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

    async def invoke(self, name: str, args: dict[str, Any], context: dict[str, Any]) -> ActionResult:
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

    def _files(self, name: str, args: dict[str, Any], context: dict[str, Any], effects: Effects) -> ActionResult:
        path = workspace_path(context["workspace"], str(args.get("path", ".")))
        if name == "files_list":
            rows: list[dict[str, Any]] = []
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
            return ActionResult(
                Outcome.OK, json.dumps(rows, ensure_ascii=False), data={"entries": cast(list[JSON], rows)}
            )
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
        if name == "files_search":
            target = args.get("target", "content")
            if target not in {"content", "files"}:
                raise ValueError("Search target must be content or files")
            query = normalize_query(args.get("query"))
            output_mode = normalize_output_mode(args.get("output_mode"))
            context_lines = normalize_context(args.get("context"))
            offset = normalize_offset(args.get("offset"))
            order = normalize_order(args.get("order"))
            file_glob = normalize_file_glob(args.get("file_glob"))
            collect_all = order == "modified"
            cap = offset + 50
            base = Path(context["workspace"]).resolve()
            results: list[dict[str, Any]] = []
            mtimes: list[float] = []
            scanned = 0
            truncated = False
            deadline = time.monotonic() + 5.0
            prune = {".git", ".venv", ".ssh", ".aws", ".azure", "node_modules", "__pycache__"}
            for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
                dirnames[:] = sorted(
                    d
                    for d in dirnames
                    if d.lower() not in prune
                    and not d.lower().startswith(".env")
                    and not (Path(dirpath) / d).is_symlink()
                    and not (Path(dirpath) / d).is_junction()
                )
                for filename in sorted(filenames):
                    if (not collect_all and len(results) >= cap) or scanned >= 2000 or time.monotonic() > deadline:
                        truncated = True
                        break
                    candidate = Path(dirpath) / filename
                    try:
                        relative = str(candidate.relative_to(base))
                        checked = workspace_path(context["workspace"], relative)
                        info = checked.stat()
                    except (ValueError, OSError):
                        continue
                    if not checked.is_file() or info.st_nlink != 1:
                        continue
                    scanned += 1
                    if target == "files":
                        if match_name(filename, query):
                            results.append({"path": relative})
                            mtimes.append(info.st_mtime)
                            if not collect_all and len(results) >= cap:
                                truncated = True
                                break
                        continue
                    if file_glob is not None and not match_path_glob(Path(relative).as_posix(), file_glob):
                        continue
                    if info.st_size > 256 * 1024:
                        continue
                    try:
                        text = checked.read_bytes().decode("utf-8-sig")
                    except (UnicodeError, OSError):
                        continue
                    if output_mode == "content":
                        for hit in match_lines(text, query, context_lines):
                            row: dict[str, Any] = {"path": relative, "line": hit["line"], "text": hit["text"]}
                            if context_lines > 0:
                                row["before"] = hit["before"]
                                row["after"] = hit["after"]
                            results.append(row)
                            mtimes.append(info.st_mtime)
                            if not collect_all and len(results) >= cap:
                                truncated = True
                                break
                    elif output_mode == "files_with_matches":
                        if match_lines(text, query):
                            results.append({"path": relative})
                            mtimes.append(info.st_mtime)
                    else:
                        total = count_matches(text, query)
                        if total:
                            results.append({"path": relative, "count": total})
                            mtimes.append(info.st_mtime)
                if (not collect_all and len(results) >= cap) or scanned >= 2000 or time.monotonic() > deadline:
                    truncated = True
                    break
            if collect_all:
                # Newest-first across the whole bounded scan; stable ties keep discovery order.
                ranked = sorted(range(len(results)), key=lambda i: mtimes[i], reverse=True)
                page = [results[i] for i in ranked][offset:cap]
                truncated = truncated or len(results) > cap
            else:
                page = results[offset:cap]
            return ActionResult(
                Outcome.OK,
                json.dumps(page, ensure_ascii=False),
                data={
                    "matches": cast(list[JSON], page),
                    "scanned": scanned,
                    "truncated": truncated,
                    "target": target,
                    "output_mode": output_mode,
                    "context": context_lines,
                    "offset": offset,
                    "order": order,
                },
            )
        if name == "files_patch":
            if not path.is_file():
                raise ValueError("Patch target must be an existing regular file")
            if path.stat().st_size > 256 * 1024:
                raise ValueError("File exceeds patch limit")
            raw = path.read_bytes()
            expected = args.get("expected_sha256")
            if not expected or hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError("File changed or patch was not authorized with its current fingerprint")
            replace_all = args.get("replace_all", False)
            if type(replace_all) is not bool:
                raise ValueError("replace_all must be a boolean")
            try:
                patched, count, strategy = patch_replace(
                    raw.decode("utf-8-sig"),
                    args.get("old_string"),
                    args.get("new_string"),
                    replace_all=replace_all,
                )
            except PatchError as exc:
                return ActionResult(Outcome.ERROR, str(exc), Effect.NONE, error_code=exc.code)
            payload = patched.encode("utf-8")
            if len(payload) > 256 * 1024:
                raise ValueError("Patched file exceeds write limit")
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".omni-write-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                workspace_path(context["workspace"], str(args["path"]))
                current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                if current != expected:
                    raise ValueError("File fingerprint changed while preparing the patch")
                effects.dispatched = True
                publish_file(Path(temporary), path, replace=True)
            finally:
                Path(temporary).unlink(missing_ok=True)
            digest = hashlib.sha256(payload).hexdigest()
            return ActionResult(
                Outcome.OK,
                "File patched",
                Effect.APPLIED,
                verification=digest,
                data={"sha256": digest, "replacements": count, "strategy": strategy},
            )
        raise ValueError("Unknown file capability")

    async def _shell(self, args: dict[str, Any], context: dict[str, Any], effects: Effects) -> ActionResult:
        raw_argv = args.get("argv")
        if not isinstance(raw_argv, list):
            raise ValueError("Provide a bounded argv array; shell interpolation is not supported")
        argv = cast(list[Any], raw_argv)
        if not argv or len(argv) > 100 or not all(isinstance(item, str) and "\0" not in item for item in argv):
            raise ValueError("Provide a bounded argv array; shell interpolation is not supported")
        cwd = workspace_path(context["workspace"], str(args.get("cwd", ".")))
        effects.dispatched = True
        process = await asyncio.create_subprocess_exec(
            *cast(list[str], argv),
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain(stream: asyncio.StreamReader) -> tuple[str, bool]:
            output = bytearray()
            truncated = False
            while data := await stream.read(8192):
                remaining = max(0, 256 * 1024 - len(output))
                truncated = truncated or len(data) > remaining
                output.extend(data[:remaining])
            return output.decode("utf-8", errors="replace"), truncated

        assert process.stdout is not None and process.stderr is not None
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

    async def _web(self, name: str, args: dict[str, Any], context: dict[str, Any], effects: Effects) -> ActionResult:
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
