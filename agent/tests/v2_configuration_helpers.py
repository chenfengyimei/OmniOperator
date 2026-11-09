"""Configuration setup for isolated V2 tests; never enables a real model adapter."""


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
