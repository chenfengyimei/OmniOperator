"""Per-account Bilibili risk pauses: typed gateway codes, owner resume, scoped gates; fake provider only."""

from omni_test_support.v2.bilibili import (
    BV as BV,
)
from omni_test_support.v2.bilibili import (
    MENTIONS as MENTIONS,
)
from omni_test_support.v2.bilibili import (
    URL as URL,
)
from omni_test_support.v2.bilibili import (
    BusinessError as BusinessError,
)
from omni_test_support.v2.bilibili import (
    Gateway as Gateway,
)
from omni_test_support.v2.bilibili import (
    Secrets as Secrets,
)
from omni_test_support.v2.bilibili import (
    asyncio as asyncio,
)
from omni_test_support.v2.bilibili import (
    opened as opened,
)
from omni_test_support.v2.bilibili import (
    pytest as pytest,
)
from omni_test_support.v2.bilibili import (
    s as s,
)
from omni_test_support.v2.bilibili import (
    select as select,
)
from omni_test_support.v2.bilibili import (
    update as update,
)
from omni_test_support.v2.bilibili import (
    uuid as uuid,
)
from omni_test_support.v2.bilibili_gateway import (
    CONFIG as CONFIG,
)
from omni_test_support.v2.bilibili_gateway import (
    BilibiliGateway as BilibiliGateway,
)
from omni_test_support.v2.bilibili_gateway import (
    Redactor as Redactor,
)
from omni_test_support.v2.bilibili_gateway import (
    Secrets as GatewaySecrets,
)
from omni_test_support.v2.bilibili_gateway import (
    authority as authority,
)
from omni_test_support.v2.bilibili_gateway import (
    httpx as httpx,
)

from omni.domain.bilibili import RISK_CODES, is_risk_code


@pytest.mark.parametrize("code", sorted(RISK_CODES))
def test_is_risk_code_accepts_exact_reference_codes(code):
    assert is_risk_code(code)


@pytest.mark.parametrize("value", [0, -400, 12016, "12015", True, 12015.0, None, [12015]])
def test_is_risk_code_rejects_everything_else(value):
    assert not is_risk_code(value)


def risk_factory(code, *, list_path=""):
    def handle(request):
        if list_path and request.url.path.endswith("/nav"):
            return httpx.Response(200, json={"code": 0, "data": {"isLogin": True, "mid": 456, "uname": "fixture"}})
        return httpx.Response(200, json={"code": code, "message": "risk"})

    return lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options)


@pytest.mark.parametrize("code", sorted(RISK_CODES))
def test_gateway_dict_reader_maps_risk_codes_to_typed_pause_signal(code):
    async def scenario():
        gateway = BilibiliGateway(GatewaySecrets(), Redactor(), authority, client_factory=risk_factory(code))
        with pytest.raises(BusinessError) as caught:
            await gateway.read(CONFIG, "account", "")
        assert caught.value.code == "bilibili_risk_code" and caught.value.status == 403
        assert caught.value.detail == code
        assert await gateway.close(1)

    asyncio.run(scenario())


def test_gateway_list_reader_maps_risk_codes_too():
    async def scenario():
        factory = risk_factory(12015, list_path="/x/tag/archive/tags")
        gateway = BilibiliGateway(GatewaySecrets(), Redactor(), authority, client_factory=factory)
        with pytest.raises(BusinessError) as caught:
            await gateway.read(CONFIG, "tags", BV)
        assert caught.value.code == "bilibili_risk_code" and caught.value.detail == 12015
        assert await gateway.close(1)

    asyncio.run(scenario())


def test_gateway_non_risk_rejection_keeps_existing_read_rejected_code():
    async def scenario():
        gateway = BilibiliGateway(GatewaySecrets(), Redactor(), authority, client_factory=risk_factory(-400))
        with pytest.raises(BusinessError) as caught:
            await gateway.read(CONFIG, "account", "")
        assert caught.value.code == "bilibili_read_rejected" and caught.value.detail is None
        assert await gateway.close(1)

    asyncio.run(scenario())


