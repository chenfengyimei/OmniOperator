"""技能系统单测：存储（创建/列表/查看/linter）+ 工具派发。"""
import json
import pytest

from omni.memory.skills import SkillStore
from omni.tools.registry import ToolRegistry
from omni.tools.skill_tools import register_skill_tools


def _body() -> str:
    return (
        "# 演示技能\n\n## When to Use\n演示时用。\n\n## Procedure\n1. 第一步；\n2. 第二步。\n\n"
        "## Pitfalls\n无。\n\n## Verification\n跑通即成功。"
    )


def test_create_list_view_roundtrip(tmp_path):
    store = SkillStore(tmp_path)
    ok, msg = store.create("demo-skill", "演示技能", _body())
    assert ok, msg
    items = store.list()
    assert items and items[0]["name"] == "demo-skill"
    assert items[0]["description"] == "演示技能"
    assert items[0]["version"] == "1"
    content = store.view("demo-skill")
    assert content and "When to Use" in content and "version: 1" in content
    assert store.exists("demo-skill")


def test_linter_guards(tmp_path):
    store = SkillStore(tmp_path)
    # 名字格式（大写/空格/特殊字符不行；太短不行）
    ok, _msg = store.create("Bad Name!", "描述", _body())
    assert not ok
    ok, _msg = store.create("ab", "描述", _body())
    assert not ok
    # 描述过长
    ok, msg = store.create("demo-skill", "描" * 61, _body())
    assert not ok and "60" in msg
    # 正文过短
    ok, msg = store.create("demo-skill", "描述", "太短")
    assert not ok
    # 重复创建被拒，且提示先查看合并
    ok, _msg = store.create("demo-skill", "描述", _body())
    assert ok
    ok, msg = store.create("demo-skill", "描述", _body())
    assert not ok and "已存在" in msg


def test_missing_and_path_safety(tmp_path):
    store = SkillStore(tmp_path)
    assert store.view("nope-skill") is None
    # 名字规则天然挡住路径穿越
    assert store.view("../escape") is None
    assert not store.exists("../escape")


def test_tools_dispatch(tmp_path):
    registry = ToolRegistry()
    register_skill_tools(registry, SkillStore(tmp_path))
    assert "还没有任何技能" in registry.dispatch("skills_list", "{}")
    out = registry.dispatch(
        "skill_create",
        json.dumps({"name": "demo-skill", "description": "演示技能", "content": _body()}),
    )
    assert "已创建技能 demo-skill" in out
    assert "demo-skill" in registry.dispatch("skills_list", "{}")
    assert "When to Use" in registry.dispatch(
        "skill_view", json.dumps({"name": "demo-skill"})
    )
    assert "没有技能" in registry.dispatch("skill_view", json.dumps({"name": "nope"}))
    # linter 错误经工具原样回传（LLM 可自行修正后重试）
    assert "错误" in registry.dispatch(
        "skill_create", json.dumps({"name": "Bad", "description": "x", "content": _body()})
    )


def test_seed_skills_are_valid():
    """仓库自带种子技能必须能被解析且过 linter 等价检查（防手改破坏渐进披露）。"""
    from omni.config import SKILLS_DIR
    from omni.memory.skills import MAX_DESCRIPTION, MIN_BODY, parse_skill_md

    names = []
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        parsed = parse_skill_md(skill_md)
        assert parsed is not None, f"{skill_md} 格式损坏（frontmatter 缺失？）"
        meta = parsed["meta"]
        desc = str(meta.get("description", ""))
        assert 0 < len(desc) <= MAX_DESCRIPTION, f"{skill_md} description 超限"
        assert len(parsed["body"]) >= MIN_BODY, f"{skill_md} 正文过短"
        names.append(str(meta.get("name") or skill_md.parent.name))
    assert "bilibili-watch" in names
    assert "nightly-tidy" in names


