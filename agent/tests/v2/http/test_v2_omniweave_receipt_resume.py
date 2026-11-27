"""External actions with definite receipts resume OWA Workers from checkpoints."""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from omni.adapters.persistence.sqlite_access import sqlite_connection
from omni.application.engine import Application
from omni.composition.http import http_app
from omni.domain.background_review import REVIEW_PROMPT
from omni.domain.contracts import Invocation, ModelReply
from omni.domain.routing import POLICY_DEFAULT

CRASH = 78


def _authorize(client, home):
    client.headers["Authorization"] = "Bearer " + (home / "run/owner.token").read_text(encoding="utf-8")
    return client.get("/api/v1/agents").json()["items"][0]["id"]


def _select_main(client, base):
    selection = client.put(base + "/omniweave/main-session", json={"session_id": None})
    assert selection.status_code == 200, selection.text
    assert selection.json()["enabled"] is True
    return selection.json()["session_id"]


def _model_base(client, agent):
    base = f"/api/v1/agents/{agent}"
    settings = client.get(base + "/settings").json()
    url = base + "/configurations/" + settings["value"]["model_profile_id"]
    profile = client.get(url).json()
    response = client.put(
        url,
        json={
            "name": profile["name"],
            "enabled": True,
            "revision": profile["revision"],
            "value": {**profile["value"], "model": "fixture"},
        },
    )
    assert response.status_code == 200, response.text
    return base


def _rows(home, query, args=()):
    with sqlite_connection(home / "data/omni.db") as connection:
        return connection.execute(query, args).fetchall()


def _wait_approval(client, base):
    for _ in range(400):
        approvals = client.get(base + "/approvals").json()["items"]
        if approvals:
            return approvals
        time.sleep(0.025)
    raise AssertionError("No approval appeared")


def crash_at(phase):
    if phase == "settled":
        original = Application._tool_message_content

        async def wrapped(self, agent_id, run_id, messages, result, settings, tool_name):
            if tool_name == "files_write":
                os._exit(CRASH)
            return await original(self, agent_id, run_id, messages, result, settings, tool_name)

        Application._tool_message_content = wrapped
    elif phase == "mailbox-turn":
        original = Application._tool_message_content

        async def wrapped(self, agent_id, run_id, messages, result, settings, tool_name):
            if tool_name == "files_write":
                current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
                if current.startswith("OmniWeave Worker. Continue"):
                    os._exit(CRASH)
            return await original(self, agent_id, run_id, messages, result, settings, tool_name)

        Application._tool_message_content = wrapped
    else:
        raise AssertionError(f"Unknown crash phase {phase}")