def test_risk_read_pauses_account_and_blocks_until_owner_resumes(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, message, gateway):
            await i._database.write(lambda u: u.credential_register("fixture/bili"))
            assert (await i.bilibili.read(agent, config["id"], "account"))["uid"] == "456"
            calls = []
            original = gateway.read

            async def risky(configuration, operation, target, limit=10):
                calls.append(operation)
                raise BusinessError("bilibili_risk_code", "risk", status=403, detail=12015)

            gateway.read = risky
            with pytest.raises(BusinessError) as caught:
                await i.bilibili.read(agent, config["id"], "account")
            assert caught.value.code == "bilibili_risk_paused" and caught.value.status == 403
            state = await i.bilibili.risk_state(agent, config["id"])
            assert state["paused"] and state["pause"]["code"] == 12015 and state["pause"]["resumed_at"] is None
            gateway.read = original
            with pytest.raises(BusinessError) as blocked:
                await i.bilibili.read(agent, config["id"], "account")
            assert blocked.value.code == "bilibili_risk_paused"
            assert calls == ["account"]
            with pytest.raises(BusinessError) as prepared:
                await i.bilibili.prepare(agent, message["id"], config["id"], "comment", URL, MENTIONS, "risk-plan")
            assert prepared.value.code == "bilibili_risk_paused" and prepared.value.status == 403
            resumed = await i.bilibili.resume(agent, config["id"])
            assert resumed["resumed_at"] is not None
            assert not (await i.bilibili.risk_state(agent, config["id"]))["paused"]
            assert (await i.bilibili.read(agent, config["id"], "account"))["uid"] == "456"
            plan = await i.bilibili.prepare(agent, message["id"], config["id"], "comment", URL, MENTIONS, "risk-plan")
            assert plan["kind"] == "comment" and plan["deliveries"]

    asyncio.run(scenario())


def test_resume_without_active_pause_is_a_conflict(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, _gateway):
            assert await i.bilibili.risk_state(agent, config["id"]) == {"paused": False, "pause": None}
            with pytest.raises(BusinessError) as caught:
                await i.bilibili.resume(agent, config["id"])
            assert caught.value.code == "bilibili_risk_not_paused" and caught.value.status == 409
            await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], -105))
            assert (await i.bilibili.resume(agent, config["id"]))["resumed_at"] is not None
            with pytest.raises(BusinessError) as again:
                await i.bilibili.resume(agent, config["id"])
            assert again.value.code == "bilibili_risk_not_paused"

    asyncio.run(scenario())


def test_repause_after_resume_reuses_row_and_keeps_identity(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, _gateway):
            first = await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], 12015))
            await i.bilibili.resume(agent, config["id"])
            second = await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], -101))
            assert second["id"] == first["id"] and second["created_at"] == first["created_at"]
            assert second["config_id"] == first["config_id"]
            assert second["code"] == -101 and second["resumed_at"] is None
            rows = await i._database.read(
                lambda u: list(
                    u.connection.execute(
                        select(s.bilibili_risk_pauses).where(s.bilibili_risk_pauses.c.agent_id == agent)
                    ).mappings()
                )
            )
            assert len(rows) == 1

    asyncio.run(scenario())


def test_pause_gate_is_scoped_to_the_paused_configuration(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, _gateway):
            await i._database.write(lambda u: u.credential_register("fixture/bili"))
            other = await i.configurations.create(
                agent,
                "channel",
                "Bili second",
                True,
                {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            )
            await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], 401))
            assert (await i.bilibili.risk_state(agent, other["id"]))["paused"] is False
            assert (await i.bilibili.read(agent, other["id"], "account"))["uid"] == "456"
            with pytest.raises(BusinessError):
                await i.bilibili.risk_state(agent, str(uuid.uuid4()))

    asyncio.run(scenario())


def test_risk_rows_are_retained_and_identity_is_immutable(tmp_path):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, _gateway):
            row = await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], 12015))
            with pytest.raises(BusinessError) as deleted:
                await i._database.write(lambda u: u.connection.execute(s.bilibili_risk_pauses.delete()))
            assert deleted.value.code == "data_constraint"
            with pytest.raises(BusinessError) as moved:
                await i._database.write(
                    lambda u: u.connection.execute(
                        update(s.bilibili_risk_pauses)
                        .where(s.bilibili_risk_pauses.c.id == row["id"])
                        .values(config_id=str(uuid.uuid4()))
                    )
                )
            assert moved.value.code == "data_constraint"
            assert (await i.bilibili.risk_state(agent, config["id"]))["pause"]["id"] == row["id"]

    asyncio.run(scenario())


