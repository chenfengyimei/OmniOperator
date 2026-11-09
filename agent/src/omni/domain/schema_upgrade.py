"""Explicit forward-only V2 schema routes bound to both immutable runtime inventories; no IO."""

from __future__ import annotations

import re

from .contracts import BusinessError
from .service_deployment import _integer, _object, _sha

FORMAT = "omni-schema-upgrade-1"
PREFIX = "adapters/persistence/migrations/versions/"
MINIMUM_SOURCE = "v2_0025"


def invalid() -> BusinessError:
    return BusinessError(
        "release_schema_route_invalid", "Review an intact forward schema route in the target installation"
    )


def schema_upgrade(source: dict, target: dict, value: object) -> dict:
    record = _object(value, {"format", "source", "target", "revisions"})
    old, new = source["schema_head"], target["schema_head"]
    revisions = record["revisions"]
    if (
        record["format"] != FORMAT
        or record["source"] != old
        or record["target"] != new
        or not MINIMUM_SOURCE <= old < new
        or not isinstance(revisions, list)
        or not 2 <= len(revisions) <= 256
    ):
        raise invalid()
    inventories = [{row["path"]: row for row in runtime["files"]} for runtime in (source, target)]
    rows, previous, source_seen = [], None, False
    for item in revisions:
        row = _object(item, {"revision", "parent", "path", "sha256", "bytes"})
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
        file = {"path": row["path"], "sha256": _sha(row["sha256"]), "bytes": _integer(row["bytes"], 1, 1024 * 1024)}
        if inventories[1].get(row["path"]) != file or (not source_seen and inventories[0].get(row["path"]) != file):
            raise invalid()
        if revision == old:
            source_seen = True
        rows.append({"revision": revision, "parent": previous, **file})
        previous = revision
    expected = {row["path"] for row in rows}
    actual = {path for path in inventories[1] if path.startswith(PREFIX) and path != PREFIX + "__init__.py"}
    old_expected = {row["path"] for row in rows if row["revision"] <= old}
    old_actual = {path for path in inventories[0] if path.startswith(PREFIX) and path != PREFIX + "__init__.py"}
    if not source_seen or previous != new or expected != actual or old_expected != old_actual:
        raise invalid()
    result = {"format": FORMAT, "source": old, "target": new, "revisions": rows}
    if record != result:
        raise invalid()
    return result
