"""Credential authority state and immutable review. Frozen SQL; no V1 credential import."""

import datetime as dt
import hashlib
import json
import re
import uuid
from typing import Any

from alembic import op
from sqlalchemy.engine import Connection

revision = "v2_0010"
down_revision = "v2_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    db = op.get_bind()
    sequence = (
        db.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='instance_audit'").scalar_one_or_none() or 0
    )
    op.execute("ALTER TABLE instance_audit RENAME TO _v10_instance_audit")
    for statement in TABLES:
        op.execute(statement)
    op.execute(
        "INSERT INTO instance_audit(sequence,id,created_at,owner_id,operation_id,kind,outcome,data) SELECT sequence,id,created_at,owner_id,operation_id,kind,outcome,data FROM _v10_instance_audit ORDER BY sequence"
    )
    op.execute("DROP TABLE _v10_instance_audit")
    current = db.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='instance_audit'").scalar_one_or_none()
    if current is None:
        db.exec_driver_sql("INSERT INTO sqlite_sequence(name,seq) VALUES('instance_audit',?)", (sequence,))
    elif current < sequence:
        db.exec_driver_sql("UPDATE sqlite_sequence SET seq=? WHERE name='instance_audit'", (sequence,))
    for statement in INDEXES:
        op.execute(statement)
    adopt_legacy(db)
    for statement in TRIGGERS:
        op.execute(statement)


def adopt_legacy(db: Connection) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")
    owner = db.exec_driver_sql("SELECT owner_id FROM instances WHERE slot=1").scalar_one_or_none()
    refs: dict[str, str] = {}

    def register(fingerprint: str, reference: str | None = None) -> str:
        if fingerprint in refs:
            return refs[fingerprint]
        if owner is None or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise RuntimeError("Legacy credential ownership or reference is invalid")
        identity = str(uuid.uuid4())
        db.exec_driver_sql(
            "INSERT INTO credential_references(id,created_at,owner_id,reference,reference_sha256,state,revision,authority_epoch,updated_at) VALUES(?,?,?,?,?,'unknown',1,1,?)",
            (identity, now, owner, reference, fingerprint, now),
        )
        refs[fingerprint] = identity
        return identity

    for table in ("model_options", "channel_options"):
        for (reference,) in db.exec_driver_sql(
            f"SELECT DISTINCT o.credential_ref FROM {table} o JOIN configuration_revisions r ON r.id=o.revision_id JOIN configurations c ON c.id=r.config_id AND c.agent_id=r.agent_id AND c.revision=r.revision WHERE c.deleted_at IS NULL AND o.credential_ref!=''"
        ):
            if not isinstance(reference, str) or not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,200}", reference):
                raise RuntimeError("Legacy credential reference is invalid")
            register(hashlib.sha256(reference.encode("utf-8")).hexdigest(), reference)

    for row in db.exec_driver_sql(
        "SELECT * FROM instance_audit WHERE kind='credential.requested' ORDER BY sequence"
    ).mappings():
        data: dict[str, Any] = json.loads(row["data"])
        fingerprint = str(data.get("reference_sha256", ""))
        credential = register(fingerprint)
        identity = str(row["operation_id"])
        if str(uuid.UUID(identity)) != identity:
            raise RuntimeError("Legacy credential operation identity is invalid")
        operation = data.get("operation")
        if operation not in ("set", "delete"):
            raise RuntimeError("Legacy credential operation is invalid")
        result = db.exec_driver_sql(
            "SELECT outcome FROM instance_audit WHERE operation_id=? AND kind='credential.resolved'", (identity,)
        ).scalar_one_or_none()
        state = result if result in ("applied", "not_applied", "unknown") else "unknown"
        boot = str(data.get("boot_id", ""))
        try:
            if str(uuid.UUID(boot)) != boot:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            boot = "00000000-0000-0000-0000-000000000000"
        db.exec_driver_sql(
            "INSERT INTO credential_operations(id,created_at,owner_id,credential_id,operation,state,prior_state,revision,authority_epoch,boot_id,execution_token,ended_at,error_code,legacy) VALUES(?,?,?,?,?,?,'unknown',1,1,?,?,?,'legacy_credential_authority_unverified',1)",
            (identity, row["created_at"], owner, credential, operation, state, boot, str(uuid.uuid4()), now),
        )
        db.exec_driver_sql("UPDATE credential_references SET current_operation_id=? WHERE id=?", (identity, credential))


def downgrade() -> None:
    raise RuntimeError("Restore a verified snapshot into a new home instead of downgrading")


