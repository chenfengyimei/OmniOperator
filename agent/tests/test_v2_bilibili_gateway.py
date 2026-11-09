"""Async reads and real gateway orchestration with fake HTTP/worker ports; no Bilibili account."""

import asyncio
import json

import httpx
import pytest
from test_v2_bilibili import BV, FORM, MENTIONS, TEXT, URL

from omni.adapters.bilibili import BilibiliGateway
from omni.adapters.diagnostics import Redactor
from omni.domain.contracts import ActionResult, BusinessError, Effect, Outcome
from omni.domain.delivery import OutboundMessage, text_fingerprint

CONFIG = {"driver": "bilibili", "account": "456", "endpoint": "", "credential_ref": "fixture/cookies"}


class Secrets:
    async def get(self, reference):
        return json.dumps({"SESSDATA": "cookie-canary-value", "bili_jct": "csrf-canary-value", "DedeUserID": "456"})


def factory(*, account=456, malformed=False, malformed_comments=False):
    calls = []

    def handle(request):
        calls.append(request)
        if malformed:
            return httpx.Response(200, text="not-json", headers={"content-type": "application/json"})
        if request.url.path.endswith("/nav"):
            data = {"isLogin": True, "mid": account, "uname": "fixture"}
        elif request.url.path.endswith("/view"):
            data = {
                "bvid": BV,
                "aid": 123,
                "cid": 789,
                "title": "fixture",
                "owner": {"name": "author"},
                "desc": "description",
                "duration": 4,
            }
        else:
            data = {
                "replies": [{"rpid": 99, "mid": 222, "member": {"uname": "Alice"}, "content": {"message": "comment"}}]
            }
        if malformed_comments and request.url.path == "/x/v2/reply":
            data = {"replies": ["invalid record"]}
        return httpx.Response(200, json={"code": 0, "data": data})

    return lambda **options: httpx.AsyncClient(transport=httpx.MockTransport(handle), **options), calls


class Worker:
    def __init__(self, *, unknown=False):
        self.calls = []
        self.closed = False
        self.unknown = unknown

    async def invoke(self, name, args, *, before_dispatch, **kwargs):
        try:
            await before_dispatch()
        except BusinessError:
            return ActionResult(Outcome.DENIED, "Revoked before IPC", error_code="bilibili_dispatch_revoked")
        self.calls.append((name, args))
        if name != "bilibili_commit":
            return ActionResult(Outcome.OK, "fixture staged", data={"nonce": "fixture"})
        if self.unknown:
            return ActionResult(Outcome.UNKNOWN, "fixture disconnected", Effect.UNKNOWN)
        return ActionResult(
            Outcome.OK,
            "fixture verified",
            Effect.APPLIED,
            data={
                "record_id": "99",
                "account": "456",
                "target": URL,
                "body_hash": text_fingerprint(TEXT),
                "mentions": ["222"],
            },
        )

    async def close(self, timeout_s):
        self.closed = True
        return True


def message():
    return OutboundMessage("delivery", "attempt", "token", "a" * 64, "456", "bilibili:plan", TEXT, CONFIG)


async def authority(message):
    return {
        "kind": "comment",
        "target": URL,
        "target_key": BV,
        "form": FORM,
        "mentions": MENTIONS,
        "authority": "generation",
        "agent_id": "agent",
        "run_id": "run",
        "session_id": "session",
        "policy_revision": 1,
    }


@pytest.mark.parametrize(
    "operation,target", [("account", ""), ("video", BV), ("comments", BV), ("replies", BV + ":99")]
)
def test_bounded_read_checks_account_and_preserves_only_business_fields(operation, target):
    async def scenario():
        client, calls = factory()
        redactor = Redactor()
        gateway = BilibiliGateway(Secrets(), redactor, authority, client_factory=client)
        result = await gateway.read(CONFIG, operation, target)
        assert result and all(r.method == "GET" and r.url.host == "api.bilibili.com" for r in calls)
        assert "cookie-canary-value" not in json.dumps(result)
        assert "cookie-canary-value" not in redactor.text("cookie-canary-value")
        assert await gateway.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["identity", "malformed"])
def test_bad_provider_or_identity_never_returns_an_empty_success(case):
    async def scenario():
        client, _ = factory(account=999 if case == "identity" else 456, malformed=case == "malformed")
        gateway = BilibiliGateway(Secrets(), Redactor(), authority, client_factory=client)
        with pytest.raises(BusinessError):
            await gateway.read(CONFIG, "video", BV)
        assert await gateway.close(1)

    asyncio.run(scenario())


def test_malformed_nested_comments_cannot_be_reported_as_empty_success():
    async def scenario():
        client, _ = factory(malformed_comments=True)
        gateway = BilibiliGateway(Secrets(), Redactor(), authority, client_factory=client)
        with pytest.raises(BusinessError) as error:
            await gateway.read(CONFIG, "comments", BV)
        assert error.value.code == "bilibili_response_invalid"
        assert await gateway.close(1)

    asyncio.run(scenario())