class WorkerModel:
    async def complete(self, messages, tools, settings):
        if messages[0]["content"] == REVIEW_PROMPT:
            return ModelReply("No reusable procedure in this test turn", cost_micro=0)
        if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
            return ModelReply("Campus receipt resume", cost_micro=0)
        last = messages[-1]
        current = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        if not current.startswith("OmniWeave Worker."):
            if last["role"] == "tool":
                return ModelReply("Plan queued in the main conversation", cost_micro=0)
            tasks = [
                {
                    "key": "writer",
                    "goal": "write the receipt",
                    "profile": "coding",
                    "tools": ["files_write", "files_read"],
                    "max_calls": 8,
                }
            ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
        if last["role"] == "tool":
            return ModelReply("Receipt verified, continuation complete", cost_micro=0)
        return ModelReply(
            "Write",
            (Invocation("write", "files_write", {"path": "receipt.txt", "content": "receipt write"}),),
            cost_micro=0,
        )


class RecoveryModel(WorkerModel):
    async def complete(self, messages, tools, settings):
        if "previous process" in str(messages):
            if messages[-1]["role"] == "tool":
                return ModelReply("Receipt verified, continuation complete", cost_micro=0)
            return ModelReply(
                "Check",
                (Invocation("read", "files_read", {"path": "receipt.txt"}),),
                cost_micro=0,
            )
        return await WorkerModel.complete(self, messages, tools, settings)


class MailboxWorkerModel:
    def __init__(self):
        self.requested = asyncio.Event()

    async def complete(self, messages, tools, settings):
        if messages[0]["content"] == REVIEW_PROMPT:
            return ModelReply("No reusable procedure in this test turn", cost_micro=0)
        if str(messages[0]["content"]).startswith("OMNI_CONTEXT_SUMMARY_V1"):
            return ModelReply("Campus mailbox resume", cost_micro=0)
        last = messages[-1]
        text = str(next(row["content"] for row in reversed(messages) if row["role"] == "user"))
        if not text.startswith("OmniWeave Worker."):
            if last["role"] == "tool":
                return ModelReply("Plan queued in the main conversation", cost_micro=0)
            tasks = [
                {
                    "key": "writer",
                    "goal": "write the shared receipt",
                    "profile": "coding",
                    "tools": ["files_write", "files_read"],
                    "peers": ["helper"],
                    "max_calls": 8,
                },
                {"key": "helper", "goal": "stand by as helper", "profile": "research"},
            ]
            return ModelReply("Plan", (Invocation("plan", "owa_plan", {"tasks": tasks}),), cost_micro=0)
        payload = json.loads(text.split("\n", 1)[1])
        results = [row["content"] for row in messages if row["role"] == "tool"]
        if "events" in payload:
            [event] = payload["events"]
            if event["type"] == "peer.request":
                if last["role"] == "tool":
                    return ModelReply("Reply recorded", cost_micro=0)
                return ModelReply(
                    "Answer",
                    (
                        Invocation(
                            "answer",
                            "reply_peer",
                            {"request_id": event["request_id"], "answer": "receipt continuation ready"},
                        ),
                    ),
                    cost_micro=0,
                )
            assert event["type"] == "peer.reply" and event["answer"] == "receipt continuation ready"
            return ModelReply(
                "Continue",
                (Invocation("second", "files_write", {"path": "turn2.txt", "content": "second write"}),),
                cost_micro=0,
            )
        goal = payload["goal"]
        if goal == "stand by as helper":
            await self.requested.wait()
            return ModelReply("Helper standing by", cost_micro=0)
        assert goal == "write the shared receipt", goal
        if last["role"] == "tool" and "request_id" in str(last["content"]):
            self.requested.set()
        if not results:
            return ModelReply(
                "Write",
                (Invocation("first", "files_write", {"path": "turn1.txt", "content": "first write"}),),
                cost_micro=0,
            )
        if len(results) == 1:
            return ModelReply(
                "Verify",
                (Invocation("check", "files_read", {"path": "turn1.txt"}),),
                cost_micro=0,
            )
        if len(results) == 2:
            return ModelReply(
                "Ask",
                (
                    Invocation(
                        "ask",
                        "request_peer",
                        {"key": "need-reply", "target": "helper", "question": "Ready to continue?", "blocking": False},
                    ),
                ),
                cost_micro=0,
            )
        return ModelReply("Writer turn complete", cost_micro=0)


def test_receipt_backed_checkpoint_continuation_completes_delivery(tmp_path):
    home = tmp_path / "home"
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(Path(__file__).resolve().parents[2]), __file__, str(home)]
    child = subprocess.run(
        [*command, "settled"],
        capture_output=True,
        timeout=90,
        check=False,
        env={**os.environ, "OMNI_TEST_OFFLINE": "1"},
    )
    assert child.returncode == CRASH, child.stderr.decode(errors="replace")

    with TestClient(http_app(home, model=RecoveryModel(), omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        closeout = None
        for _ in range(400):
            items = client.get(base + f"/sessions/{session_id}/messages").json()["items"]
            closeout = next(
                (
                    row
                    for row in items
                    if row["role"] == "assistant" and "OmniWeave 交付 · 第 1 版 · 已完成" in row["content"]
                ),
                closeout,
            )
            if closeout is not None:
                break
            time.sleep(0.025)
        assert closeout is not None, items
        assert closeout["omniweave_delivery"]["outcome"] == "completed"
        assert client.get(base + "/approvals").json()["items"] == []
        [write] = _rows(home, "SELECT effect FROM actions WHERE tool='files_write'")
        assert write[0] == "applied"
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_read'") == [(1,)]
        assert _rows(
            home,
            "SELECT t.task_key, r.state FROM owa_tasks t JOIN owa_task_attempts a ON a.task_id=t.id JOIN runs r ON r.id=a.run_id",
        ) == [("writer", "succeeded")]
        workspaces = list((home / "owa-workspaces").rglob("receipt.txt"))
        assert len(workspaces) == 1 and workspaces[0].read_text(encoding="utf-8") == "receipt write"


class MailboxRecoveryModel(MailboxWorkerModel):
    async def complete(self, messages, tools, settings):
        if "previous process" in str(messages):
            if messages[-1]["role"] == "tool":
                assert "second write" in str(messages[-1]["content"]), messages[-1]["content"]
                return ModelReply("Receipt observed, mailbox continuation complete", cost_micro=0)
            return ModelReply(
                "Check",
                (
                    Invocation("read1", "files_read", {"path": "turn1.txt"}),
                    Invocation("read2", "files_read", {"path": "turn2.txt"}),
                ),
                cost_micro=0,
            )
        return await MailboxWorkerModel.complete(self, messages, tools, settings)


def test_mailbox_turn_resumes_from_checkpoint_after_crash(tmp_path):
    home = tmp_path / "home"
    launcher = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    command = [sys.executable, "-c", launcher, str(Path(__file__).resolve().parents[2]), __file__, str(home)]
    child = subprocess.run(
        [*command, "mailbox-turn"],
        capture_output=True,
        timeout=120,
        check=False,
        env={**os.environ, "OMNI_TEST_OFFLINE": "1"},
    )
    assert child.returncode == CRASH, child.stderr.decode(errors="replace")

    with TestClient(http_app(home, model=MailboxRecoveryModel(), omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        session_id = _select_main(client, base)
        closeout = None
        items: list = []
        for _ in range(3000):
            items = client.get(base + f"/sessions/{session_id}/messages").json()["items"]
            closeout = next(
                (
                    row
                    for row in items
                    if row["role"] == "assistant" and "OmniWeave 交付 · 第 1 版 · 已完成" in row["content"]
                ),
                closeout,
            )
            if closeout is not None:
                break
            time.sleep(0.05)
        assert closeout is not None, items
        assert closeout["omniweave_delivery"]["outcome"] == "completed"
        assert _rows(home, "SELECT count(*) FROM actions WHERE tool='files_write' AND effect='applied'") == [(2,)]
        assert _rows(home, "SELECT count(*) FROM actions WHERE state='unknown'") == [(0,)]
        first = list((home / "owa-workspaces").rglob("turn1.txt"))
        second = list((home / "owa-workspaces").rglob("turn2.txt"))
        assert len(first) == 1 and first[0].read_text(encoding="utf-8") == "first write"
        assert len(second) == 1 and second[0].read_text(encoding="utf-8") == "second write"
        rows = _rows(
            home,
            "SELECT t.task_key, r.state, a.attempt_no FROM owa_tasks t "
            "JOIN owa_task_attempts a ON a.task_id=t.id JOIN runs r ON r.id=a.run_id "
            "ORDER BY t.task_key, a.attempt_no",
        )
        assert rows == [
            ("helper", "succeeded", 1),
            ("helper", "succeeded", 2),
            ("writer", "succeeded", 1),
            ("writer", "succeeded", 2),
        ]
        assert client.get(base + "/approvals").json()["items"] == []


if __name__ == "__main__":
    home = Path(sys.argv[1])
    phase = sys.argv[2]
    crash_at(phase)
    model = MailboxWorkerModel() if phase == "mailbox-turn" else WorkerModel()
    with TestClient(http_app(home, model=model, omniweave_mode="dispatch")) as client:
        agent = _authorize(client, home)
        base = _model_base(client, agent)
        policy = client.get(base + "/routing").json()
        saved = client.put(
            base + "/routing",
            json={
                "revision": policy["revision"],
                "value": {**POLICY_DEFAULT, "kernel": {"safe_resume": True, "background_learning": False}},
            },
        )
        assert saved.status_code == 200, saved.text
        session_id = _select_main(client, base)
        client.post(
            base + "/turns",
            json={
                "key": "receipt" if phase == "settled" else "mailbox",
                "session_id": session_id,
                "message": "Campus receipt resume" if phase == "settled" else "Campus mailbox resume",
            },
        )
        while True:
            for approval in client.get(base + "/approvals").json()["items"]:
                if approval["state"] == "pending":
                    client.post(
                        base + f"/approvals/{approval['id']}",
                        json={"allowed": True, "policy_revision": approval["policy_revision"]},
                    )
            time.sleep(0.05)