TABLES = (
    "\nCREATE TABLE instance_audit (\n\tsequence INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, \n\tid VARCHAR(36) NOT NULL, \n\tcreated_at VARCHAR(40) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\toperation_id VARCHAR(36) NOT NULL, \n\tkind VARCHAR(40) NOT NULL, \n\toutcome VARCHAR(20) NOT NULL, \n\tdata JSON NOT NULL, \n\tCONSTRAINT uq_instance_audit_operation_id UNIQUE (operation_id, kind), \n\tCONSTRAINT ck_instance_audit_identities CHECK (length(id)=36 AND length(operation_id)=36), \n\tCONSTRAINT ck_instance_audit_event CHECK ((kind='credential.requested' AND outcome='requested') OR (kind='credential.resolved' AND outcome IN ('applied','not_applied','unknown')) OR (kind='credential.registered' AND outcome='registered') OR (kind='credential.reviewed' AND outcome IN ('allowed','revoked')) OR (kind='credential.recovered' AND outcome IN ('not_applied','unknown')) OR (kind='credential.restored' AND outcome='unknown')), \n\tCONSTRAINT uq_instance_audit_id UNIQUE (id), \n\tCONSTRAINT fk_instance_audit_owner_id_principals FOREIGN KEY(owner_id) REFERENCES principals (id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE credential_references (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_credential_references_uuid_length CHECK (length(id) = 36), \n\tcreated_at VARCHAR(40) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\treference VARCHAR(200), \n\treference_sha256 VARCHAR(64) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\trevision INTEGER DEFAULT '1' NOT NULL, \n\tauthority_epoch INTEGER DEFAULT '1' NOT NULL, \n\tcurrent_operation_id VARCHAR(36), \n\tupdated_at VARCHAR(40) NOT NULL, \n\tCONSTRAINT pk_credential_references PRIMARY KEY (id), \n\tCONSTRAINT uq_credential_references_id UNIQUE (id, owner_id), \n\tCONSTRAINT ck_credential_references_state CHECK (state IN ('ready','changing','unknown','revoked') AND revision>0 AND authority_epoch>0 AND length(reference_sha256)=64), \n\tCONSTRAINT ck_credential_references_reference CHECK (reference IS NULL OR length(reference) BETWEEN 1 AND 200), \n\tCONSTRAINT ck_credential_references_ready_reference CHECK (state!='ready' OR reference IS NOT NULL), \n\tCONSTRAINT ck_credential_references_changing_operation CHECK (state!='changing' OR current_operation_id IS NOT NULL), \n\tCONSTRAINT fk_credential_references_owner_id_principals FOREIGN KEY(owner_id) REFERENCES principals (id) ON DELETE RESTRICT, \n\tCONSTRAINT uq_credential_references_reference UNIQUE (reference), \n\tCONSTRAINT uq_credential_references_reference_sha256 UNIQUE (reference_sha256), \n\tCONSTRAINT fk_credential_references_current_operation_id_credential_operations FOREIGN KEY(current_operation_id, id, owner_id) REFERENCES credential_operations (id, credential_id, owner_id) ON DELETE RESTRICT\n)\n\n",
    "\nCREATE TABLE credential_operations (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_credential_operations_uuid_length CHECK (length(id) = 36), \n\tcreated_at VARCHAR(40) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\tcredential_id VARCHAR(36) NOT NULL, \n\toperation VARCHAR(20) NOT NULL, \n\tstate VARCHAR(20) NOT NULL, \n\tprior_state VARCHAR(20) NOT NULL, \n\trevision INTEGER DEFAULT '1' NOT NULL, \n\tauthority_epoch INTEGER NOT NULL, \n\tboot_id VARCHAR(36) NOT NULL, \n\texecution_token VARCHAR(36) NOT NULL, \n\tstarted_at VARCHAR(40), \n\tended_at VARCHAR(40), \n\terror_code VARCHAR(100) DEFAULT '' NOT NULL, \n\tlegacy BOOLEAN DEFAULT '0' NOT NULL, \n\tCONSTRAINT pk_credential_operations PRIMARY KEY (id), \n\tCONSTRAINT uq_credential_operations_id UNIQUE (id, credential_id, owner_id), \n\tCONSTRAINT fk_credential_operations_credential_id_credential_references FOREIGN KEY(credential_id, owner_id) REFERENCES credential_references (id, owner_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_credential_operations_state CHECK (operation IN ('set','delete','legacy') AND state IN ('prepared','running','applied','not_applied','unknown') AND prior_state IN ('ready','revoked','unknown') AND revision>0 AND authority_epoch>0 AND length(boot_id)=36 AND length(execution_token)=36 AND legacy IN (0,1)), \n\tCONSTRAINT ck_credential_operations_legacy_terminal CHECK (legacy=0 OR state IN ('applied','not_applied','unknown'))\n)\n\n",
    "\nCREATE TABLE credential_reviews (\n\tid VARCHAR(36) NOT NULL CONSTRAINT ck_credential_reviews_uuid_length CHECK (length(id) = 36), \n\tcreated_at VARCHAR(40) NOT NULL, \n\towner_id VARCHAR(36) NOT NULL, \n\tcredential_id VARCHAR(36) NOT NULL, \n\toperation_id VARCHAR(36), \n\treference_revision INTEGER NOT NULL, \n\tauthority_epoch INTEGER NOT NULL, \n\tfingerprint VARCHAR(64) NOT NULL, \n\tpolicy VARCHAR(40) NOT NULL, \n\tdecision VARCHAR(20) NOT NULL, \n\tevidence TEXT NOT NULL, \n\tCONSTRAINT pk_credential_reviews PRIMARY KEY (id), \n\tCONSTRAINT uq_credential_reviews_credential_id UNIQUE (credential_id, authority_epoch, reference_revision), \n\tCONSTRAINT fk_credential_reviews_credential_id_credential_references FOREIGN KEY(credential_id, owner_id) REFERENCES credential_references (id, owner_id) ON DELETE RESTRICT, \n\tCONSTRAINT fk_credential_reviews_operation_id_credential_operations FOREIGN KEY(operation_id, credential_id, owner_id) REFERENCES credential_operations (id, credential_id, owner_id) ON DELETE RESTRICT, \n\tCONSTRAINT ck_credential_reviews_review CHECK (reference_revision>0 AND authority_epoch>0 AND length(fingerprint)=64 AND policy='credential-authority-v1' AND decision IN ('allow_current','revoke') AND length(trim(evidence)) BETWEEN 1 AND 2000)\n)\n\n",
)

