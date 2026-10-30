"""技能中心：统计、归档恢复、外来包、设置 CAS 与真实 Chromium 操作。

范围声明：
- 统计只读：不执行技能、不调用模型、不写配置；
- 归档不是硬删除：移出技能库但保留 .archive/ 与修订历史，可恢复；
- 外来包上传只写收件箱，导入仍走类型白名单/隔离区，激活仍有人工确认；
- 技能设置与普通配置共用文件锁、白名单、revision CAS，保存后重启生效。
"""
import io
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from test_web import _client


BODY = (
    "## When to Use\n演示场景：需要复用一段完整流程时。\n\n"
    "## Procedure\n1. 先观察；2. 再执行；3. 最后验证。\n\n"
    "## Pitfalls\n不要跳过验证。\n\n"
    "## Verification\n回读结果并确认字段一致。"
)


def _skill_md(name: str = "pkg-skill", description: str = "外来包技能") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "version: 1\n"
        "platforms: [windows, macos]\n"
        "---\n\n" + BODY + "\n"
    )


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture
def web(tmp_path):
    client, runtime = _client(
        tmp_path,
        skills_root=tmp_path / "skills",
        config_text="# 保留注释\nskills:\n  history_keep: 50\n  max_write_chars: 8000\n",
    )
    try:
        yield client, runtime
    finally:
        client.close()
        assert runtime.close()["complete"]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------- 总览（只读）


def test_skill_overview_reports_lifecycle_without_writing(web, tmp_path):
    client, runtime = web
    assert runtime.skills.create("agent-skill", "agent 写的", BODY)[0]
    assert runtime.skills.patch("agent-skill", "Pitfalls", "补一条坑。" )[0]
    assert runtime.skills.create("user-skill", "用户写的", BODY, author="user", source="workbench")[0]
    refs = runtime.skills.root / "agent-skill" / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("按需读取的参考", encoding="utf-8")

    before = _snapshot(tmp_path)
    response = client.get("/api/skills/overview")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["totals"]["skills"] == 2 and data["totals"]["archived"] == 0
    assert data["totals"]["references"] == 1 and data["totals"]["invalid"] == 0
    assert data["revisions"]["total"] == 3  # agent 创建+补坑，用户创建
    assert data["pending"]["count"] == 2
    assert {item["name"] for item in data["pending"]["items"]} == {"agent-skill"}
    assert data["pending"]["items"][0]["base_rev"] is not None
    assert len(data["activity"]) == 14 and data["activity"][-1]["date"]
    assert sum(item["count"] for item in data["activity"]) == 3
    assert data["inbox_count"] == 0 and data["quarantine_count"] == 0
    details = {item["name"]: item for item in data["skills"]}
    assert details["agent-skill"]["references"] == 1
    assert details["agent-skill"]["hash"]
    assert details["agent-skill"]["pending"] == 2
    assert details["agent-skill"]["valid"] is True
    assert details["agent-skill"]["sections_missing"] == []
    assert data["limits"]["max_body_chars"] == 24000
    assert _snapshot(tmp_path) == before, "只读统计不能写文件"


def test_skill_overview_surfaces_broken_skill_and_archive_accepts_it(web):
    client, runtime = web
    broken = runtime.skills.root / "broken-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text("这不是 SKILL.md 结构", encoding="utf-8")
    data = client.get("/api/skills/overview").json()
    item = next(row for row in data["skills"] if row["name"] == "broken-skill")
    assert item["valid"] is False and item["sections_missing"]
    assert data["totals"]["invalid"] == 1
    archived = client.post(
        "/api/skills/broken-skill/archive", json={"expected_hash": item["hash"], "reason": "结构损坏"}
    )
    assert archived.status_code == 200, archived.text
    data = client.get("/api/skills/overview").json()
    assert all(row["name"] != "broken-skill" for row in data["skills"])
    assert data["archived"][0]["name"] == "broken-skill" and data["archived"][0]["valid"] is False


# ---------------------------------------------------------------- 设置与 CAS


