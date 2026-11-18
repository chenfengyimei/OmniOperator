"""Reusable history_ui fixtures, extracted without collecting test cases."""

from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from omni_test_support.v2.ui import v2_server as v2_server


def history_page(marker, *, more=False, cursor="", trimmed=False):
    return {
        "items": [
            {
                "sequence": 7,
                "created_at": "2026-11-04T13:00:00.000000+00:00",
                "kind": "maintenance.future_kind",
                "operation_id": marker,
                "outcome": "unknown",
                "data": {"operation": "set"},
            }
        ],
        "next_cursor": cursor,
        "has_more": more,
        "stream": {
            "high_water": 7,
            "trim_revision": 2 if trimmed else 0,
            "deleted_count": 3 if trimmed else 0,
            "last_trim_at": "2026-11-04T12:00:00.000000+00:00" if trimmed else None,
        },
        "history_trimmed": trimmed,
    }


def connect(context, server):
    url, token = server
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.locator("#token").fill(token)
    page.get_by_role("button", name="连接实例", exact=True).click()
    expect(page.locator("nav")).not_to_have_attribute("inert", "")
    return page, errors


def assert_history_contract(context, server, suffix):
    url, token = server
    response = context.request.get(url + "/api/v1" + suffix, headers={"Authorization": "Bearer " + token})
    assert response.status == 200
    value = response.json()
    assert {"items", "next_cursor", "has_more", "stream", "history_trimmed"} <= value.keys()
    assert {"high_water", "trim_revision", "deleted_count", "last_trim_at"} <= value["stream"].keys()


__all__ = [
    "assert_history_contract",
    "connect",
    "expect",
    "history_page",
    "parse_qs",
    "pytest",
    "urlsplit",
    "v2_server",
]
