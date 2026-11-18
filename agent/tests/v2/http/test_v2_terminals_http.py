"""Owner terminal admission and receipts use real API/storage with an isolated native port."""

from omni_test_support.v2.terminals_http import (
    FixtureTerminalBackend as FixtureTerminalBackend,
)
from omni_test_support.v2.terminals_http import (
    TestClient as TestClient,
)
from omni_test_support.v2.terminals_http import (
    configure_workspace as configure_workspace,
)
from omni_test_support.v2.terminals_http import (
    http_app as http_app,
)
from omni_test_support.v2.terminals_http import (
    migration_head as migration_head,
)
from omni_test_support.v2.terminals_http import (
    open_request as open_request,
)
from omni_test_support.v2.terminals_http import (
    owner as owner,
)
from omni_test_support.v2.terminals_http import (
    uuid as uuid,
)


def test_owner_terminal_receipts_do_not_replay_input_or_claim_command_success(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    backend = FixtureTerminalBackend()
    with TestClient(http_app(home, terminal_backend=backend)) as client:
        assert client.get(f"/api/v1/agents/{uuid.uuid4()}/terminals").status_code == 401
        agent, base, db = owner(client, home, workspace)
        options = client.get(base + "/terminals/options").json()
        assert options["control"] == "owner_only" and not options["model_raw_input"]
        assert options["workspace"] == str(workspace)
        request = open_request()
        response = client.post(base + "/terminals", json=request)
        assert response.status_code == 201, response.text
        session = response.json()
        url = base + "/terminals/" + session["id"]
        assert session["state"] == "running" and len(backend.processes) == 1
        assert client.post(base + "/terminals", json=request).json()["id"] == session["id"]
        assert len(backend.processes) == 1
        receipt = client.get(base + f"/terminal-operations/{request['operation_id']}").json()
        assert receipt["session"]["id"] == session["id"] and receipt["operation"]["state"] == "accepted"
        connection = client.post(url + "/attach", json={}).json()
        assert client.post(url + "/attach", json={}).status_code == 409
        raw = "private-terminal-input-中文\r"
        entry = {"operation_id": str(uuid.uuid4()), "connection_id": connection["connection_id"], "data": raw}
        response = client.post(url + "/input", json=entry)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "accepted" and response.json()["bytes"] == len(raw.encode())
        assert raw not in response.text and "command_success" not in response.json()
        assert client.post(url + "/input", json=entry).json()["state"] == "accepted"
        assert backend.processes[0].writes == [(entry["operation_id"], raw)]
        assert client.post(url + "/input", json={**entry, "data": "different"}).status_code == 409
        resized = client.post(
            url + "/resize",
            json={
                "operation_id": str(uuid.uuid4()),
                "connection_id": connection["connection_id"],
                "cols": 140,
                "rows": 40,
            },
        )
        assert resized.status_code == 200 and resized.json()["state"] == "accepted"
        assert client.get(url).json()["cols"] == 140
        audit = client.portal.call(db.read, lambda u: u.query("audit", agent))
        assert "private-terminal-input" not in str(audit)
        assert client.post(url + "/detach", json={"connection_id": connection["connection_id"]}).json()["detached"]
        assert not backend.processes[0].reclaimed
        assert client.post(url + "/input", json={**entry, "operation_id": str(uuid.uuid4())}).status_code == 409
        connection = client.post(url + "/attach", json={}).json()
        assert (
            client.get(
                url + "/output",
                params={"connection_id": connection["connection_id"], "stream_id": str(uuid.uuid4()), "after": 0},
            ).status_code
            == 410
        )
        current = client.get(url).json()
        closed = client.post(url + "/close", json={"operation_id": str(uuid.uuid4()), "revision": current["revision"]})
        assert closed.status_code == 200, closed.text
        assert closed.json()["session"]["state"] == "exited" and not closed.json()["session"]["cleanup_pending"]


def test_partial_input_stays_unknown_and_receipt_lookup_never_sends_again(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    backend = FixtureTerminalBackend()
    with TestClient(http_app(home, terminal_backend=backend)) as client:
        agent, base, db = owner(client, home, workspace)
        session = client.post(base + "/terminals", json=open_request()).json()
        url = base + "/terminals/" + session["id"]
        connection = client.post(url + "/attach", json={}).json()
        backend.processes[0].partial_write = True
        request = {
            "operation_id": str(uuid.uuid4()),
            "connection_id": connection["connection_id"],
            "data": "effect might be applied\r",
        }
        response = client.post(url + "/input", json=request)
        assert response.status_code == 200 and response.json()["state"] == "unknown"
        assert client.post(url + "/input", json=request).json()["state"] == "unknown"
        assert len(backend.processes[0].writes) == 1
        assert client.get(url + f"/operations/{request['operation_id']}").json()["state"] == "unknown"
        other = client.portal.call(db.write, lambda u: u.create_agent("Other"))["id"]
        assert client.get(f"/api/v1/agents/{other}/terminals/{session['id']}").status_code == 404
        assert client.get(f"/api/v1/agents/{other}/terminal-operations/{request['operation_id']}").status_code == 404
        assert client.portal.call(db.read, lambda u: u.terminal_get(agent, session["id"]))["state"] == "running"


def test_terminal_requests_are_strict_and_headless_capabilities_do_not_need_model(tmp_path):
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(http_app(home, terminal_backend=FixtureTerminalBackend())) as client:
        _, base, _ = owner(client, home, workspace)
        for changes in (
            {"trust": False},
            {"cols": True},
            {"rows": 201},
            {"shell": "custom.exe"},
            {"cwd": str(workspace)},
            {"operation_id": "bad"},
        ):
            assert client.post(base + "/terminals", json={**open_request(), **changes}).status_code == 422
        for query in ("?limit=51", "?after=-1", "?after=9223372036854775808"):
            assert client.get(base + "/terminals" + query).status_code == 422
        session = client.post(base + "/terminals", json=open_request()).json()
        url = base + "/terminals/" + session["id"]
        connection = client.post(url + "/attach", json={}).json()
        valid = {"operation_id": str(uuid.uuid4()), "connection_id": connection["connection_id"], "data": "x"}
        for changes in ({"data": ""}, {"data": "x" * 16385}, {"extra": "forbidden"}, {"connection_id": "bad"}):
            assert client.post(url + "/input", json={**valid, **changes}).status_code == 422
        assert client.get("/api/v1/version").json()["schema_version"] == migration_head()