def test_skill_settings_are_pending_and_keep_comments(web):
    client, runtime = web
    before = client.get("/api/skills/settings").json()
    assert before["saved"]["skills"]["history_keep"] == 50
    assert not before["restart_required"]
    response = client.put(
        "/api/skills/settings",
        json={"revision": before["revision"], "updates": {"skills": {"history_keep": 12, "max_write_chars": 6000}}},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["saved"]["skills"] == {"history_keep": 12, "max_write_chars": 6000}
    assert data["running"]["skills"] == {"history_keep": 50, "max_write_chars": 8000}
    assert data["restart_required"] is True
    assert "# 保留注释" in runtime.paths.config.read_text(encoding="utf-8")
    assert runtime.config.skills.history_keep == 50
    assert client.get("/api/skills/settings").json() == data


def test_skill_settings_reject_invalid_and_stale_updates(web):
    client, runtime = web
    revision = client.get("/api/skills/settings").json()["revision"]
    before = runtime.paths.config.read_bytes()
    for updates in (
        {"skills": {"history_keep": 1}},
        {"skills": {"max_write_chars": 24001}},
        {"skills": {"made_up": 1}},
        {"persona": {"name": "越界"}},
        {},
    ):
        response = client.put("/api/skills/settings", json={"revision": revision, "updates": updates})
        assert response.status_code == 422, response.text
        assert runtime.paths.config.read_bytes() == before

    assert client.post("/api/system/config", json={"updates": {"persona": {"name": "别的页面"}}}).status_code == 200
    response = client.put(
        "/api/skills/settings",
        json={"revision": revision, "updates": {"skills": {"history_keep": 20}}},
    )
    assert response.status_code == 409 and "重新读取" in response.json()["detail"]


# ---------------------------------------------------------------- 创建 / 归档 / 恢复


def test_skill_create_archive_restore_and_history(web):
    client, runtime = web
    created = client.post(
        "/api/skills", json={"name": "ui-skill", "description": "界面技能", "content": BODY}
    )
    assert created.status_code == 200, created.text
    loaded = client.get("/api/skills/ui-skill").json()
    assert loaded["hash"] and "When to Use" in loaded["content"]

    # 错误指纹：拒绝且不移除
    conflict = client.post(
        "/api/skills/ui-skill/archive", json={"expected_hash": "deadbeefdeadbeef"}
    )
    assert conflict.status_code == 409
    assert [item["name"] for item in client.get("/api/skills").json()["skills"]] == ["ui-skill"]

    archived = client.post(
        "/api/skills/ui-skill/archive", json={"expected_hash": loaded["hash"], "reason": "暂时不用"}
    )
    assert archived.status_code == 200, archived.text
    assert client.get("/api/skills").json()["skills"] == []
    assert client.get("/api/skills/ui-skill").status_code == 404
    overview = client.get("/api/skills/overview").json()
    assert overview["archived"][0]["name"] == "ui-skill"
    assert overview["archived"][0]["archive_reason"] == "暂时不用"
    assert overview["archived"][0]["revision_count"] >= 1

    restored = client.post("/api/skills/archive/ui-skill/restore")
    assert restored.status_code == 200, restored.text
    assert [item["name"] for item in client.get("/api/skills").json()["skills"]] == ["ui-skill"]
    history = client.get("/api/skills/ui-skill/history").json()["revisions"]
    assert history[0]["source"] == "workbench"


def test_skill_create_rejects_duplicate_and_bad_linter(web):
    client, _runtime = web
    assert client.post("/api/skills", json={"name": "dup-skill", "description": "重复", "content": BODY}).status_code == 200
    assert client.post("/api/skills", json={"name": "dup-skill", "description": "重复", "content": BODY}).status_code == 400
    assert client.post("/api/skills", json={"name": "Bad Name", "description": "坏名字", "content": BODY}).status_code == 400
    assert client.post("/api/skills", json={"name": "short-skill", "description": "太短", "content": "短"}).status_code == 400


# ---------------------------------------------------------------- 外来包


def test_skill_package_upload_import_activate_and_discard(web):
    client, runtime = web
    payload = _zip_bytes({"pkg/SKILL.md": _skill_md(), "pkg/references/note.md": "参考文本\n"})
    upload = client.post(
        "/api/skills/inbox/upload",
        params={"filename": "demo.zip"},
        content=payload,
        headers={"Content-Type": "application/zip"},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["inbox"][0]["name"] == "demo.zip"

    imported = client.post("/api/skills/inbox/demo.zip/import")
    assert imported.status_code == 200, imported.text
    quarantine = imported.json()["quarantine"]
    assert quarantine and quarantine[0]["name"] == "pkg-skill"
    assert quarantine[0]["can_activate"] is True
    assert quarantine[0]["files"][1]["sha256"]

    assert client.get("/api/skills").json()["skills"] == []
    activated = client.post("/api/skills/quarantine/pkg-skill/activate")
    assert activated.status_code == 200, activated.text
    assert [item["name"] for item in client.get("/api/skills").json()["skills"]] == ["pkg-skill"]
    assert runtime.skills.references("pkg-skill") == ["references/note.md"]
    assert client.get("/api/skills/inbox").json()["quarantine"] == []

    # 再次上传 → 导入 → 篡改 → 激活必须 409，丢弃只动隔离区
    tamper_payload = _zip_bytes({"pkg/SKILL.md": _skill_md("tamper-skill")})
    assert client.post(
        "/api/skills/inbox/upload",
        params={"filename": "second.zip"},
        content=tamper_payload,
        headers={"Content-Type": "application/zip"},
    ).status_code == 200
    assert client.post("/api/skills/inbox/second.zip/import").status_code == 200
    (runtime.skill_inbox._quarantine / "tamper-skill" / "SKILL.md").write_text(
        _skill_md("tamper-skill").replace("外来包技能", "被改过的描述"), encoding="utf-8"
    )
    tampered = client.post("/api/skills/quarantine/tamper-skill/activate")
    assert tampered.status_code == 409 and "不一致" in tampered.json()["detail"]
    assert client.post("/api/skills/quarantine/tamper-skill/discard").status_code == 200
    assert client.get("/api/skills/inbox").json()["quarantine"] == []
    assert client.post("/api/skills/quarantine/tamper-skill/activate").status_code == 404


def test_skill_package_upload_rejects_bad_input(web):
    client, _runtime = web
    for filename in ("../evil.zip", "C:/evil.zip", "plain.txt", "", "CON.zip", "trail.zip "):
        response = client.post(
            "/api/skills/inbox/upload",
            params={"filename": filename},
            content=b"PK\x03\x04not-really",
            headers={"Content-Type": "application/zip"},
        )
        assert response.status_code in (400, 422), (filename, response.text)
    bad = client.post(
        "/api/skills/inbox/upload",
        params={"filename": "not-zip.zip"},
        content=b"not a zip",
        headers={"Content-Type": "application/zip"},
    )
    assert bad.status_code == 400 and "zip" in bad.json()["detail"]
    assert client.delete("/api/skills/inbox/ghost.zip").status_code == 404


# ---------------------------------------------------------------- 鉴权


def test_skill_center_routes_require_pairing(tmp_path):
    client, runtime = _client(
        tmp_path, skills_root=tmp_path / "skills", pair=False, config_text="skills:\n  history_keep: 50\n"
    )
    try:
        assert client.get("/api/skills/overview").status_code == 401
        assert client.get("/api/skills/settings").status_code == 401
        assert client.get("/api/skills/inbox").status_code == 401
        assert client.post("/api/skills", json={"name": "x-skill", "description": "x", "content": BODY}).status_code == 401
    finally:
        client.close()
        assert runtime.close()["complete"]


# ---------------------------------------------------------------- 真实 Chromium


@pytest.fixture
def skill_ui(tmp_path):
    client, runtime = _client(
        tmp_path,
        skills_root=tmp_path / "skills",
        config_text="# 保留注释\nskills:\n  history_keep: 50\n  max_write_chars: 8000\n",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            from urllib.parse import urlsplit

            url = urlsplit(request.request.url)
            headers = {"Content-Type": request.request.headers.get("content-type", "application/json")}
            response = client.request(
                request.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=request.request.post_data_buffer,
                headers=headers,
            )
            request.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "")},
            )

        page.route("**/*", route)
        try:
            yield page, client, runtime, errors
        finally:
            page.close()
            browser.close()
            assert runtime.close()["complete"]
            client.close()