@pytest.mark.parametrize("unknown", [False, True])
def test_publication_gateway_rechecks_authority_and_reclaims_actual_worker(unknown):
    async def scenario():
        client, calls = factory()
        worker = Worker(unknown=unknown)
        gateway = BilibiliGateway(
            Secrets(), Redactor(), authority, client_factory=client, worker_factory=lambda: worker
        )
        result = await gateway.send(message())
        assert result.status == ("unknown" if unknown else "sent")
        assert [name for name, _ in worker.calls] == ["bilibili_open", "bilibili_fill", "bilibili_commit"]
        assert all(r.method == "GET" for r in calls)
        assert worker.closed and await gateway.close(1)

    asyncio.run(scenario())


def test_revocation_after_fill_cannot_dispatch_the_final_click():
    async def scenario():
        client, _ = factory()
        worker = Worker()

        async def revoked(m):
            if len(worker.calls) >= 2:
                raise BusinessError("bilibili_dispatch_revoked", "owner revoked")
            return await authority(m)

        gateway = BilibiliGateway(Secrets(), Redactor(), revoked, client_factory=client, worker_factory=lambda: worker)
        result = await gateway.send(message())
        assert result.status == "not_sent"
        assert all(name != "bilibili_commit" for name, _ in worker.calls)
        assert worker.closed and await gateway.close(1)

    asyncio.run(scenario())


def test_real_managed_worker_and_gateway_complete_with_only_locally_fulfilled_pages(tmp_path):
    import sys

    from test_v2_bilibili import Model, terminal
    from test_v2_bilibili import Secrets as OwnerSecrets
    from test_v2_bilibili_worker import HTML, server_record
    from v2_configuration_helpers import model_instance

    from omni.adapters.workers.client import WorkerClient
    from omni.composition.instance import open_instance

    script = tmp_path / "fixture_browser.py"
    script.write_text(
        """import asyncio
from omni.adapters.workers import bilibili as native
original=native.PublicationGate.route
records=[]
async def routed(self,route):
    if route.request.url=="""
        + repr(URL)
        + """:
        await route.fulfill(status=200,body="""
        + repr(HTML)
        + ".replace('<!--SERVER_RECORDS-->',''.join(records))"
        + """,content_type='text/html')
        return
    class Controlled:
        request=route.request
        async def continue_(self):
            records.append("""
        + repr(server_record())
        + """)
            await route.fulfill(status=200,json={'code':0,'data':{'rpid':99}},headers={'Access-Control-Allow-Origin':'https://www.bilibili.com'})
        async def abort(self):
            await route.abort()
    await original(self,Controlled())
native.PublicationGate.route=routed
asyncio.run(native.serve())
""",
        encoding="utf-8",
    )

    async def scenario():
        holder = {}
        client, _ = factory()
        workers = []

        async def authorized(m):
            return await holder["instance"]._database.read(lambda u: u.bilibili_outbound_authorized(m))

        def create_worker():
            w = WorkerClient(
                "bilibili",
                tool_writes={"bilibili_open": False, "bilibili_fill": True, "bilibili_commit": True},
                argv=[sys.executable, str(script)],
                forbid_artifacts=True,
            )
            workers.append(w)
            return w

        gateway = BilibiliGateway(
            Secrets(), Redactor(), authorized, client_factory=client, worker_factory=create_worker
        )
        instance = await open_instance(
            tmp_path / "home",
            model=Model(),
            capabilities=(),
            credential_backend=OwnerSecrets(),
            bilibili_backend=gateway,
        )
        holder["instance"] = instance
        try:
            agent = (await instance.application.query("agents", ""))[0]["id"]
            await model_instance(instance, agent)
            config = await instance.configurations.create(agent, "channel", "Bili fake UI", True, CONFIG)
            await instance.bilibili.save_profile(agent, config["id"], True, {"comment": FORM}, 0)
            turn = await instance.application.submit_turn(agent, None, "source", "fixture draft")
            assert (await terminal(instance, agent, turn["run_id"]))["state"] == "succeeded"
            source = (await instance.application.messages(agent, turn["session_id"]))[-1]
            plan = await instance.bilibili.prepare(
                agent, source["id"], config["id"], "comment", URL, MENTIONS, "managed"
            )
            row = plan["deliveries"][0]
            await instance.deliveries.decide(agent, row["id"], True, row["revision"], row["fingerprint"])
            async with asyncio.timeout(30):
                while True:
                    current = await instance.deliveries.get(agent, row["id"])
                    if current["state"] in {"sent", "not_sent", "unknown"}:
                        break
                    await asyncio.sleep(0.05)
            assert current["state"] == "sent", current["error_code"]
            assert len(workers) == 1 and workers[0].health()["closed"] and not workers[0].health()["running"]
        finally:
            assert (await instance.close())["complete"]

    asyncio.run(scenario())
