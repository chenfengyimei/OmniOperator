"""Owner terminal browser flows use a fake native backend, isolated contexts and real HTTP receipts."""

from omni_test_support.v2.terminals_ui import (
    FixtureTerminalBackend as FixtureTerminalBackend,
)
from omni_test_support.v2.terminals_ui import (
    attach as attach,
)
from omni_test_support.v2.terminals_ui import (
    base as base,
)
from omni_test_support.v2.terminals_ui import (
    connect as connect,
)
from omni_test_support.v2.terminals_ui import (
    expect as expect,
)
from omni_test_support.v2.terminals_ui import (
    headers as headers,
)
from omni_test_support.v2.terminals_ui import (
    new_session as new_session,
)
from omni_test_support.v2.terminals_ui import (
    open_request as open_request,
)
from omni_test_support.v2.terminals_ui import (
    output as output,
)
from omni_test_support.v2.terminals_ui import (
    parse_qs as parse_qs,
)
from omni_test_support.v2.terminals_ui import (
    pytest as pytest,
)
from omni_test_support.v2.terminals_ui import (
    select_terminal as select_terminal,
)
from omni_test_support.v2.terminals_ui import (
    shared_ui as shared_ui,
)
from omni_test_support.v2.terminals_ui import (
    terminal_backend as terminal_backend,
)
from omni_test_support.v2.terminals_ui import (
    terminal_server as terminal_server,
)
from omni_test_support.v2.terminals_ui import (
    urlencode as urlencode,
)
from omni_test_support.v2.terminals_ui import (
    urlsplit as urlsplit,
)
from omni_test_support.v2.terminals_ui import (
    urlunsplit as urlunsplit,
)
from omni_test_support.v2.terminals_ui import (
    uuid as uuid,
)
from omni_test_support.v2.terminals_ui import (
    v2_server as v2_server,
)
from omni_test_support.v2.terminals_ui import (
    watch_terminal as watch_terminal,
)
from omni_test_support.v2.terminals_ui import (
    workspace as workspace,
)


def test_owner_start_explicit_input_and_panel_leave_detaches_without_closing_native(
    ui_browser, terminal_server, terminal_backend, tmp_path
):
    context = ui_browser.new_context(viewport={"width": 390, "height": 1000})
    try:
        context.add_init_script("localStorage.setItem('omni_theme', 'dark')")
        page, errors = connect(context, terminal_server)
        agent = page.locator("#agents").input_value()
        folder = tmp_path / "workspace"
        workspace(context, terminal_server, agent, folder)
        watch_terminal(page)
        page.get_by_role("button", name="所有者终端", exact=True).click()
        expect(page.locator("#terminal-options")).to_contain_text(str(folder))
        page.locator("#terminal-create").click()
        expect(page.locator("#ui-dialog")).to_contain_text("不是操作系统沙箱")
        with page.expect_response(
            lambda response: response.request.method == "POST" and response.url.endswith("/terminals")
        ) as opened:
            page.locator("#ui-dialog").get_by_role("button", name="信任并启动终端", exact=True).click()
        assert opened.value.status == 201
        session = opened.value.json()
        sent = opened.value.request.post_data_json
        assert sent == {
            "operation_id": str(uuid.UUID(sent["operation_id"])),
            "shell": "bash",
            "cols": 80,
            "rows": 24,
            "trust": True,
        }
        assert len(terminal_backend.processes) == 1
        native = terminal_backend.processes[0]
        assert native.spec["cwd"] == str(folder)
        attach(page)
        assert page.evaluate("window.__fixtureScreens.at(-1).options.disableStdin")
        output(
            terminal_server,
            native,
            "\x1b[6n\x1b[c\x1b]52;c;cHJpdmF0ZQ==\x07\x1b]8;;https://invalid.example\x07parser-boundary\x1b]8;;\x07\r\n",
        )
        page.wait_for_function("() => window.__fixtureTerminalText().includes('parser-boundary')")
        assert native.writes == []
        page.locator("#terminal-input").fill("explicit-owner-text")
        with page.expect_response(lambda response: response.url.endswith("/input")) as accepted:
            page.locator("#terminal-send").click()
        assert accepted.value.status == 200 and accepted.value.json()["state"] == "accepted"
        assert accepted.value.request.post_data_json["data"] == "explicit-owner-text"
        assert len(native.writes) == 1 and native.writes[0][1] == "explicit-owner-text"
        expect(page.locator("#terminal-receipt")).to_contain_text("不证明命令执行成功")
        expect(page.locator("#terminal-input")).to_have_value("")
        with page.expect_response(lambda response: response.url.endswith("/resize")) as resized:
            page.locator("#terminal-resize").click()
        assert resized.value.status == 200 and resized.value.json()["state"] == "accepted"
        assert len(native.resizes) == 1 and 20 <= native.resizes[0][0] <= 500 and 5 <= native.resizes[0][1] <= 200
        with page.expect_response(lambda response: response.url.endswith("/detach")) as detached:
            page.locator('nav[aria-label="工作台"] [data-page="memory"]').click()
        assert detached.value.status == 200
        assert not native.reclaimed and len(native.writes) == 1
        assert page.evaluate("window.__fixtureScreens.at(-1).__fixtureDisposed === true")
        expect(page.locator("#terminal-screen")).to_have_text("")
        recorded = context.request.get(
            base(terminal_server, agent) + f"/terminals/{session['id']}", headers=headers(terminal_server)
        )
        assert recorded.status == 200 and recorded.json()["state"] == "running"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert errors == []
    finally:
        context.close()


