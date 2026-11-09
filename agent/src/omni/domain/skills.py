"""Versioned textual skills, bounded disclosure and edit rules; skills confer no authority."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import cast

from .contracts import BusinessError, ToolSpec

PLATFORMS = ("windows", "macos", "linux")
SECTIONS = ("When to Use", "Procedure", "Pitfalls", "Verification")
MAX_FILES = 17
MAX_BYTES = 512 * 1024
MAX_ZIP = 1024 * 1024
MAX_ACTIVE = 4


def invalid(code: str = "invalid_skill") -> BusinessError:
    return BusinessError(code, "Skill metadata, content, path or revision is invalid", status=422)


def identifiers(value: object) -> tuple[str, ...]:
    try:
        if not isinstance(value, (tuple, list)) or len(value) > MAX_ACTIVE:
            raise ValueError
        result = tuple(value)
        if len(set(result)) != len(result) or any(not isinstance(v, str) or str(uuid.UUID(v)) != v for v in result):
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise invalid() from exc


def file_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 180 or value != unicodedata.normalize("NFC", value):
        raise invalid()
    parts = value.split("/")
    if len(parts) > 4 or any(
        not p
        or p in {".", ".."}
        or p[-1] in " ."
        or re.search(r'[\\:< >"|?*\x00-\x1f]', p)
        or p.split(".")[0].casefold()
        in {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}
        for p in parts
    ):
        raise invalid()
    if value != "SKILL.md" and (
        len(parts) < 2
        or parts[0] != "references"
        or "." + parts[-1].rsplit(".", 1)[-1].lower()
        not in {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv"}
    ):
        raise invalid()
    return value


def document(metadata: object, body: str) -> dict:
    if (
        not isinstance(metadata, dict)
        or not {"name", "description"} <= set(metadata)
        or not set(metadata) <= {"name", "description", "platforms", "version"}
    ):
        raise invalid()
    name, description = metadata["name"], metadata["description"]
    platforms = metadata.get("platforms", list(PLATFORMS))
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,39}", name)
        or not isinstance(description, str)
        or not 1 <= len(description.strip()) <= 120
        or any(ord(c) < 32 for c in description)
    ):
        raise invalid()
    if (
        not isinstance(platforms, list)
        or not 1 <= len(platforms) <= 3
        or any(not isinstance(p, str) or p not in PLATFORMS for p in platforms)
        or len(set(platforms)) != len(platforms)
    ):
        raise invalid()
    if "version" in metadata and (not isinstance(metadata["version"], str) or not 1 <= len(metadata["version"]) <= 40):
        raise invalid()
    if (
        not 50 <= len(body) <= 24000
        or "\0" in body
        or any(len(re.findall(r"(?m)^## " + re.escape(section) + r"\s*$", body)) != 1 for section in SECTIONS)
    ):
        raise invalid()
    return {"name": name, "description": description.strip(), **{p: p in platforms for p in PLATFORMS}}


def patch_section(content: str, section: str, replacement: str) -> str:
    if (
        section not in SECTIONS
        or not isinstance(replacement, str)
        or not replacement.strip()
        or len(replacement) > 12000
        or re.search(r"(?m)^##?\s", replacement)
    ):
        raise invalid()
    pattern = re.compile(r"(?m)(^## " + re.escape(section) + r"\s*\n)(.*?)(?=^## |\Z)", re.S)
    if len(list(pattern.finditer(content))) != 1:
        raise invalid()
    return pattern.sub(lambda m: m[1] + replacement.strip() + "\n\n", content, count=1)


def manifest(files: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(
            [{k: r[k] for k in ("path", "sha256", "bytes")} for r in sorted(files, key=lambda r: r["path"])],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SkillPackage:
    metadata: dict
    files: tuple[tuple[str, bytes], ...]

    def validate(self) -> None:
        names = [file_path(name) for name, _ in self.files]
        if (
            not 1 <= len(names) <= MAX_FILES
            or "SKILL.md" not in names
            or len({n.casefold() for n in names}) != len(names)
            or sum(len(raw) for _, raw in self.files) > MAX_BYTES
        ):
            raise invalid()
        for name, raw in self.files:
            if not 1 <= len(raw) <= (128 * 1024 if name == "SKILL.md" else 64 * 1024):
                raise invalid()
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise invalid() from exc
            if "\0" in text:
                raise invalid()


def _spec(name: str, description: str, properties: dict, required: list[str], writes: bool = False) -> ToolSpec:
    return ToolSpec(
        name,
        description,
        cast(dict, {"type": "object", "properties": properties, "required": required, "additionalProperties": False}),
        writes,
        resource="skills",
    )


_ID = {"type": "string", "maxLength": 36}
_REVISION = {
    "type": "integer",
    "minimum": 1,
    "description": "Editing revision from skills_list or skill_view; never the content history number.",
}
_CONTENT = {"type": "string", "maxLength": 32000}
SKILL_SPECS = (
    _spec(
        "skills_list",
        "List this run's reviewed, platform-compatible skill revisions without loading instructions.",
        {},
        [],
    ),
    _spec(
        "skill_activate",
        "Load this captured skill's instructions for this run. Confers no tool permission or action approval.",
        {"skill_id": _ID, "revision_id": _ID},
        ["skill_id", "revision_id"],
    ),
    _spec(
        "skill_view",
        "Read a captured skill or declared text reference and activate its source binding for this run.",
        {"skill_id": _ID, "revision_id": _ID, "path": {"type": "string", "maxLength": 180}},
        ["skill_id", "revision_id"],
    ),
    _spec(
        "skill_history",
        "List revision metadata for a skill captured by this run; no historical instruction bodies.",
        {"skill_id": _ID},
        ["skill_id"],
    ),
    _spec(
        "skill_create",
        "Save complete SKILL.md as a new disabled skill. Requires approval; owner separately reviews and enables it.",
        {"content": _CONTENT},
        ["content"],
        True,
    ),
    _spec(
        "skill_rewrite",
        "Create a disabled skill revision, preserving references. Requires approval and observed head revision.",
        {"skill_id": _ID, "revision": _REVISION, "content": _CONTENT},
        ["skill_id", "revision", "content"],
        True,
    ),
    _spec(
        "skill_patch",
        "Replace one standard section and save a disabled revision. Existing run authority is revoked.",
        {
            "skill_id": _ID,
            "revision": _REVISION,
            "section": {"type": "string", "enum": list(SECTIONS)},
            "text": {"type": "string", "maxLength": 12000},
        },
        ["skill_id", "revision", "section", "text"],
        True,
    ),
    _spec(
        "skill_rollback",
        "Copy a historical skill revision into a new disabled revision; never auto-enables it.",
        {"skill_id": _ID, "revision": _REVISION, "revision_id": _ID},
        ["skill_id", "revision", "revision_id"],
        True,
    ),
    _spec(
        "skill_archive",
        "Archive a skill and revoke its authority, retaining all content and history.",
        {"skill_id": _ID, "revision": _REVISION},
        ["skill_id", "revision"],
        True,
    ),
    _spec(
        "skill_restore",
        "Restore an archived skill to disabled state for owner review; grants no authority.",
        {"skill_id": _ID, "revision": _REVISION},
        ["skill_id", "revision"],
        True,
    ),
)
