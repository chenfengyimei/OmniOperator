"""Reusable people fixtures, extracted without collecting test cases."""

import asyncio
import json
import subprocess
import sys
import uuid

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, insert, select, update

from omni.adapters.persistence import schema as s
from omni.adapters.persistence.database import Database
from omni.adapters.persistence.memory_repository import MemoryRepositories
from omni.adapters.persistence.migration_config import head as migration_head
from omni.adapters.persistence.migrations.versions.v0001 import SQL
from omni.adapters.persistence.migrations.versions.v0002 import upgrade as objects_upgrade
from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.adapters.platform.runtime.paths import InstancePaths
from omni.adapters.primitives import UUIDs
from omni.application.engine import Capability
from omni.application.memory_tools import MemoryCapabilities
from omni.application.people import PeopleService
from omni.composition.instance import open_instance
from omni.domain.contracts import (
    ActionResult,
    BusinessError,
    Effect,
    Invocation,
    InvocationContext,
    ModelReply,
    Outcome,
    ToolSpec,
)
from omni_test_support.v2.objects import Clock, opened
from omni_test_support.v2.v2_configuration_helpers import model_instance


def legacy_database(home):
    paths = InstancePaths.resolve(home)
    paths.initialize()
    identities = {
        name: str(uuid.uuid4())
        for name in (
            "owner",
            "instance",
            "agent",
            "session",
            "message",
            "run",
            "memory",
            "version",
            "anchor",
            "snapshot",
        )
    }
    now = Clock().now()
    engine = create_engine("sqlite+pysqlite:///" + str(paths.database))
    with engine.begin() as connection:
        for statement in SQL:
            connection.exec_driver_sql(statement)
        with Operations.context(MigrationContext.configure(connection)):
            objects_upgrade()
        connection.exec_driver_sql("CREATE TABLE alembic_version(version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO alembic_version VALUES('v2_0002')")
        connection.exec_driver_sql(
            "INSERT INTO principals(id,created_at,role,name) VALUES(?,?,'owner','Owner')", (identities["owner"], now)
        )
        connection.exec_driver_sql(
            "INSERT INTO instances(id,created_at,slot,owner_id) VALUES(?,?,1,?)",
            (identities["instance"], now, identities["owner"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO agents(id,created_at,name,enabled,revision) VALUES(?,?,'Agent',1,1)",
            (identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO sessions(id,agent_id,created_at,title,revision,next_seq) VALUES(?,?,?,'old',1,1)",
            (identities["session"], identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO messages(id,agent_id,created_at,session_id,seq,role,content) VALUES(?,?,?,?,1,'user','historical quote')",
            (identities["message"], identities["agent"], now, identities["session"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO runs(id,agent_id,created_at,session_id,input_message_id,state,config_revision,config_snapshot,last_seq,error_code) VALUES(?,?,?,?,?,'succeeded',1,'{}',0,'')",
            (identities["run"], identities["agent"], now, identities["session"], identities["message"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_entries(id,agent_id,created_at,kind,content,revision) VALUES(?,?,?,'fact','historical memory',1)",
            (identities["memory"], identities["agent"], now),
        )
        connection.exec_driver_sql(
            "INSERT INTO memory_revisions(id,agent_id,created_at,memory_id,revision,content) VALUES(?,?,?,?,1,'historical memory')",
            (identities["version"], identities["agent"], now, identities["memory"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO source_anchors(id,agent_id,created_at,memory_id,message_id,quote) VALUES(?,?,?,?,?,'historical quote')",
            (identities["anchor"], identities["agent"], now, identities["memory"], identities["message"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO context_snapshots(id,agent_id,created_at,run_id,message_ids,memory_ids) VALUES(?,?,?,?,?,?)",
            (
                identities["snapshot"],
                identities["agent"],
                now,
                identities["run"],
                json.dumps([identities["message"]]),
                json.dumps([identities["memory"]]),
            ),
        )
    engine.dispose()
    return paths, identities


__all__ = [
    "ActionResult",
    "BusinessError",
    "Capability",
    "Clock",
    "Database",
    "Effect",
    "InstancePaths",
    "Invocation",
    "InvocationContext",
    "MemoryCapabilities",
    "MemoryRepositories",
    "MigrationContext",
    "ModelReply",
    "Operations",
    "Outcome",
    "PeopleService",
    "SQL",
    "ToolSpec",
    "UUIDs",
    "asyncio",
    "create_engine",
    "delete",
    "insert",
    "json",
    "legacy_database",
    "migration_head",
    "model_instance",
    "objects_upgrade",
    "open_instance",
    "opened",
    "pytest",
    "s",
    "select",
    "sqlite_connection",
    "subprocess",
    "sys",
    "update",
    "uuid",
]