def test_output_gap_and_invalid_stream_require_explicit_reconnect_without_input_replay(
    ui_browser, terminal_server, terminal_backend, tmp_path
):
    context = ui_browser.new_context(viewport={"width": 760, "height": 1000})
    try:
        page, errors = connect(context, terminal_server)
        agent = page.locator("#agents").input_value()
        workspace(context, terminal_server, agent, tmp_path / "workspace")
        session = new_session(context, terminal_server, agent)
        native = terminal_backend.processes[0]
        output(terminal_server, native, "x" * 540000 + "\r\nretained-tail-marker\r\n")
        watch_terminal(page)
        page.get_by_role("button", name="所有者终端", exact=True).click()
        select_terminal(page, session)
        first = attach(page)
        expect(page.locator("#terminal-gap")).to_be_visible()
        expect(page.locator("#terminal-gap")).to_contain_text("输出存在缺失")
        page.wait_for_function("() => window.__fixtureTerminalText().includes('retained-tail-marker')")

        def invalid_stream(route):
            original = urlsplit(route.request.url)
            query = parse_qs(original.query)
            query["stream_id"] = [str(uuid.uuid4())]
            route.continue_(
                url=urlunsplit(
                    (original.scheme, original.netloc, original.path, urlencode(query, doseq=True), original.fragment)
                )
            )

        with page.expect_response(lambda response: response.url.endswith("/detach")) as detached:
            page.route("**/output?**", invalid_stream, times=1)
            expect(page.locator("#terminal-status")).to_contain_text("输出游标或流已失效")
        assert detached.value.status == 200
        expect(page.locator("#terminal-input-fields")).to_have_attribute("disabled", "")
        expect(page.locator("#terminal-enter")).to_be_disabled()
        expect(page.locator("#terminal-screen")).to_have_text("")
        assert native.writes == [] and not native.reclaimed
        with page.expect_request(
            lambda request: "/output?" in request.url and parse_qs(urlsplit(request.url).query).get("after") == ["0"]
        ) as replayed:
            second = attach(page)
        assert replayed.value.method == "GET" and second["connection_id"] != first["connection_id"]
        page.wait_for_function("() => window.__fixtureTerminalText().includes('retained-tail-marker')")
        expect(page.locator("#terminal-gap")).to_be_visible()
        assert native.writes == []
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize("v2_server", ["two_agents"], indirect=True)
def test_late_input_receipt_keeps_agent_drafts_and_lost_reply_only_queries_get(
    ui_browser, terminal_server, terminal_backend, tmp_path
):
    context = ui_browser.new_context(viewport={"width": 1440, "height": 1000})
    try:
        page, errors = connect(context, terminal_server)
        original = page.locator("#agents").input_value()
        other = next(
            value
            for value in page.locator("#agents option").evaluate_all("rows => rows.map(row => row.value)")
            if value != original
        )
        workspace(context, terminal_server, original, tmp_path / "workspace")
        workspace(context, terminal_server, other, tmp_path / "workspace")
        old = new_session(context, terminal_server, original)
        newer = new_session(context, terminal_server, other)
        page.get_by_role("button", name="所有者终端", exact=True).click()
        select_terminal(page, old)
        attach(page)
        page.evaluate(
            """({agent,session}) => {
              const fetch = window.fetch.bind(window), path = `/api/v1/agents/${agent}/terminals/${session}/input`;
              window.__terminalInputPosts = []; window.__terminalHeld = []; window.__dropTerminalInput = false;
              window.fetch = async (url,options={}) => {
                if (String(url) !== path || options.method !== 'POST') return fetch(url,options);
                window.__terminalInputPosts.push(JSON.parse(options.body));
                const response = await fetch(url,options), body = await response.text();
                if (window.__dropTerminalInput) throw new TypeError('Fixture lost response after commit');
                return new Promise(resolve => window.__terminalHeld.push({resolve,body,status:response.status}));
              };
            }""",
            {"agent": original, "session": old["id"]},
        )
        page.locator("#terminal-input").fill("one explicit original input")
        page.locator("#terminal-send").click()
        page.wait_for_function("() => window.__terminalHeld.length === 1")
        assert len(terminal_backend.processes[0].writes) == 1
        page.locator("#terminal-input").fill("new original draft while receipt is held")
        page.locator("#agents").select_option(other)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        select_terminal(page, newer)
        page.locator("#terminal-input").fill("current other Agent draft")
        page.evaluate(
            """async () => {
              for(const reply of window.__terminalHeld.splice(0)) reply.resolve(new Response(reply.body,{status:reply.status,headers:{'Content-Type':'application/json'}}));
              await new Promise(requestAnimationFrame);
            }"""
        )
        expect(page.locator("#terminal-detail")).to_contain_text(newer["id"])
        expect(page.locator("#terminal-input")).to_have_value("current other Agent draft")
        page.locator("#agents").select_option(original)
        expect(page.locator("nav")).not_to_have_attribute("inert", "")
        expect(page.locator("#terminal-detail")).to_contain_text(old["id"])
        expect(page.locator("#terminal-input")).to_have_value("new original draft while receipt is held")
        attach(page)
        page.evaluate("() => { window.__dropTerminalInput = true; }")
        page.locator("#terminal-send").click()
        expect(page.locator("#terminal-status")).to_contain_text("操作回执未确认")
        expect(page.locator("#terminal-input-fields")).to_have_attribute("disabled", "")
        expect(page.locator("#terminal-send")).to_be_disabled()
        assert len(terminal_backend.processes[0].writes) == 2
        operation = page.evaluate("window.__terminalInputPosts.at(-1).operation_id")
        with page.expect_response(lambda response: response.url.endswith(f"/operations/{operation}")) as receipt:
            page.locator("#terminal-query").click()
        assert receipt.value.status == 200 and receipt.value.request.method == "GET"
        assert receipt.value.json()["state"] == "accepted"
        expect(page.locator("#terminal-receipt")).to_contain_text("原生操作已接收")
        expect(page.locator("#terminal-input")).to_have_value("new original draft while receipt is held")
        assert page.evaluate("window.__terminalInputPosts.length") == 2
        assert len(terminal_backend.processes[0].writes) == 2 and terminal_backend.processes[1].writes == []
        assert errors == []
    finally:
        context.close()
