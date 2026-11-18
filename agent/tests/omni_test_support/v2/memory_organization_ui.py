"""Reusable memory_organization_ui fixtures, extracted without collecting test cases."""

import unicodedata
import uuid

import pytest
from playwright.sync_api import expect

from omni_test_support.v2.history_ui import connect
from omni_test_support.v2.ui import v2_server as v2_server


def authorization(server):
    return {"Authorization": "Bearer " + server[1]}


def agent_url(server, agent):
    return f"{server[0]}/api/v1/agents/{agent}"


def get_json(context, server, url, **kwargs):
    response = context.request.get(url, headers=authorization(server), **kwargs)
    assert response.status == 200
    return response.json()


def create_facts(context, server, agent, contents):
    rows = []
    for content in contents:
        response = context.request.post(
            agent_url(server, agent) + "/memory", headers=authorization(server), data={"content": content}
        )
        assert response.status == 201
        rows.append(response.json())
    return rows


def wait_ready(page, context, server, agent, batch_id):
    url = agent_url(server, agent) + f"/memory/organizations/{batch_id}"
    page.wait_for_function(
        """async ({url, token}) => {
          const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}});
          return response.ok && (await response.json()).state === 'ready';
        }""",
        arg={"url": url, "token": server[1]},
        polling=100,
    )
    return get_json(context, server, url)


def submit_batch(page, context, server, agent, facts):
    response = context.request.post(
        agent_url(server, agent) + "/memory/organizations",
        headers=authorization(server),
        data={
            "key": str(uuid.uuid4()),
            "sources": [{"memory_id": row["id"], "revision": row["revision"]} for row in facts],
        },
    )
    assert response.status == 202
    return wait_ready(page, context, server, agent, response.json()["id"])


def confirm(page, label):
    dialog = page.locator("#ui-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name=label, exact=True).click()


def select_batch(page, batch):
    page.locator("#memory-organization-batches").select_option(batch["id"])
    expect(page.locator("#memory-organization-detail")).to_contain_text(batch["id"])
    expect(page.locator("#memory-organization-refresh-detail")).to_be_enabled()


__all__ = [
    "agent_url",
    "authorization",
    "confirm",
    "connect",
    "create_facts",
    "expect",
    "get_json",
    "pytest",
    "select_batch",
    "submit_batch",
    "unicodedata",
    "uuid",
    "v2_server",
    "wait_ready",
]
