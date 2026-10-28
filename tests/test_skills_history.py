"""技能与常驻记忆的一致性单测（D08 / A09、A14）。

这里验的不是"函数返回 True"，而是四类只能在真实文件系统上暴露的失效：

1. **丢写**：两个进程同时"读全文 → 改一节 → 写全文"，后写的整份覆盖前者；
2. **静默覆盖**：用户在编辑器里改过、模型仍按旧内容写回；
3. **半写**：落盘失败时文件被截断/留下半截内容；
4. **不可恢复**：改坏了没有任何旧版本可取回。

跨进程用真实子进程（`subprocess` + go 文件对齐起跑），不是 mock —— 线程锁在单进程里
永远是对的，缺陷只在跨进程出现。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from omni.memory.resident import ENTRY_DELIMITER, ResidentMemory
from omni.memory.skills import SkillStore
from omni.tools.registry import ToolRegistry
from omni.tools.skill_tools import register_skill_tools

ROOT = Path(__file__).resolve().parents[1]


def _body(marker: str = "演示时用") -> str:
    return (
        f"# 演示技能\n\n## When to Use\n{marker}。\n\n## Procedure\n1. 第一步；\n2. 第二步。\n\n"
        "## Pitfalls\n无。\n\n## Verification\n跑通即成功。"
    )


def _store(tmp_path) -> SkillStore:
    return SkillStore(tmp_path / "skills", history_root=tmp_path / "history")


# ---------------------------------------------------------------- 工作台全文保存（D18）


def test_save_raw_roundtrip_cas_and_validation(tmp_path):
    """编辑器全文保存：解析在存储层、CAS 同 rewrite、坏结构直接拒绝。"""
    store = _store(tmp_path)
    ok, msg = store.create("editor-skill", "编辑器技能", _body("第一版"))
    assert ok, msg
    text, digest = store.read("editor-skill")

    edited = text.replace("第一版", "第二版（编辑器改的）")
    ok, msg = store.save_raw(
        "editor-skill", edited, expected_hash=digest, author="user", source="workbench"
    )
    assert ok, msg
    new_text, new_digest = store.read("editor-skill")
    assert "编辑器改的" in new_text and new_digest != digest
    newest = store.history("editor-skill")[0]
    assert newest.source == "workbench" and newest.author == "user"

    # 旧指纹再保存：冲突拒绝，文件不被覆盖
    ok2, msg2 = store.save_raw("editor-skill", edited, expected_hash=digest)
    assert not ok2 and "被改动过" in msg2
    assert store.read("editor-skill")[0] == new_text

    # 结构破坏（没有四节正文）：解析层拒绝
    ok3, msg3 = store.save_raw("editor-skill", "没有结构的纯文本", expected_hash=new_digest)
    assert not ok3 and "结构" in msg3
    # 不存在 / 空内容
    ok4, msg4 = store.save_raw("ghost-skill", _body(), expected_hash=None)
    assert not ok4
    ok5, msg5 = store.save_raw("editor-skill", "", expected_hash=new_digest)
    assert not ok5


def _run_children(code: str, argsets: list[list[str]], go: Path) -> list[str]:
    """同时起多个独立进程，等全部就绪后靠 go 文件一起放行。"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code] + [str(a).replace("{go}", str(go)) for a in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for args in argsets
    ]
    go.write_text("go", encoding="utf-8")
    out: list[str] = []
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=120)
        out.append((stdout.strip() or stderr.strip()).splitlines()[-1] if (stdout.strip() or stderr.strip()) else "")
    return out


# ---------------------------------------------------------------- 修订历史


