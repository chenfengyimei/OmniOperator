"""Actual browser form/chip/receipt path on isolated HTML; every API response is locally fulfilled."""

import asyncio
import html
import json
import sys
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode

from test_v2_bilibili import BV, FORM, MENTIONS, TEXT, URL

from omni.adapters.workers.bilibili import PageForms, PublicationGate
from omni.domain.bilibili import forms_value
from omni.domain.contracts import Effect, Outcome


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


HTML = """<!doctype html><meta charset=utf-8><style>#editor,.body{white-space:pre-wrap}#editor{min-height:40px;border:1px solid}</style>
<a id=account href="https://space.bilibili.com/456" data-mid=456>owner</a>
<div id=challenge hidden></div><div id=loggedout hidden></div>
<section id=target data-id=BV1xx411c7mD><div id=editor contenteditable=true></div><button id=submit>Publish</button><div id=records><!--SERVER_RECORDS--></div></section>
<input id=mention-query><div id=choices></div>
<script>
window.submitted=0;
document.querySelector('#mention-query').addEventListener('input',()=>{
 const choices=document.querySelector('#choices');choices.replaceChildren();
 for(const uid of ['111','222']){const option=document.createElement('button');option.className='candidate';option.dataset.mid=uid;option.textContent='Alice';
 option.onclick=()=>{const editor=document.querySelector('#editor');editor.textContent=editor.textContent.replace(/@$/,'');const chip=document.createElement('span');chip.className='mention';chip.dataset.mid=uid;chip.contentEditable='false';chip.textContent='@Alice';editor.append(chip);choices.replaceChildren();editor.focus();const r=document.createRange();r.selectNodeContents(editor);r.collapse(false);getSelection().removeAllRanges();getSelection().addRange(r);};choices.append(option);}
});
document.querySelector('#editor').addEventListener('keydown',e=>{if(e.key==='Enter')document.querySelector('#submit').click();});
document.querySelector('#submit').onclick=async()=>{
 const editor=document.querySelector('#editor');window.submitted++;
 const mentions=Object.fromEntries([...editor.querySelectorAll('.mention')].map(c=>[c.textContent.slice(1),c.dataset.mid]));
 const data=new URLSearchParams({oid:'123',type:'1',root:'0',parent:'0',message:editor.innerText,at_name_to_mid:JSON.stringify(mentions)});
 const response=await fetch('https://api.bilibili.com/x/v2/reply/add',{method:'POST',body:data});const receipt=await response.json();
 const record=document.createElement('article');record.className='record';record.dataset.id=String(receipt.data.rpid);
 const author=document.createElement('a');author.className='author';author.dataset.mid='456';author.href='https://space.bilibili.com/456';author.textContent='owner';
 const body=document.createElement('div');body.className='body';body.innerHTML=editor.innerHTML;record.append(author,body);document.querySelector('#records').append(record);editor.replaceChildren();
};
</script>"""


def spec():
    return {
        "kind": "comment",
        "url": URL,
        "target_key": BV,
        "remote_target_id": "123",
        "account": "456",
        "form": forms_value({"comment": FORM}, True)["comment"],
    }


def server_record(text=TEXT):
    body = (
        html.escape(text[: MENTIONS[0]["start"]])
        + f'<span class="mention" data-mid="222">@Alice</span>'
        + html.escape(text[MENTIONS[0]["end"] :])
        if text == TEXT
        else html.escape(text)
    )
    return (
        '<article class="record" data-id="99"><a class="author" data-mid="456" href="https://space.bilibili.com/456">owner</a><div class="body">'
        + body
        + "</div></article>"
    )


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