def test_patch_and_rewrite(tmp_path):
    """skill_patch 只动一小节（版本+1）；skill_rewrite 全文重写并重过 linter。"""
    store = SkillStore(tmp_path)
    ok, msg = store.create("demo-skill", "演示技能", _body())
    assert ok, msg

    ok, msg = store.patch("demo-skill", "Pitfalls", "新坑：不要在雨天操作。")
    assert ok, msg
    after = store.view("demo-skill")
    assert "不要在雨天操作" in after
    assert "演示时用" in after and "跑通即成功" in after  # 其它节原样
    assert "version: 2" in after
    assert store.list()[0]["version"] == "2"

    # 非法 section / 不存在的技能
    ok, msg = store.patch("demo-skill", "Nope", "x")
    assert not ok and "section" in msg
    ok, msg = store.patch("nope-skill", "Pitfalls", "x")
    assert not ok and "不存在" in msg

    # rewrite：描述与正文一起更新
    ok, msg = store.rewrite(
        "demo-skill", "重写后的描述", _body() + "\n补充一行让长度达标。"
    )
    assert ok, msg
    after2 = store.view("demo-skill")
    assert "version: 3" in after2 and "重写后的描述" in after2

    # rewrite 也要过 linter：描述超长 / 正文过短
    ok, msg = store.rewrite("demo-skill", "描" * 61, _body())
    assert not ok and "60" in msg
    ok, msg = store.rewrite("demo-skill", "合法描述", "太短")
    assert not ok and "太短" in msg


def test_references_progressive_disclosure(tmp_path):
    store = SkillStore(tmp_path)
    assert store.create("demo-skill", "演示", _body())[0]
    refs = tmp_path / "demo-skill" / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("只在第三级出现的正文", encoding="utf-8")
    registry = ToolRegistry()
    register_skill_tools(registry, store)
    listing = registry.dispatch("skills_list", {})
    assert "guide.md" not in listing
    main = registry.dispatch("skill_view", {"name": "demo-skill"})
    assert "references/guide.md" in main and "只在第三级" not in main
    assert registry.dispatch("skill_view", {"name": "demo-skill", "path": "references/guide.md"}) == "只在第三级出现的正文"
    assert store.view("demo-skill", "references/missing.md") is None


@pytest.mark.parametrize("path", ["../secret.md", "references/../../secret.md", "C:/secret.md", "C:secret.md", "\\\\server\\share\\secret.md", "references/a.md:stream", "references/../SKILL.md", "references/NUL.txt", "references/guide.md.", "scripts/run.py"])
def test_reference_paths_cannot_escape(tmp_path, path):
    store = SkillStore(tmp_path)
    store.create("demo-skill", "演示", _body())
    with pytest.raises(ValueError):
        store.view("demo-skill", path)


def test_references_reject_binary_and_oversize(tmp_path):
    store = SkillStore(tmp_path)
    store.create("demo-skill", "演示", _body())
    refs = tmp_path / "demo-skill" / "references"
    refs.mkdir()
    target = refs / "guide.md"
    for data in (b"x" * 65537, b"\xff\xfe", b"hello\x00world"):
        target.write_bytes(data)
        with pytest.raises(ValueError):
            store.view("demo-skill", "references/guide.md")


def test_references_listing_is_bounded(tmp_path):
    store = SkillStore(tmp_path)
    store.create("demo-skill", "演示", _body())
    refs = tmp_path / "demo-skill" / "references"
    refs.mkdir()
    for i in range(70):
        (refs / f"guide-{i:02}.md").write_text("正文", encoding="utf-8")
    assert len(store.references("demo-skill")) == 60


def test_skill_and_reference_links_are_rejected(tmp_path):
    store = SkillStore(tmp_path / "skills")
    store.create("demo-skill", "演示", _body())
    external = tmp_path / "external"
    external.mkdir()
    (external / "secret.md").write_text("不可泄露", encoding="utf-8")
    skill = tmp_path / "skills" / "demo-skill"
    # Windows 无符号链接权限时用 junction；两种都是需要阻断的真实文件系统路径。
    import os
    import subprocess
    reference_link = skill / "references"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(reference_link), str(external)], capture_output=True)
        assert result.returncode == 0
    else:
        reference_link.symlink_to(external, target_is_directory=True)
    try:
        assert store.references("demo-skill") == []
        with pytest.raises(ValueError):
            store.view("demo-skill", "references/secret.md")
    finally:
        if os.name == "nt":
            reference_link.rmdir()
        else:
            reference_link.unlink()
