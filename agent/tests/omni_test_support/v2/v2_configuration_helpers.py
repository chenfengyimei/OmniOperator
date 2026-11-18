"""Configuration setup for isolated V2 tests; never enables a real model adapter."""

from sqlalchemy import text


def add_model_headers_column(connection):
    """Frozen pre-v38/v40 baselines lack the newer model columns.

    Migration tests seed such a baseline with the *current* repository, whose model
    payload now carries extra_headers and whose settle_call writes the cache usage
    columns. Add them (only if absent) so seeding works; v0038/v0040 are idempotent,
    so the later upgrade to head still succeeds on the populated DB.
    """
    cols = {row[1] for row in connection.execute(text("PRAGMA table_info(model_options)"))}
    if "extra_headers" not in cols:
        connection.execute(
            text(
                "ALTER TABLE model_options ADD COLUMN extra_headers JSON NOT NULL DEFAULT '{}' "
                "CONSTRAINT ck_model_options_headers CHECK (json_valid(extra_headers) AND json_type(extra_headers)='object')"
            )
        )
    call_cols = {row[1] for row in connection.execute(text("PRAGMA table_info(model_calls)"))}
    if "cached_prompt_tokens" not in call_cols:
        connection.execute(
            text(
                "ALTER TABLE model_calls ADD COLUMN cached_prompt_tokens INTEGER "
                "CONSTRAINT ck_model_calls_cached_tokens CHECK (cached_prompt_tokens IS NULL OR cached_prompt_tokens>=0)"
            )
        )
    if "cache_creation_tokens" not in call_cols:
        connection.execute(
            text(
                "ALTER TABLE model_calls ADD COLUMN cache_creation_tokens INTEGER "
                "CONSTRAINT ck_model_calls_cache_created_tokens CHECK (cache_creation_tokens IS NULL OR cache_creation_tokens>=0)"
            )
        )


def model_uow(u, agent, model="fake", **options):
    settings = u.settings(agent)
    row = u.configuration_get(agent, settings["value"]["model_profile_id"])
    return u.configuration_put(
        agent, row["id"], row["name"], True, {**row["value"], "model": model, **options}, row["revision"]
    )


async def model_instance(instance, agent, model="fake", **options):
    settings = await instance.application.settings(agent)
    service = instance.configurations
    row = await service.get(agent, settings["value"]["model_profile_id"])
    return await service.save(
        agent, row["id"], str(row["name"]), True, {**row["value"], "model": model, **options}, row["revision"]
    )