def _open_skill_center(page):
    page.goto("http://127.0.0.1/")
    page.locator(".hub-open").click()
    page.locator('.hub-nav a[data-go="sec-skills"]').click()
    page.locator("#sec-skills:not(.hidden)").wait_for()
    page.locator("#skills-hub-metrics .metric-card").first.wait_for()
    page.wait_for_function("!document.querySelector('#skills-hub-settings-controls').disabled")


def test_settings_skill_center_renders_and_saves(web):
    client, runtime = web
    runtime.skills.create("agent-skill", "agent 写的", BODY)
    runtime.skills.patch("agent-skill", "Pitfalls", "补一条坑。")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            from urllib.parse import urlsplit

            url = urlsplit(request.request.url)
            headers = {"Content-Type": request.request.headers.get("content-type", "application/json")}
            response = client.request(
                request.request.method,
                url.path + ("?" + url.query if url.query else ""),
                content=request.request.post_data_buffer,
                headers=headers,
            )
            request.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "")},
            )

        page.route("**/*", route)
        _open_skill_center(page)
        assert "agent-skill" in page.locator("#skills-hub-list").inner_text()
        assert "共 2 条" in page.locator("#skills-hub-pending").inner_text()
        assert "修订快照" in page.locator("#skills-hub-metrics").inner_text()

        page.locator("#cfg-skills-history-keep").fill("13")
        page.locator("#skills-hub-settings-save").click()
        page.locator("#skills-hub-settings-msg").get_by_text("已保存", exact=False).wait_for()
        saved = client.get("/api/skills/settings").json()["saved"]["skills"]
        assert saved["history_keep"] == 13
        assert runtime.config.skills.history_keep == 50
        page.locator("details.skills-settings-diff > summary").click()
        assert "运行 50" in page.locator("#skills-hub-settings-running").inner_text()
        assert not errors
        browser.close()


