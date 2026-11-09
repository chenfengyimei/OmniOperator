"""One migration-directory and expected-head lookup for startup and offline readers."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from ...domain.contracts import BusinessError


def configuration() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def head(config: Config | None = None) -> str:
    result = ScriptDirectory.from_config(config or configuration()).get_current_head()
    if result is None:
        raise BusinessError("migration_missing", "The package has no database baseline", status=503)
    return result
