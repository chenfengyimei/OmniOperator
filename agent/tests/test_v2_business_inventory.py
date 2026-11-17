"""Business inventory guard: every workbench page is documented once, with an honest status."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web/ui/v2/index.html"
DOC = ROOT / "docs/V2_BUSINESS_INVENTORY.md"
STATUSES = {"可用（离线）", "需配置"}
GROUPS = ("对话与记忆", "知识与技能", "工具与扩展", "设备与平台", "资料与媒体", "自动化", "系统与运维")


def nav_groups():
    html = INDEX.read_text(encoding="utf-8")
    block = re.search(r'<nav aria-label="工作台" inert>(.*?)</nav>', html, re.S)
    assert block, "workbench navigation block is missing"
    return re.findall(r'<p class="nav-group">([^<]+)</p>', block.group(1))


def page_sets():
    html = INDEX.read_text(encoding="utf-8")
    nav = set(re.findall(r'data-page="([a-z-]+)"', html))
    sections = set(re.findall(r'<section id="([a-z-]+)" class="page"', html))
    return nav, sections


def inventory_rows():
    rows = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\| `([a-z-]+)` \| ([^|]+) \| ([^|]+) \|", line)
        if match:
            rows[match.group(1)] = (match.group(2).strip(), match.group(3).strip())
    return rows


def test_every_page_is_documented_and_every_documented_page_exists():
    nav, sections = page_sets()
    rows = inventory_rows()
    assert "默认入口为 V2" in DOC.read_text(encoding="utf-8")
    assert nav == sections, f"navigation/section drift: {nav ^ sections}"
    assert set(rows) == nav, f"inventory/navigation drift: {set(rows) ^ nav}"
    assert len(rows) == len(nav)


def test_navigation_is_grouped_by_business_domain():
    groups = nav_groups()
    assert tuple(groups) == GROUPS, f"navigation groups drifted: {groups}"
    text = DOC.read_text(encoding="utf-8")
    for group in GROUPS:
        assert group in text, f"inventory does not explain navigation group {group}"


def test_inventory_statuses_are_honest_and_bounded():
    rows = inventory_rows()
    assert rows, "business inventory has no page rows"
    for page, (_, status) in rows.items():
        assert status in STATUSES, f"{page} uses an unknown status: {status}"
    text = DOC.read_text(encoding="utf-8")
    assert "| 已验收 |" not in text and "状态 | 已验收" not in text
    for required in ("真实模型", "真实第三方插件", "三平台", "全仓严格类型"):
        assert required in text