def test_settings_skill_center_create_archive_restore(skill_ui):
    page, client, runtime, errors = skill_ui
    _open_skill_center(page)
    page.locator('[data-action="skills-hub:new"]').first.click()
    page.locator("#skill-new-name").fill("ui-skill")
    page.locator("#skill-new-description").fill("界面新建技能")
    page.locator("#skill-new-submit").click()
    page.locator("#skill-new-msg").get_by_text("已创建", exact=False).wait_for()
    page.locator("#skills-hub-list").get_by_text("ui-skill", exact=False).wait_for()

    page.locator("#skills-hub-list .skills-hub-item").filter(has_text="ui-skill").locator(
        '[data-action="skills-hub:archive"]'
    ).click()
    page.locator("#ui-dialog").get_by_role("button", name="归档", exact=True).click()
    page.locator("#skills-hub-archived-count").get_by_text("1", exact=True).wait_for()
    assert client.get("/api/skills").json()["skills"] == []

    page.locator("#sec-skills details.skills-archive summary").click()
    page.locator("#skills-hub-archived").get_by_text("ui-skill", exact=False).wait_for()
    page.locator('#skills-hub-archived [data-action="skills-hub:restore"]').click()
    page.locator("#ui-dialog").get_by_role("button", name="恢复", exact=True).click()
    page.locator("#skills-hub-list").get_by_text("ui-skill", exact=False).wait_for()
    assert [item["name"] for item in client.get("/api/skills").json()["skills"]] == ["ui-skill"]
    assert not errors


def test_settings_skill_center_imports_and_activates_package(skill_ui):
    page, client, runtime, errors = skill_ui
    payload = _zip_bytes({"pkg/SKILL.md": _skill_md(), "pkg/references/note.md": "参考文本\n"})
    assert runtime.skill_inbox.upload("demo.zip", payload)[0]
    _open_skill_center(page)
    page.locator("#skills-hub-inbox").get_by_text("demo.zip", exact=False).wait_for()
    page.locator('#skills-hub-inbox [data-action="skills-hub:inbox-import"]').click()
    page.locator("#skills-hub-quarantine").get_by_text("pkg-skill", exact=False).wait_for()
    page.locator("#skills-hub-quarantine details.skills-manifest summary").click()
    assert "sha256" in page.locator("#skills-hub-quarantine").inner_text()
    page.locator('#skills-hub-quarantine [data-action="skills-hub:quarantine-activate"]').click()
    page.locator("#ui-dialog").get_by_role("button", name="激活", exact=True).click()
    page.locator("#skills-hub-list").get_by_text("pkg-skill", exact=False).wait_for()
    assert [item["name"] for item in client.get("/api/skills").json()["skills"]] == ["pkg-skill"]
    assert not errors


@pytest.mark.parametrize("width", [390, 760, 1024, 1440])
def test_settings_skill_center_layout_and_unique_controls(skill_ui, width):
    page, _client_, _runtime, errors = skill_ui
    page.set_viewport_size({"width": width, "height": 900})
    page.goto("http://127.0.0.1/")
    page.locator("body.ui-ready").wait_for()
    page.locator('.ui-settings:visible,[data-action="hub:open"]:visible').first.click()
    page.locator('.hub-nav a[data-go="sec-skills"]').click()
    page.locator("#skills-hub-metrics .metric-card").first.wait_for()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    # 空态必须横跨内容区，不能只占网格第一列被挤成竖排（真实回归：40px 宽 / 500px 高）
    page.locator("#skills-hub-activity .empty-rich").wait_for()
    activity = page.locator("#skills-hub-activity .empty-rich").bounding_box()
    assert activity and activity["width"] >= 260 and activity["height"] < 360, activity
    list_empty = page.locator("#skills-hub-list .empty-rich")
    if list_empty.count():
        box = list_empty.bounding_box()
        assert box and box["width"] >= 260, box
    assert page.locator("#cfg-skills-history-keep").count() == 1
    assert page.locator("#cfg-limits-history").count() == 0
    assert not errors
