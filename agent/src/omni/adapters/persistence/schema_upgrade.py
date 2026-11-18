"""Reviewed migration catalog and transactional upgrades only in a locked, fenced release target."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

from alembic import command
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util.exc import CommandError

from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.platform.services.deployment_files import (
    CurrentDeploymentFiles,
    json_bytes,
    no_links,
    publish_json,
    read_file,
)

from ...domain.contracts import BusinessError
from ...domain.releases import ReleasePlan, digest
from ...domain.schema_upgrade import FORMAT, schema_upgrade
from .deployed_instance import verify_deployed_instance
from .history_sequence import allocate_history
from .maintenance import fingerprint, inspect_database
from .migration_config import configuration
from .object_inventory import verify_objects
from .sqlalchemy_access import database_engine


def route(source: dict[str, object], target: dict[str, object]) -> dict[str, object]:
    try:
        return _route(source, target)
    except (CommandError, RevisionError, KeyError, ImportError, OSError, ValueError) as exc:
        raise BusinessError(
            "release_schema_catalog_invalid", "Repair the target migration catalog before any database writes"
        ) from exc


def _route(source: dict[str, object], target: dict[str, object]) -> dict[str, object]:
    config = configuration()
    scripts = ScriptDirectory.from_config(config)
    if scripts.get_current_head() != target["schema_head"]:
        raise BusinessError("release_schema_program_changed", "Use the reviewed target migration installation")
    root = Path(__file__).resolve().parents[2]
    revisions: list[dict[str, object]] = []
    for script in reversed(list(scripts.walk_revisions())):
        path = Path(script.path)
        no_links(path)
        if not path.is_relative_to(root) or script.dependencies is not None:
            raise BusinessError("release_schema_route_invalid", "Use one complete linear migration history")
        data = read_file(path, 1024 * 1024, single=True)
        revisions.append(
            {
                "revision": script.revision,
                "parent": script.down_revision,
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return schema_upgrade(
        source,
        target,
        {
            "format": FORMAT,
            "source": source["schema_head"],
            "target": target["schema_head"],
            "revisions": revisions,
        },
    )


def upgrade_target(plan: ReleasePlan, paths: InstancePaths, restore_report_sha256: str) -> str:
    transition = plan["schema_upgrade"]
    if transition is None or route(plan["source"]["runtime"], plan["target"]["runtime"]) != transition:
        raise BusinessError("release_schema_route_changed", "Preserve the staged target and review the migration route")
    verify_deployed_instance(
        paths.database, plan["source"]["request"]["instance_id"], schema_version=transition["source"]
    )
    before = inspect_database(paths.database)
    if before["integrity"] != "ok" or before["foreign_key_errors"]:
        raise BusinessError("release_schema_integrity_failed", "The staged source snapshot failed verification")
    publisher = CurrentDeploymentFiles()
    intent = paths.control / "schema-upgrade-intent.json"
    report = paths.control / "schema-upgrade-report.json"
    for path in (intent, report):
        no_links(path)
        if path.exists():
            raise BusinessError(
                "release_schema_already_attempted", "Preserve interrupted migration; restore into another new home"
            )
    binding = {
        "format": FORMAT,
        "release_id": plan["id"],
        "release_sha256": plan["sha256"],
        "route_sha256": digest(transition),
        "source": transition["source"],
        "target": transition["target"],
        "restore_report_sha256": restore_report_sha256,
    }
    # Persist before opening the write transaction. A missing result after a
    # crash never authorizes a second attempt against partially migrated data.
    publish_json(publisher, intent, json_bytes(binding))
    engine = database_engine(paths.database)
    try:
        with engine.connect() as connection:
            connection.info["omni_write"] = True
            with connection.begin():
                if connection.exec_driver_sql("SELECT version_num FROM alembic_version").all() != [
                    (transition["source"],)
                ]:
                    raise BusinessError("release_schema_source_changed", "The migration source schema changed")
                if connection.exec_driver_sql("SELECT id FROM instances WHERE slot=1").all() != [
                    (plan["source"]["request"]["instance_id"],)
                ]:
                    raise BusinessError("service_instance_changed", "The staged instance identity changed")
                config = configuration()
                config.attributes["connection"] = connection
                command.upgrade(config, transition["target"])
                if (
                    connection.exec_driver_sql("SELECT version_num FROM alembic_version").all()
                    != [(transition["target"],)]
                    or connection.exec_driver_sql("PRAGMA integrity_check").all() != [("ok",)]
                    or connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                ):
                    raise BusinessError("release_schema_integrity_failed", "The migrated target failed validation")
                now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
                for (agent_id,) in connection.exec_driver_sql("SELECT id FROM agents").all():
                    connection.exec_driver_sql(
                        "INSERT INTO audit(sequence,id,agent_id,created_at,kind,data) VALUES(?,?,?,?,'schema.upgraded',?)",
                        (allocate_history(connection, "audit"), str(uuid.uuid4()), agent_id, now, json.dumps(binding)),
                    )
    finally:
        engine.dispose()
    verify_deployed_instance(
        paths.database, plan["target"]["request"]["instance_id"], schema_version=transition["target"]
    )
    after = inspect_database(paths.database)
    objects = verify_objects(paths.database, paths.objects)
    publish_json(
        publisher,
        report,
        json_bytes(
            {
                **binding,
                "complete": True,
                "integrity": after["integrity"],
                "foreign_key_errors": after["foreign_key_errors"],
                "objects": len(objects),
                "credentials_restored": False,
                "execution_quarantined": True,
            }
        ),
    )
    return fingerprint(report)
