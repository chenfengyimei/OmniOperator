"""Explicit forward-only V2 schema routes bound to both immutable runtime inventories; no IO."""

from __future__ import annotations

import re
from typing import cast

from .contracts import BusinessError
from .service_deployment import deployment_fields, deployment_integer, deployment_sha

FORMAT = "omni-schema-upgrade-1"
PREFIX = "adapters/persistence/migrations/versions/"
MINIMUM_SOURCE = "v2_0025"


def invalid() -> BusinessError:
    return BusinessError(
        "release_schema_route_invalid", "Review an intact forward schema route in the target installation"
    )


def schema_upgrade(source: dict[str, object], target: dict[str, object], value: object) -> dict[str, object]:
    record = deployment_fields(value, {"format", "source", "target", "revisions"})
    old, new = source["schema_head"], target["schema_head"]
    if not isinstance(old, str) or not isinstance(new, str):
        raise invalid()
    revisions = record["revisions"]
    if not isinstance(revisions, list):
        raise invalid()
    items = cast(list[object], revisions)
    if (
        record["format"] != FORMAT
        or record["source"] != old
        or record["target"] != new
        or not MINIMUM_SOURCE <= old < new
        or not 2 <= len(items) <= 256
    ):
        raise invalid()
    inventories: list[dict[str, dict[str, object]]] = []
    for runtime in (source, target):
        files = runtime["files"]
        if not isinstance(files, list):
            raise invalid()
        inventories.append({cast(str, row["path"]): row for row in cast(list[dict[str, object]], files)})
    rows: list[dict[str, object]] = []
    previous: str | None = None
    source_seen = False
    for item in items:
        row = deployment_fields(item, {"revision", "parent", "path", "sha256", "bytes"})
        revision = row["revision"]
        if (
            not isinstance(revision, str)
            or re.fullmatch(r"v2_[0-9]{4}", revision) is None
            or row["parent"] != previous
            or (previous is None and revision != "v2_0001")
            or (previous is not None and revision <= previous)
            or row["path"] != PREFIX + "v" + revision.removeprefix("v2_") + ".py"
        ):
            raise invalid()
        file: dict[str, object] = {
            "path": cast(str, row["path"]),
            "sha256": deployment_sha(row["sha256"]),
            "bytes": deployment_integer(row["bytes"], 1, 1024 * 1024),
        }
        path = cast(str, row["path"])
        if inventories[1].get(path) != file or (not source_seen and inventories[0].get(path) != file):
            raise invalid()
        if revision == old:
            source_seen = True
        rows.append({"revision": revision, "parent": previous, **file})
        previous = revision
    expected = {cast(str, row["path"]) for row in rows}
    actual = {path for path in inventories[1] if path.startswith(PREFIX) and path != PREFIX + "__init__.py"}
    old_expected = {cast(str, row["path"]) for row in rows if cast(str, row["revision"]) <= old}
    old_actual = {path for path in inventories[0] if path.startswith(PREFIX) and path != PREFIX + "__init__.py"}
    if not source_seen or previous != new or expected != actual or old_expected != old_actual:
        raise invalid()
    result: dict[str, object] = {"format": FORMAT, "source": old, "target": new, "revisions": rows}
    if record != result:
        raise invalid()
    return result
