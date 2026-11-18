"""Actual browser form/chip/receipt path on isolated HTML; every API response is locally fulfilled."""

from omni_test_support.v2.bilibili_worker import (
    BV as BV,
)
from omni_test_support.v2.bilibili_worker import (
    FORM as FORM,
)
from omni_test_support.v2.bilibili_worker import (
    HTML as HTML,
)
from omni_test_support.v2.bilibili_worker import (
    MENTIONS as MENTIONS,
)
from omni_test_support.v2.bilibili_worker import (
    TEXT as TEXT,
)
from omni_test_support.v2.bilibili_worker import (
    URL as URL,
)
from omni_test_support.v2.bilibili_worker import (
    Effect as Effect,
)
from omni_test_support.v2.bilibili_worker import (
    Outcome as Outcome,
)
from omni_test_support.v2.bilibili_worker import (
    PageForms as PageForms,
)
from omni_test_support.v2.bilibili_worker import (
    PublicationGate as PublicationGate,
)
from omni_test_support.v2.bilibili_worker import (
    SimpleNamespace as SimpleNamespace,
)
from omni_test_support.v2.bilibili_worker import (
    asyncio as asyncio,
)
from omni_test_support.v2.bilibili_worker import (
    forms_value as forms_value,
)
from omni_test_support.v2.bilibili_worker import (
    html as html,
)
from omni_test_support.v2.bilibili_worker import (
    json as json,
)
from omni_test_support.v2.bilibili_worker import (
    server_record as server_record,
)
from omni_test_support.v2.bilibili_worker import (
    spec as spec,
)
from omni_test_support.v2.bilibili_worker import (
    sys as sys,
)
from omni_test_support.v2.bilibili_worker import (
    urlencode as urlencode,
)
from omni_test_support.v2.bilibili_worker import (
    uuid as uuid,
)


def test_real_worker_rejects_readonly_declaration_for_commit_before_browser_start():
    from omni.adapters.workers.client import WorkerClient
    from omni.domain.contracts import InvocationContext

    async def scenario():
        worker = WorkerClient(
            "bilibili",
            tool_writes={"bilibili_commit": False},
            argv=[sys.executable, "-m", "omni.adapters.workers.bilibili"],
            forbid_artifacts=True,
        )
        try:
            result = await worker.invoke(
                "bilibili_commit",
                {"observation": {}},
                operation_id=str(uuid.uuid4()),
                context=InvocationContext(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), 1),
            )
            assert result.status == Outcome.ERROR and result.effect == Effect.NONE
        finally:
            assert await worker.close(5)

    asyncio.run(scenario())


def test_network_gate_never_permits_staging_or_mismatched_or_second_writes():
    async def scenario():
        gate = PublicationGate(spec())
        gate.text = TEXT
        gate.mentions = MENTIONS
        data = {"oid": "123", "type": "1", "message": TEXT, "at_name_to_mid": json.dumps({"Alice": "222"})}

        class Route:
            def __init__(self, body):
                self.request = SimpleNamespace(
                    url="https://api.bilibili.com/x/v2/reply/add", method="POST", post_data=urlencode(body)
                )
                self.result = None

            async def continue_(self):
                self.result = "allowed"

            async def abort(self):
                self.result = "blocked"

        first = Route(data)
        await gate.route(first)
        assert first.result == "blocked"
        gate.allowed = True
        for bad in [
            {**data, "oid": "999"},
            {**data, "message": "different"},
            {**data, "at_name_to_mid": json.dumps({"Alice": "999"})},
        ]:
            route = Route(bad)
            await gate.route(route)
            assert route.result == "blocked" and not gate.sent
        allowed = Route(data)
        await gate.route(allowed)
        assert allowed.result == "allowed" and gate.sent
        again = Route(data)
        await gate.route(again)
        assert again.result == "blocked"

    asyncio.run(scenario())


def test_real_browser_selects_uid_and_requires_receipt_and_new_record_without_enter_send():
    async def scenario():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                for case in ["success", "changed_account", "lost_click_reply", "server_changed_content"]:
                    context = await browser.new_context(service_workers="block")
                    verified_contexts = []
                    try:
                        page = await context.new_page()
                        gate = PublicationGate(spec())
                        requests = []
                        records = []

                        async def route(actual):
                            if actual.request.url == URL:
                                await actual.fulfill(
                                    status=200,
                                    body=HTML.replace("<!--SERVER_RECORDS-->", "".join(records)),
                                    content_type="text/html",
                                )
                                return

                            class ControlledRoute:
                                request = actual.request

                                async def continue_(self):
                                    requests.append(actual.request.post_data)
                                    records.append(
                                        server_record(
                                            "different server content" if case == "server_changed_content" else TEXT
                                        )
                                    )
                                    await actual.fulfill(status=200, json={"code": 0, "data": {"rpid": 99}})

                                async def abort(self):
                                    await actual.abort()

                            await gate.route(ControlledRoute())

                        await context.route("**/*", route)
                        await page.goto(URL)

                        async def verify_page():
                            fresh = await browser.new_context(service_workers="block")
                            verified_contexts.append(fresh)
                            await fresh.route("**/*", route)
                            fresh_page = await fresh.new_page()
                            await fresh_page.goto(URL)
                            return fresh_page

                        forms = PageForms(page, spec(), gate, verify_page)
                        opened = await forms.open()
                        assert opened.status == Outcome.OK
                        filled = await forms.fill({"observation": opened.data, "text": TEXT, "mentions": MENTIONS})
                        assert (
                            filled.status == Outcome.OK
                            and not requests
                            and await page.evaluate("window.submitted") == 0
                        )
                        assert await page.locator("#editor .mention").get_attribute("data-mid") == "222"
                        if case == "changed_account":
                            await page.locator("#account").evaluate("e=>e.dataset.mid='999'")
                            try:
                                await forms.commit({"observation": filled.data})
                            except ValueError:
                                pass
                            else:
                                raise AssertionError("Account drift must reject before clicking")
                            assert not requests
                        else:
                            if case == "lost_click_reply":
                                original = page.evaluate

                                async def lost(script, arg=None):
                                    value = await original(script, arg)
                                    if "b.click()" in script:
                                        async with asyncio.timeout(5):
                                            while not requests:
                                                await asyncio.sleep(0.01)
                                        raise RuntimeError("fixture lost evaluation reply after click")
                                    return value

                                page.evaluate = lost
                            result = await forms.commit({"observation": filled.data})
                            assert len(requests) == 1, (case, result)
                            if case == "success":
                                assert (
                                    result.status == Outcome.OK
                                    and result.effect == Effect.APPLIED
                                    and result.data["record_id"] == "99"
                                )
                            else:
                                assert result.status == Outcome.UNKNOWN and result.effect == Effect.UNKNOWN
                    finally:
                        await context.close()
                        for fresh in verified_contexts:
                            await fresh.close()
            finally:
                await browser.close()

    asyncio.run(scenario())