INDEXES = (
    "CREATE INDEX ix_credential_references_state ON credential_references (state)",
    "CREATE INDEX ix_credential_operations_state ON credential_operations (state, boot_id)",
)

TRIGGERS = (
    "CREATE TRIGGER credential_reference_identity BEFORE UPDATE ON credential_references WHEN new.id IS NOT old.id OR new.owner_id IS NOT old.owner_id OR new.reference_sha256 IS NOT old.reference_sha256 OR new.created_at IS NOT old.created_at OR (old.reference IS NOT NULL AND new.reference IS NOT old.reference) OR new.revision<=old.revision OR new.authority_epoch<old.authority_epoch BEGIN SELECT RAISE(ABORT,'credential identity and authority cannot rewind'); END",
    "CREATE TRIGGER credential_operation_identity BEFORE UPDATE ON credential_operations WHEN new.id IS NOT old.id OR new.credential_id IS NOT old.credential_id OR new.owner_id IS NOT old.owner_id OR new.created_at IS NOT old.created_at OR new.operation IS NOT old.operation OR new.boot_id IS NOT old.boot_id OR new.execution_token IS NOT old.execution_token OR new.authority_epoch IS NOT old.authority_epoch OR new.prior_state IS NOT old.prior_state OR new.legacy IS NOT old.legacy OR new.revision<=old.revision OR old.state IN ('applied','not_applied','unknown') OR (old.state='running' AND new.state='prepared') BEGIN SELECT RAISE(ABORT,'credential execution identity and terminal outcome are immutable'); END",
    "CREATE TRIGGER credential_dispatch_authority BEFORE UPDATE ON credential_operations WHEN new.state='running' AND NOT EXISTS(SELECT 1 FROM credential_references r WHERE r.id=new.credential_id AND r.owner_id=new.owner_id AND r.current_operation_id=new.id AND r.state='changing' AND r.authority_epoch=new.authority_epoch) BEGIN SELECT RAISE(ABORT,'credential dispatch requires current authority'); END",
    "CREATE TRIGGER credential_review_update BEFORE UPDATE ON credential_reviews BEGIN SELECT RAISE(ABORT,'credential review history is immutable'); END",
    "CREATE TRIGGER credential_review_delete BEFORE DELETE ON credential_reviews BEGIN SELECT RAISE(ABORT,'credential review history must remain'); END",
)
