"""Configuration setup for isolated V2 tests; never enables a real model adapter."""

from sqlalchemy import text


def add_model_headers_column(connection):
    """Frozen pre-v38 baselines lack model_options.extra_headers.

    Migration tests seed such a baseline with the *current* repository, whose model
    payload now carries extra_headers. Add the column (only if absent) so seeding works;
    v0038 is idempotent, so the later upgrade to head still succeeds on the populated DB.
    """
    cols = {row[1] for row in connection.execute(text("PRAGMA table_info(model_options)"))}
    if "extra_headers" not in cols:
        connection.execute(
            text(
                "ALTER TABLE model_options ADD COLUMN extra_headers JSON NOT NULL DEFAULT '{}' "
                "CONSTRAINT ck_model_options_headers CHECK (json_valid(extra_headers) AND json_type(extra_headers)='object')"
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
