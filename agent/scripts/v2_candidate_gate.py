"""Read-only V2 Windows offline-candidate gate; never imports V1, runs acceptance, or authorizes release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CHECKS = (
    "full_pytest",
    "strict_types",
    "ruff",
    "python_format",
    "frontend",
    "package",
    "windows_lifecycle",
    "business_ui",
)
SOURCE_DIRS = ("agent/src", "agent/tests", "agent/scripts", "scripts", "web", "deploy/v2")
CONFIGS = (
    "pyproject.toml",
    "agent/pyproject.toml",
    "agent/hatch_build.py",
    "uv.lock",
    "ruff.toml",
    "pyrightconfig.json",
    "pyrightconfig.strict.json",
)
IGNORED = frozenset({"__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", ".build", ".swiftpm"})
SHA = re.compile(r"[a-f0-9]{64}")
MAX_REPORT_BYTES = 32 * 1024 * 1024


def _no_links(path: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("linked_path")


def _hash(path: Path) -> str:
    _no_links(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path):
    _no_links(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("report_too_large")
    return json.loads(raw)


def capture_source(root: Path) -> dict[str, object]:
    _no_links(root)
    files = set()
    for name in SOURCE_DIRS:
        directory = root / name
        _no_links(directory)
        if not directory.is_dir():
            raise ValueError("missing_source_directory")
        for base, directories, names in os.walk(directory, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in IGNORED)
            for child in directories:
                _no_links(Path(base) / child)
            for child in names:
                path = Path(base) / child
                if path.suffix in {".pyc", ".pyo"}:
                    continue
                files.add(path.relative_to(root).as_posix())
                if len(files) > 16384:
                    raise ValueError("too_many_source_files")
    files.update(name for name in CONFIGS if (root / name).is_file())
    # Include contracts used by documentation guards. Progress/evidence records stay outside the code identity.
    files.update(p.relative_to(root).as_posix() for p in (root / "docs").glob("V2_*.md"))
    rows = {name: _hash(root / name) for name in sorted(files)}
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"format": "omni-v2-candidate-source-1", "source_sha256": digest, "files": rows}


def _artifact(folder: Path, descriptor) -> Path:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise ValueError("invalid_artifact")
    name, digest = descriptor["path"], descriptor["sha256"]
    if not isinstance(name, str) or not isinstance(digest, str) or SHA.fullmatch(digest) is None:
        raise ValueError("invalid_artifact")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or ":" in name or "\\" in name:
        raise ValueError("unsafe_artifact")
    path = folder / relative
    if not path.resolve().is_relative_to(folder.resolve()) or _hash(path) != digest:
        raise ValueError("artifact_hash_mismatch")
    return path


def _junit_node(case: ET.Element) -> str:
    parts = case.get("classname", "").split(".")
    if parts[0] == "agent":
        parts = parts[1:]
    module = next((i for i, name in enumerate(parts) if name.startswith("test_")), None)
    if module is None or not case.get("name"):
        raise ValueError("invalid_junit_node")
    return "agent/" + "/".join(parts[: module + 1]) + ".py::" + "::".join([*parts[module + 1 :], case.get("name", "")])


def _pytest_command(command: object, *, collect: bool = False) -> None:
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("invalid_pytest_command")
    try:
        module = command.index("-m")
    except ValueError:
        raise ValueError("invalid_pytest_command") from None
    if module not in (1, 2) or command[module + 1 : module + 2] != ["pytest"]:
        raise ValueError("invalid_pytest_command")
    if module == 2 and command[1] != "-I":
        raise ValueError("invalid_pytest_command")
    arguments = iter(command[module + 2 :])
    seen_collect = False
    for argument in arguments:
        if argument == "-n":
            if next(arguments, "") not in {"0", "1", "2", "3", "4", "auto"}:
                raise ValueError("invalid_pytest_workers")
        elif argument == "--collect-only":
            seen_collect = True
        elif argument not in {"-q", "--tb=short", "--dist=loadfile"} and not argument.startswith("--junitxml="):
            raise ValueError("scoped_pytest_cannot_satisfy_full_suite")
    if seen_collect != collect:
        raise ValueError("invalid_pytest_collection_mode")


def _full_suite(folder: Path, artifacts: dict) -> None:
    collected = _json(_artifact(folder, artifacts["collected"]))
    if not isinstance(collected, list) or not collected or not all(isinstance(n, str) for n in collected):
        raise ValueError("invalid_collection")
    if len(collected) != len(set(collected)):
        raise ValueError("duplicate_collection")
    junit = _artifact(folder, artifacts["junit"])
    if junit.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("report_too_large")
    raw = junit.read_bytes()
    if b"<!" in raw:
        raise ValueError("invalid_junit")
    tree = ET.fromstring(raw)
    cases = list(tree.iter("testcase"))
    if any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError("full_suite_has_failures_or_skips")
    if any(float(s.get(key, "0")) != 0 for s in tree.iter("testsuite") for key in ("failures", "errors", "skipped")):
        raise ValueError("full_suite_summary_failed")
    nodes = [_junit_node(case) for case in cases]
    if len(nodes) != len(set(nodes)) or set(nodes) != set(collected):
        raise ValueError("full_suite_collection_mismatch")


def assess(root: Path, manifest_path: Path) -> dict[str, object]:
    manifest = _json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("format") != "omni-v2-candidate-gate-1":
        raise ValueError("invalid_manifest")
    folder, blockers = manifest_path.parent, []
    snapshot = _json(_artifact(folder, manifest["source"]))
    current = capture_source(root)
    if snapshot != current:
        blockers.append("candidate_source_changed")
    checks = manifest.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS):
        raise ValueError("missing_or_unknown_check")
    for kind in CHECKS:
        try:
            report = _json(_artifact(folder, checks[kind]))
            if (
                not isinstance(report, dict)
                or report.get("format") != "omni-v2-candidate-check-1"
                or report.get("kind") != kind
                or report.get("source_sha256") != current["source_sha256"]
                or type(report.get("returncode")) is not int
                or report["returncode"] != 0
                or not isinstance(report.get("command"), list)
                or not report["command"]
                or not all(isinstance(arg, str) for arg in report["command"])
            ):
                raise ValueError("failed_or_stale_report")
            artifacts = report.get("artifacts")
            if not isinstance(artifacts, dict) or not artifacts:
                raise ValueError("missing_check_artifacts")
            for descriptor in artifacts.values():
                _artifact(folder, descriptor)
            if kind == "full_pytest":
                _pytest_command(report["command"])
                _pytest_command(report["collection_command"], collect=True)
                if type(report.get("collection_returncode")) is not int or report["collection_returncode"] != 0:
                    raise ValueError("collection_failed")
                _full_suite(folder, artifacts)
            elif kind == "strict_types":
                summary = _json(_artifact(folder, artifacts["pyright"]))["summary"]
                expected = len(_json(root / "pyrightconfig.strict.json")["include"])
                if summary["errorCount"] != 0 or summary["warningCount"] != 0 or summary["filesAnalyzed"] != expected:
                    raise ValueError("strict_scope_incomplete")
        except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as exc:
            blockers.append(f"{kind}:{type(exc).__name__}:{exc}")
    return {
        "format": "omni-v2-candidate-assessment-1",
        "source_sha256": current["source_sha256"],
        "ready_for_windows_offline_review": not blockers,
        "blockers": blockers,
        "whole_v2_complete": False,
        "production_release_ready": False,
        "unverified_release_gates": ["real_accounts", "real_devices", "native_desktop", "72h", "other_platforms"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "assess"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "snapshot":
            result = capture_source(args.root.resolve())
        else:
            if args.manifest is None:
                parser.error("assess requires --manifest")
            result = assess(args.root.resolve(), args.manifest.resolve())
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(text + "\n")
        print(text)
        return 0 if args.operation == "snapshot" or result["ready_for_windows_offline_review"] else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
