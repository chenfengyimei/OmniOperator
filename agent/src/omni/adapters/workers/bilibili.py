"""One isolated Bilibili browser transaction; no database, arbitrary script, API writes or persisted cookies."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

from ...domain.bilibili import Form, digest, mentions_value, page_identity, target, uid
from ...domain.contracts import ActionResult, Effect, Outcome
from ...domain.delivery import text_fingerprint
from .protocol import MAX_FRAME, encode_response
from .protocol import request as decode_request

METHODS = {"bilibili_open": False, "bilibili_fill": True, "bilibili_commit": True}
Payload = dict[str, object]

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page, Request, Route


class PublicationGate:
    """No browser write during observation/fill; exactly one content-bound request after commit."""

    def __init__(self, spec: Payload) -> None:
        self.spec = spec
        self.allowed = self.sent = False
        self.text = ""
        self.mentions: list[Payload] = []

    async def route(self, route: Route) -> None:
        request = route.request
        url = urlsplit(request.url)
        allowed_host = url.hostname and any(
            url.hostname == suffix or url.hostname.endswith("." + suffix)
            for suffix in ("bilibili.com", "hdslb.com", "bilivideo.com", "biliapi.net")
        )
        if (
            request.method in {"GET", "HEAD"}
            and url.scheme == "https"
            and allowed_host
            and not url.username
            and not url.password
        ):
            await route.continue_()
            return
        if self.allowed and not self.sent and self.matches(request):
            self.sent = True
            await route.continue_()
        else:
            await route.abort()

    def matches(self, request: Request) -> bool:
        try:
            url = urlsplit(request.url)
            if (
                request.method != "POST"
                or url.scheme != "https"
                or url.username
                or url.password
                or url.port not in {None, 443}
            ):
                return False
            raw = request.post_data
            if not isinstance(raw, str) or len(raw) > 32768:
                return False
            data: dict[str, Any] = parse_qs(raw, keep_blank_values=True)
            if any(len(v) != 1 for v in data.values()):
                return False
            data = {k: v[0] for k, v in data.items()}
            if cast(str, self.spec["kind"]) == "comment":
                if url.hostname != "api.bilibili.com" or url.path != "/x/v2/reply/add":
                    return False
                reply = parse_qs(urlsplit(cast(str, self.spec["url"])).query).get("reply_id", ["0"])[0]
                actual_mentions: Payload = json.loads(data.get("at_name_to_mid", "{}"))
                if not isinstance(cast(object, actual_mentions), dict):
                    return False
                return (
                    data.get("oid") == cast(str, self.spec["remote_target_id"])
                    and data.get("type") == "1"
                    and data.get("message") == self.text
                    and data.get("root", "0") == reply
                    and data.get("parent", "0") == reply
                    and data.get("pictures", "") in {"", "[]"}
                    and {name: str(value) for name, value in actual_mentions.items()}
                    == {m["name"]: cast(str, m["uid"]) for m in self.mentions}
                )
            if url.hostname != "api.vc.bilibili.com" or url.path != "/web_im/v1/web_im/send_msg":
                return False
            content: Payload = json.loads(data.get("msg[content]", "null"))
            return (
                not self.mentions
                and data.get("msg[sender_uid]") == cast(str, self.spec["account"])
                and data.get("msg[receiver_id]") == cast(str, self.spec["remote_target_id"])
                and data.get("msg[receiver_type]") == "1"
                and data.get("msg[msg_type]") == "1"
                and isinstance(cast(object, content), dict)
                and set(content) == {"content"}
                and content["content"] == self.text
            )
        except (ValueError, TypeError):
            return False


class PageForms:
    def __init__(
        self,
        page: Page,
        spec: Payload,
        gate: PublicationGate | None = None,
        verifier: Callable[[], Awaitable[Page]] | None = None,
    ) -> None:
        self.page, self.spec = page, spec
        self.form: dict[str, str] = Form.model_validate(spec["form"]).model_dump()
        self.observation: Payload | None = None
        self.used = False
        self.text = ""
        self.mentions: list[Payload] = []
        self.gate = gate
        self.verifier = verifier

    @staticmethod
    async def one(scope: Page | Locator, selector: str) -> Locator:
        item = scope.locator(selector)
        if await item.count() != 1 or not await item.is_visible():
            raise ValueError("Missing or ambiguous visible form control")
        return item

    async def capture(self) -> tuple[Payload, Locator, Locator, Locator]:
        if target(cast(str, self.spec["kind"]), self.page.url)[0] != cast(str, self.spec["url"]):
            raise ValueError("Page target changed")
        f = self.form
        for selector in (f["challenge"], f["logged_out"]):
            items = self.page.locator(selector)
            if await items.count() > 32 or any([await items.nth(n).is_visible() for n in range(await items.count())]):
                raise ValueError("Login or challenge requires owner intervention")
        account = await self.one(self.page, f["account"])
        root = await self.one(self.page, f["target"])
        account_raw = await account.get_attribute(f["account_attr"])
        target_raw = await root.get_attribute(f["target_attr"])
        target_keys = {cast(str, self.spec["target_key"])}
        if cast(str, self.spec["kind"]) == "comment" and cast(str, self.spec["target_key"]).startswith("BV"):
            target_keys.add(cast(str, self.spec["remote_target_id"]))
        if (
            page_identity(account_raw) != cast(str, self.spec["account"])
            or page_identity(target_raw) not in target_keys
        ):
            raise ValueError("Account or target identity changed")
        editor, button = await self.one(root, f["editor"]), await self.one(root, f["submit"])
        if not await editor.is_editable() or (await editor.get_attribute("type") or "").lower() == "password":
            raise ValueError("Editor is unavailable")
        text = await editor.evaluate("e => 'value' in e ? e.value : e.innerText")
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Unexpected editor content")
        key = "e => [e.tagName,e.id,e.getAttribute('name'),e.getAttribute('type'),e.getAttribute('aria-label')]"
        observation: Payload = {
            "url": cast(str, self.spec["url"]),
            "epoch": await self.page.evaluate("performance.timeOrigin"),
            "account": cast(str, self.spec["account"]),
            "target_key": cast(str, self.spec["target_key"]),
            "account_raw": account_raw,
            "target_raw": target_raw,
            "editor_key": await editor.evaluate(key),
            "button_key": await button.evaluate(key),
            "text": text,
        }
        return observation, root, editor, button

    async def open(self) -> ActionResult:
        observation, _, _, _ = await self.capture()
        if observation["text"]:
            raise ValueError("Existing draft must be handled by the owner")
        self.observation = {**observation, "nonce": str(uuid.uuid4())}
        return ActionResult(
            Outcome.OK,
            "Reviewed form observed",
            data={"nonce": cast(str, self.observation["nonce"]), "fingerprint": digest(observation)},
        )

    async def fill(self, args: Payload) -> ActionResult:
        if (
            self.used
            or self.observation is None
            or args["observation"]
            != {
                "nonce": cast(str, self.observation["nonce"]),
                "fingerprint": digest({k: v for k, v in self.observation.items() if k != "nonce"}),
            }
        ):
            raise ValueError("Observation expired")
        observation, _, editor, _ = await self.capture()
        if observation != {k: v for k, v in self.observation.items() if k != "nonce"}:
            raise ValueError("Form changed after observation")
        text, mentions = cast(str, args["text"]), mentions_value(cast(str, args["text"]), args["mentions"])
        if mentions and not self.form["mention_options"]:
            raise ValueError("Real mention component is not configured")
        self.text, self.mentions = text, mentions
        if not mentions:
            await editor.fill(text, timeout=2000)
        else:
            await editor.fill("", timeout=2000)
            offset = 0
            for item in mentions:
                await editor.focus()
                await self.page.keyboard.insert_text(text[offset : cast(int, item["start"])])
                await editor.press_sequentially("@", timeout=2000)
                if self.form["mention_input"]:
                    field = await self.one(self.page, self.form["mention_input"])
                    await field.fill(cast(str, item["name"]), timeout=2000)
                else:
                    await self.page.keyboard.insert_text(cast(str, item["name"]))
                options = self.page.locator(self.form["mention_options"])
                # Candidates may arrive asynchronously; never choose by nickname alone.
                chosen = None
                for _ in range(20):
                    count = await options.count()
                    if count > 50:
                        raise ValueError("Mention list is oversized")
                    matches = [
                        options.nth(n)
                        for n in range(count)
                        if await options.nth(n).get_attribute(self.form["mention_uid_attr"]) == cast(str, item["uid"])
                        and await options.nth(n).is_visible()
                    ]
                    if len(matches) == 1:
                        chosen = matches[0]
                        break
                    if len(matches) > 1:
                        raise ValueError("Mention UID is ambiguous")
                    await asyncio.sleep(0.1)
                if chosen is None:
                    raise ValueError("Requested mention UID is unavailable")
                await chosen.click(timeout=2000)
                offset = cast(int, item["end"])
            await editor.focus()
            await self.page.keyboard.insert_text(text[offset:])
        observation, _, editor, _ = await self.capture()
        if observation["text"] != text or await self.chips(editor, self.form["mention_nodes"]) != [
            cast(str, m["uid"]) for m in mentions
        ]:
            raise ValueError("Rendered content or actual mention chips differ from the approved source")
        self.observation = {**observation, "nonce": cast(str, self.observation["nonce"])}
        return ActionResult(
            Outcome.OK,
            "Exact content and mention chips staged",
            Effect.APPLIED,
            data={"nonce": cast(str, self.observation["nonce"]), "fingerprint": digest(observation)},
        )

    async def chips(self, scope: Page | Locator, selector: str) -> list[str]:
        if not selector:
            return []
        nodes = scope.locator(selector)
        if await nodes.count() > 10:
            raise ValueError("Too many mention chips")
        return [
            uid(await nodes.nth(n).get_attribute(self.form["mention_uid_attr"])) for n in range(await nodes.count())
        ]

    async def commit(self, args: Payload) -> ActionResult:
        if self.used or self.observation is None:
            raise ValueError("Publication is one-shot")
        expected = {k: v for k, v in self.observation.items() if k != "nonce"}
        if args["observation"] != {"nonce": cast(str, self.observation["nonce"]), "fingerprint": digest(expected)}:
            raise ValueError("Staged form token changed")
        observation, _root, editor, button = await self.capture()
        if (
            observation != expected
            or not await button.is_enabled()
            or await self.chips(editor, self.form["mention_nodes"]) != [cast(str, m["uid"]) for m in self.mentions]
        ):
            raise ValueError("Staged form changed")
        self.used = True
        if self.gate:
            self.gate.text, self.gate.mentions, self.gate.allowed = self.text, self.mentions, True
        receipt_path = "/x/v2/reply/add" if cast(str, self.spec["kind"]) == "comment" else "/web_im/v1/web_im/send_msg"
        requests: list[Request] = []

        def relevant(request: Request) -> bool:
            from urllib.parse import urlsplit

            url = urlsplit(request.url)
            return (
                request.method == "POST"
                and url.scheme == "https"
                and url.hostname in {"api.bilibili.com", "api.vc.bilibili.com"}
                and url.path == receipt_path
                and request.frame == self.page.main_frame
            )

        def observed(request: Request) -> None:
            if relevant(request):
                requests.append(request)

        observed_page = self.page
        observed_page.on("request", observed)
        clicked = False
        attempted = False
        try:
            async with self.page.expect_response(
                lambda response: relevant(response.request), timeout=5000
            ) as response_info:
                # One DOM task rechecks identities, exact text and chip UIDs before the single click.
                attempted = True
                clicked = await self.page.evaluate(
                    """p => {
                  const one=(s,base=document)=>{const a=base.querySelectorAll(s);return a.length===1?a[0]:null;};
                  const a=one(p.form.account),t=one(p.form.target),e=t&&one(p.form.editor,t),b=t&&one(p.form.submit,t);
                  const key=n=>[n.tagName,n.id,n.getAttribute('name'),n.getAttribute('type'),n.getAttribute('aria-label')];
                  const visible=n=>n&&n.getClientRects().length&&!n.closest('[inert]')&&getComputedStyle(n).visibility!=='hidden';
                  if(location.href!==p.observation.url||performance.timeOrigin!==p.observation.epoch||!visible(a)||!visible(t)||!visible(e)||!visible(b)||b.disabled||b.getAttribute('aria-disabled')==='true'||e.disabled||e.readOnly||a.getAttribute(p.form.account_attr)!==p.observation.account_raw||t.getAttribute(p.form.target_attr)!==p.observation.target_raw||('value'in e?e.value:e.innerText)!==p.observation.text||JSON.stringify(key(e))!==JSON.stringify(p.observation.editor_key)||JSON.stringify(key(b))!==JSON.stringify(p.observation.button_key))return false;
                  if([p.form.challenge,p.form.logged_out].some(s=>[...document.querySelectorAll(s)].some(visible)))return false;
                  const u=p.form.mention_nodes?[...e.querySelectorAll(p.form.mention_nodes)].map(n=>n.getAttribute(p.form.mention_uid_attr)):[];
                  if(JSON.stringify(u)!==JSON.stringify(p.uids))return false;
                  b.click();return true;
                }""",
                    {"form": self.form, "observation": expected, "uids": [cast(str, m["uid"]) for m in self.mentions]},
                )
                if not clicked:
                    attempted = False
                    return ActionResult(
                        Outcome.DENIED, "Form changed before submission", error_code="bilibili_form_changed"
                    )
            response = await response_info.value
            if response.status != 200:
                raise ValueError("No successful browser receipt")
            raw = await response.body()
            if len(raw) > 65536:
                raise ValueError("Oversized browser receipt")
            data: dict[str, Any] = json.loads(raw)
            if (
                not isinstance(cast(object, data), dict)
                or type(data.get("code")) is not int
                or data["code"] != 0
                or not isinstance(data.get("data"), dict)
            ):
                raise ValueError("Rejected or changed browser receipt")
            identity = cast(Payload, data["data"]).get(
                "rpid" if cast(str, self.spec["kind"]) == "comment" else "msg_key"
            )
            identity = uid(identity)
            if len(requests) != 1:
                raise ValueError("Concurrent or duplicate browser submissions")
            # A local optimistic record is not server evidence. Use independent storage/cache and a fresh page.
            if self.verifier is None:
                raise ValueError("Independent publication readback is unavailable")
            self.page = await self.verifier()
            # A provider receipt alone is insufficient: read the exact new record in the reviewed target.
            for _ in range(30):
                _, current_root, _, _ = await self.capture()
                records = current_root.locator(self.form["records"])
                if await records.count() > 100:
                    raise ValueError("Published record window is oversized")
                matches = [
                    records.nth(n)
                    for n in range(await records.count())
                    if await records.nth(n).get_attribute(self.form["record_id_attr"]) == identity
                ]
                if len(matches) > 1:
                    raise ValueError("Published record is ambiguous")
                if matches:
                    record = matches[0]
                    author = record.locator(self.form["record_author"])
                    body = record.locator(self.form["record_text"])
                    if (
                        await author.count() == 1
                        and await body.count() == 1
                        and page_identity(await author.get_attribute(self.form["record_author_attr"]))
                        == cast(str, self.spec["account"])
                        and await body.inner_text() == self.text
                        and await self.chips(record, self.form["record_mentions"])
                        == [cast(str, m["uid"]) for m in self.mentions]
                    ):
                        return ActionResult(
                            Outcome.OK,
                            "Provider receipt and exact published record verified",
                            Effect.APPLIED,
                            data={
                                "record_id": identity,
                                "account": cast(str, self.spec["account"]),
                                "target": cast(str, self.spec["url"]),
                                "body_hash": text_fingerprint(self.text),
                                "mentions": [cast(str, m["uid"]) for m in self.mentions],
                            },
                        )
                await asyncio.sleep(0.1)
            raise ValueError("Published content could not be verified")
        except Exception:  # noqa: BLE001 -- page contents and provider exceptions must not escape IPC
            return (
                ActionResult(
                    Outcome.UNKNOWN,
                    "Publication outcome requires owner review",
                    Effect.UNKNOWN,
                    error_code="bilibili_publish_unknown",
                )
                if attempted
                else ActionResult(Outcome.DENIED, "Submission was not invoked", error_code="bilibili_form_changed")
            )
        finally:
            if self.gate:
                self.gate.allowed = False
            observed_page.remove_listener("request", observed)


class BrowserWorker:
    def __init__(self):
        self.playwright = self.browser = self.context = self.forms = None
        self.binding = None

    async def invoke(self, method: str, args: dict, context: dict) -> ActionResult:
        try:
            if self.binding is not None and self.binding != context:
                raise ValueError("Publication worker scope changed")
            if method == "bilibili_open":
                if self.forms is not None:
                    raise ValueError("One publication per worker")
                from playwright.async_api import async_playwright

                url, key = target(args["kind"], args["url"])
                if key != args["target_key"] or url != args["url"]:
                    raise ValueError("Target proof changed")
                self.playwright = await async_playwright().start()
                if type(args.get("headless")) is not bool:
                    raise ValueError("Browser mode was not bound to a reviewed profile")
                self.browser = await self.playwright.chromium.launch(headless=args["headless"])
                self.binding = context
                gate = PublicationGate({k: v for k, v in args.items() if k != "cookies"})

                async def close_socket(route):
                    await route.close()

                async def new_context():
                    assert self.browser is not None
                    fresh = await self.browser.new_context(accept_downloads=False, service_workers="block")
                    await fresh.route("**/*", gate.route)
                    await fresh.route_web_socket("**", close_socket)
                    await fresh.add_cookies(
                        [
                            {"name": name, "value": value, "domain": ".bilibili.com", "path": "/", "secure": True}
                            for name, value in args["cookies"].items()
                        ]
                    )
                    return fresh

                self.context = await new_context()
                page = await self.context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=8000)

                async def verify_page():
                    fresh = await new_context()
                    verified = await fresh.new_page()
                    await verified.goto(url, wait_until="domcontentloaded", timeout=5000)
                    return verified

                self.forms = PageForms(page, {k: v for k, v in args.items() if k != "cookies"}, gate, verify_page)
                return await self.forms.open()
            if self.forms is None:
                raise ValueError("No observed publication form")
            if method == "bilibili_fill":
                return await self.forms.fill(args)
            if method == "bilibili_commit":
                return await self.forms.commit(args)
            raise ValueError("Unknown publication operation")
        except Exception:  # noqa: BLE001 -- no native exceptions or credentials in protocol responses
            return ActionResult(
                Outcome.ERROR, "Publication form is unavailable", error_code="bilibili_form_unavailable"
            )

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


async def serve():
    worker = BrowserWorker()
    try:
        while raw := await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1):
            request = decode_request(raw)
            if request["method"] not in METHODS or request["writes"] is not METHODS[request["method"]]:
                return
            result = await worker.invoke(request["method"], request["arguments"], request["context"])
            sys.stdout.buffer.write(encode_response(request["id"], result))
            sys.stdout.buffer.flush()
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(serve())