def test_every_write_keeps_a_retrievable_revision(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    assert store.patch("demo-skill", "Pitfalls", "踩过一次：别在雨天操作。")[0]
    assert store.rewrite("demo-skill", "重写后的描述", _body("结构变了") + "\n补一行。")[0]

    revisions = store.history("demo-skill")
    assert [r.rev for r in revisions] == [3, 2, 1]
    assert [r.version for r in revisions] == [3, 2, 1]
    # 每一版都能取回原文，且内容与当时一致
    first = store.revision("demo-skill", 1)
    assert first is not None and "演示时用" in first[1] and "雨天" not in first[1]
    second = store.revision("demo-skill", 2)
    assert second is not None and "雨天" in second[1]
    # 最新一版就是磁盘上的内容
    latest = store.revision("demo-skill", 3)
    assert latest is not None and latest[1] == store.read("demo-skill")[0]


def test_first_write_snapshots_preexisting_content(tmp_path):
    """历史从 D08 才开始：第一次写入前必须把"已经在磁盘上的内容"也收进去。"""
    root = tmp_path / "skills"
    (root / "legacy-skill").mkdir(parents=True)
    original = "---\nname: legacy-skill\ndescription: 老技能\nversion: 1\n---\n\n## When to Use\n老内容。\n\n## Procedure\n老步骤。\n\n## Pitfalls\n老坑。\n\n## Verification\n老验证。\n"
    (root / "legacy-skill" / "SKILL.md").write_text(original, encoding="utf-8", newline="\n")
    on_disk = (root / "legacy-skill" / "SKILL.md").read_bytes().decode("utf-8")
    store = SkillStore(root, history_root=tmp_path / "history")
    assert store.history("legacy-skill") == []

    ok, msg = store.patch("legacy-skill", "Pitfalls", "补一条新坑。")
    assert ok, msg
    revisions = store.history("legacy-skill")
    assert [r.rev for r in revisions] == [2, 1]
    assert revisions[1].author == "external"  # 老内容不是本次写入产生的
    assert store.revision("legacy-skill", 1)[1] == on_disk


def test_history_is_bounded(tmp_path):
    store = SkillStore(tmp_path / "skills", history_root=tmp_path / "history", history_keep=5)
    assert store.create("demo-skill", "演示技能", _body())[0]
    for i in range(12):
        assert store.patch("demo-skill", "Pitfalls", f"第 {i} 次补充。" + "补" * 20)[0]
    revisions = store.history("demo-skill")
    assert len(revisions) == 5
    assert revisions[0].rev == 13  # 最新仍在


# ---------------------------------------------------------------- CAS


def test_patch_after_hand_edit_conflicts_and_keeps_the_edit(tmp_path):
    """模型 view 过、用户随后用编辑器改了文件：patch 必须冲突，且用户改动不丢。"""
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    store.view("demo-skill")  # 模型看到的是这一版

    path = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    hand = path.read_text(encoding="utf-8").replace("无。", "用户手写的一条坑。")
    path.write_text(hand, encoding="utf-8")

    ok, msg = store.patch("demo-skill", "Pitfalls", "模型想写的内容。")
    assert not ok and "被改动过" in msg
    assert path.read_text(encoding="utf-8") == hand  # 一个字都没动
    # 但用户那次改动已经进了历史，回滚/对比都能取到
    latest = store.history("demo-skill")[0]
    assert latest.author == "external" and latest.summary.startswith("写入前检测到")


def test_expected_hash_guards_against_stale_reads(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    original_hash = store.read("demo-skill")[1]

    assert store.patch("demo-skill", "Pitfalls", "新坑一条。", expected_hash=original_hash[:16])[0]
    # 再用旧指纹写 → 冲突（这正是"用旧确认执行已经变化的目标"）
    ok, msg = store.patch("demo-skill", "Pitfalls", "旧指纹写入。", expected_hash=original_hash)
    assert not ok and "被改动过" in msg
    assert "旧指纹写入" not in store.read("demo-skill")[0]


def test_expected_hash_too_short_is_ignored_not_trusted(tmp_path):
    """少于 8 位的前缀不当指纹用（否则 "a" 能匹配任何哈希）。"""
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    assert store.patch("demo-skill", "Pitfalls", "新坑一条。", expected_hash="abc")[0]


# ---------------------------------------------------------------- 落盘失败


def test_write_failure_leaves_original_intact(tmp_path, monkeypatch):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    before = (tmp_path / "skills" / "demo-skill" / "SKILL.md").read_bytes()
    before_revs = [r.rev for r in store.history("demo-skill")]

    def boom(*_args, **_kwargs):
        raise OSError("磁盘满（模拟）")

    monkeypatch.setattr("omni.memory.skills.atomic_write_text", boom)
    ok, msg = store.patch("demo-skill", "Pitfalls", "这条写不进去。")
    assert not ok and "未修改" in msg
    assert (tmp_path / "skills" / "demo-skill" / "SKILL.md").read_bytes() == before
    assert [r.rev for r in store.history("demo-skill")] == before_revs  # 没有多出"已生效"的修订

    # 恢复后同一操作可以正常完成（不需要手工清理）
    monkeypatch.undo()
    assert store.patch("demo-skill", "Pitfalls", "这条写得进去。")[0]


# ---------------------------------------------------------------- 回滚


def test_rollback_restores_content_without_rewinding_version(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body("原始用法"))[0]
    assert store.patch("demo-skill", "Pitfalls", "后来加的一条坑。")[0]
    assert store.patch("demo-skill", "Pitfalls", "又一条坑。")[0]
    assert store.list()[0]["version"] == "3"

    ok, msg = store.rollback("demo-skill", 1)
    assert ok, msg
    text = store.read("demo-skill")[0]
    assert "原始用法" in text and "后来加的一条坑" not in text  # 真的回到了旧内容
    assert "version: 4" in text  # 版本继续加，不改小
    assert store.list()[0]["version"] == "4"
    # 回滚本身也是一条可查的修订，旧修订仍然在
    revs = store.history("demo-skill")
    assert revs[0].summary == "回滚到 rev 1"
    assert store.revision("demo-skill", 3) is not None


def test_rollback_refuses_broken_or_missing_revision(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    ok, msg = store.rollback("demo-skill", 99)
    assert not ok and "没有 rev 99" in msg
    # 历史里塞一条正文过短的坏修订：回滚也要过 linter
    (tmp_path / "history" / "skills" / "demo-skill").mkdir(parents=True, exist_ok=True)
    import json

    bad = {
        "key": "skills/demo-skill",
        "rev": 50,
        "created_at": "2026-10-27T23:00:00+08:00",
        "author": "external",
        "source": "hand",
        "summary": "坏内容",
        "sha256": "x" * 64,
        "size": 10,
        "version": 9,
        "applied": True,
        "references": {},
        "content": "---\nname: demo-skill\ndescription: 坏的\nversion: 9\n---\n\n太短",
    }
    (tmp_path / "history" / "skills" / "demo-skill" / "0050-xxxxxxxx.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8"
    )
    ok, msg = store.rollback("demo-skill", 50)
    assert not ok and "未通过校验" in msg


# ---------------------------------------------------------------- 附件


def test_reference_manifest_records_drift(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    refs = tmp_path / "skills" / "demo-skill" / "references"
    refs.mkdir()
    (refs / "guide.md").write_text("附件第一版", encoding="utf-8")
    assert store.patch("demo-skill", "Pitfalls", "补一条。")[0]

    latest = store.history("demo-skill")[0]
    assert "references/guide.md" in latest.references  # 这一版记录了附件指纹
    assert store.reference_drift("demo-skill", latest.rev) == []

    (refs / "guide.md").write_text("附件被改了", encoding="utf-8")
    (refs / "extra.md").write_text("新增附件", encoding="utf-8")
    drift = store.reference_drift("demo-skill", latest.rev)
    assert any("改动 references/guide.md" in line for line in drift)
    assert any("新增 references/extra.md" in line for line in drift)


# ---------------------------------------------------------------- 跨进程


_CHILD_PATCH = """
import sys, time
from pathlib import Path
from omni.memory.skills import SkillStore

root, hist, section, content, expected, go = sys.argv[1:7]
store = SkillStore(Path(root), history_root=Path(hist))
deadline = time.time() + 60
while not Path(go).exists() and time.time() < deadline:
    time.sleep(0.005)
ok, msg = store.patch("demo-skill", section, content, expected_hash=(expected or None))
if ok:
    print("OK")
elif "被改动过" in msg:
    print("CONFLICT")
else:
    print("ERR " + msg)
"""


def test_two_processes_editing_different_sections_merge(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    outs = _run_children(
        _CHILD_PATCH,
        [
            [str(tmp_path / "skills"), str(tmp_path / "history"), "Pitfalls", "进程甲写的坑。", "", "{go}"],
            [str(tmp_path / "skills"), str(tmp_path / "history"), "Procedure", "1. 进程乙改的步骤。", "", "{go}"],
        ],
        tmp_path / "go",
    )
    assert outs == ["OK", "OK"], outs
    text = store.read("demo-skill")[0]
    assert "进程甲写的坑" in text and "进程乙改的步骤" in text  # 两次修改都在，没有互相覆盖


def test_two_processes_with_same_expected_hash_only_one_wins(tmp_path):
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    stale = store.read("demo-skill")[1]
    outs = _run_children(
        _CHILD_PATCH,
        [
            [str(tmp_path / "skills"), str(tmp_path / "history"), "Pitfalls", "甲的坑。", stale, "{go}"],
            [str(tmp_path / "skills"), str(tmp_path / "history"), "Pitfalls", "乙的坑。", stale, "{go}"],
        ],
        tmp_path / "go",
    )
    assert sorted(outs) == ["CONFLICT", "OK"], outs
    text = store.read("demo-skill")[0]
    winners = [marker for marker in ("甲的坑", "乙的坑") if marker in text]
    assert len(winners) == 1


# ---------------------------------------------------------------- 路径仍然守得住


def test_write_through_junction_is_refused(tmp_path):
    """技能目录被换成指向外部的 junction：读拒绝，写也必须拒绝。"""
    store = _store(tmp_path)
    assert store.create("demo-skill", "演示技能", _body())[0]
    external = tmp_path / "external"
    external.mkdir()
    target = external / "SKILL.md"
    target.write_text(_body(), encoding="utf-8")

    skill_dir = tmp_path / "skills" / "demo-skill"
    for child in skill_dir.iterdir():
        child.unlink()
    skill_dir.rmdir()
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(skill_dir), str(external)], capture_output=True
        )
        assert result.returncode == 0
    else:
        skill_dir.symlink_to(external, target_is_directory=True)
    try:
        ok, msg = store.patch("demo-skill", "Pitfalls", "想通过链接写进外部目录。")
        assert not ok and "不存在" in msg
        assert "想通过链接写进外部目录" not in target.read_text(encoding="utf-8")
    finally:
        if os.name == "nt":
            skill_dir.rmdir()
        else:
            skill_dir.unlink()


# ---------------------------------------------------------------- 常驻记忆


_CHILD_RESIDENT = """
import sys, time
from pathlib import Path
from omni.memory.resident import ResidentMemory

path, history, content, go = sys.argv[1:5]
rm = ResidentMemory(Path(path), char_limit=8000, history_root=Path(history))
deadline = time.time() + 60
while not Path(go).exists() and time.time() < deadline:
    time.sleep(0.005)
out = rm.add(content)
print("OK" if "已写入" in out else "ERR " + out)
"""


def test_concurrent_adds_from_processes_keep_every_entry(tmp_path):
    """四个进程同时 add：旧实现读全量再写回，必然有若干条被覆盖丢失。"""
    path = tmp_path / "data" / "MEMORY.md"
    path.parent.mkdir(parents=True)
    hist = tmp_path / "data" / "history"
    contents = [f"第 {i} 条：跨进程写入的唯一标记 {i}" for i in range(4)]
    outs = _run_children(
        _CHILD_RESIDENT,
        [[str(path), str(hist), text, "{go}"] for text in contents],
        tmp_path / "go",
    )
    assert outs == ["OK"] * 4, outs
    stored = path.read_text(encoding="utf-8")
    for text in contents:
        assert text in stored, f"丢失条目：{text}\n实际内容：{stored}"
    # 没有写出重复条目（说明每次都是基于最新内容追加）
    assert stored.count(ENTRY_DELIMITER) == len(contents) - 1


def test_index_edit_after_external_change_is_refused(tmp_path):
    path = tmp_path / "MEMORY.md"
    mine = ResidentMemory(path, char_limit=2000)
    mine.add("甲：进行中的事")
    mine.view()  # 记住当前的编号

    other = ResidentMemory(path, char_limit=2000)  # 另一个进程/工作台
    other.add("乙：别人插进来的一条")

    out = mine.replace(1, "甲被改写")
    assert "被改动过" in out
    assert "甲被改写" not in path.read_text(encoding="utf-8")
    assert "乙：别人插进来的一条" in path.read_text(encoding="utf-8")
    # 重新 view 之后就能按新编号操作
    mine.view()
    assert "已更新" in mine.replace(1, "甲被改写")


def test_unreadable_file_is_never_overwritten(tmp_path):
    """读不出来 ≠ 空文件：否则 add 会拿一条新条目覆盖掉整份笔记。"""
    path = tmp_path / "MEMORY.md"
    path.write_bytes(b"\xff\xfe\x00broken")
    rm = ResidentMemory(path, char_limit=2000)
    out = rm.add("新的一条")
    assert "错误" in out and "UTF-8" in out
    assert path.read_bytes() == b"\xff\xfe\x00broken"


def test_resident_history_and_rollback(tmp_path):
    path = tmp_path / "MEMORY.md"
    rm = ResidentMemory(path, char_limit=2000)
    rm.add("第一条")
    rm.add("第二条")
    revs = rm.history()
    assert [r.rev for r in revs] == [2, 1]
    out = rm.rollback(1)
    assert "回滚到 rev 1" in out
    assert [e for _i, e in rm.entries()] == ["第一条"]
    assert rm.history()[0].summary == "回滚到 rev 1"


# ---------------------------------------------------------------- 工具面


def test_skill_tools_expose_hash_history_and_rollback(tmp_path):
    store = _store(tmp_path)
    registry = ToolRegistry()
    register_skill_tools(registry, store)
    assert "已创建" in registry.dispatch(
        "skill_create", {"name": "demo-skill", "description": "演示技能", "content": _body()}
    )
    viewed = registry.dispatch("skill_view", {"name": "demo-skill"})
    assert "内容指纹" in viewed
    digest = viewed.split("内容指纹：")[1][:16]

    assert "已更新" in registry.dispatch(
        "skill_patch",
        {"name": "demo-skill", "section": "Pitfalls", "content": "新坑。", "expected_hash": digest},
    )
    listed = registry.dispatch("skill_history", {"name": "demo-skill"})
    assert "rev 1" in listed and "rev 2" in listed
    assert "回滚到 rev 1" in registry.dispatch("skill_rollback", {"name": "demo-skill", "rev": 1})
    assert "没有 rev" in registry.dispatch("skill_rollback", {"name": "demo-skill", "rev": 99})