@pytest.mark.parametrize("code", [-400, 0, "12015", True])
def test_repository_rejects_unknown_risk_codes(tmp_path, code):
    async def scenario():
        async with opened(tmp_path) as (i, agent, config, _profile, _message, _gateway):
            with pytest.raises(BusinessError) as caught:
                await i._database.write(lambda u: u.bilibili_risk_pause(agent, config["id"], code))
            assert caught.value.code == "bilibili_risk_invalid" and caught.value.status == 422
            assert await i.bilibili.risk_state(agent, config["id"]) == {"paused": False, "pause": None}

    asyncio.run(scenario())


def test_v0046_upgrade_creates_retained_risk_table_and_refuses_downgrade(tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from omni.adapters.persistence.migration_config import configuration
    from omni.adapters.persistence.sqlalchemy_access import database_engine

    engine = database_engine(tmp_path / "omni.db")
    try:
        with engine.connect() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "v2_0045")
            assert "bilibili_risk_pauses" not in inspect(connection).get_table_names()
            command.upgrade(config, "v2_0046")
            command.upgrade(config, "v2_0046")
            assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "v2_0046"
            reflection = inspect(connection)
            table = s.metadata.tables["bilibili_risk_pauses"]
            assert {c["name"]: c["nullable"] for c in reflection.get_columns("bilibili_risk_pauses")} == {
                c.name: c.nullable for c in table.c
            }
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='bilibili_risk_pauses'"
                )
            }
            assert triggers == {"bilibili_risk_identity", "bilibili_risk_keep"}
            with pytest.raises(RuntimeError):
                command.downgrade(config, "v2_0045")
            connection.commit()
    finally:
        engine.dispose()


def test_risk_http_state_pause_and_owner_resume(tmp_path):
    from fastapi.testclient import TestClient

    from omni.composition.http import http_app

    class RiskGateway(Gateway):
        def __init__(self):
            super().__init__()
            self.risk = False

        async def read(self, configuration, operation, target, limit=10):
            if self.risk:
                raise BusinessError("bilibili_risk_code", "risk", status=403, detail=-105)
            return await super().read(configuration, operation, target, limit)

    gateway = RiskGateway()
    with TestClient(http_app(tmp_path, bilibili_backend=gateway, credential_backend=Secrets())) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/bilibili/risk/{uuid.uuid4()}").status_code == 401
        client.headers["Authorization"] = "Bearer " + (tmp_path / "run/owner.token").read_text()
        agent = client.get("/api/v1/agents").json()["items"][0]["id"]
        base = f"/api/v1/agents/{agent}"
        config = client.post(
            base + "/configurations",
            json={
                "kind": "channel",
                "name": "Bili fixture",
                "enabled": True,
                "value": {"driver": "bilibili", "account": "456", "credential_ref": "fixture/bili"},
            },
        ).json()
        assert client.post("/api/v1/credentials/register", json={"reference": "fixture/bili"}).status_code == 200
        risk_path = base + "/bilibili/risk/" + config["id"]
        read_body = {"config_id": config["id"], "operation": "account"}
        assert client.get(risk_path).json() == {"paused": False, "pause": None}
        assert client.post(risk_path + "/resume").status_code == 409
        gateway.risk = True
        paused = client.post(base + "/bilibili/read", json=read_body)
        assert paused.status_code == 403 and paused.json()["error"]["code"] == "bilibili_risk_paused"
        state = client.get(risk_path).json()
        assert state["paused"] and state["pause"]["code"] == -105
        gateway.risk = False
        still = client.post(base + "/bilibili/read", json=read_body)
        assert still.status_code == 403 and still.json()["error"]["code"] == "bilibili_risk_paused"
        assert client.post(risk_path + "/resume").status_code == 200
        assert client.get(risk_path).json()["paused"] is False
        assert client.post(base + "/bilibili/read", json=read_body).status_code == 200
        assert client.post(risk_path + "/resume").status_code == 409
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/bilibili/risk/{config['id']}").status_code == 404
