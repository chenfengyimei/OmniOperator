"""外来技能包隔离安装单测（D18 后置项）。

覆盖契约：
- 导入 = 数据化解包进隔离区：正式技能库完全不可见（skills_list 列不出）；
- 路径纪律：zip-slip（../、盘符、ADS、保留名）与类型白名单外的条目整包拒绝；
- 结构/linter：SKILL.md 必须过与 skill_create 相同的校验；
- 体积与数量上限：zip 本体、解包总量、条目数、单文件大小、references 数；
- 激活 = CONFIRM 级动作：防篡改（对清单逐文件 sha256 全等比对）、
  同名冲突锁内复核、来源记进修订历史（provenance）；
- 工具层：审批边界——无人值守（DenyApprover）永远激活不了外来包。
"""

import json
import zipfile

import pytest

from omni.memory.skill_inbox import SkillInbox
from omni.memory.skills import SkillStore
from omni.safety.gates import AllowAllApprover, DenyApprover, Risk
from omni.tools.registry import ToolRegistry
from omni.tools.skill_tools import register_skill_inbox_tools


def test_path_guard_primitive_is_shared_not_duplicated():
    """zip-slip/摘要原语归 system/path_guard：skill_inbox 与 package_reader 消费同一实现，
    各自文件里不再持有副本（同一份安全逻辑两处维护 = 改一处漏一处）。"""
    import omni.plugins.package_reader as reader
    import omni.system.path_guard as guard
    from pathlib import Path

    skill_src = (Path(__file__).resolve().parents[1] / "src" / "omni" / "memory" / "skill_inbox.py").read_text(
        encoding="utf-8"
    )
    reader_src = (Path(__file__).resolve().parents[1] / "src" / "omni" / "plugins" / "package_reader.py").read_text(
        encoding="utf-8"
    )
    for src, name in ((skill_src, "skill_inbox.py"), (reader_src, "package_reader.py")):
        assert "def _safe_parts" not in src and "def safe_parts" not in src, f"{name} 不该再内嵌路径安全实现"
        assert "def _digest" not in src and "def digest" not in src, f"{name} 不该再内嵌摘要实现"
    # 两个域引用的是同一个函数对象
    assert reader.safe_parts is guard.safe_parts
    assert reader.digest is guard.digest


def test_digest_helpers_all_alias_path_guard():
    """全仓命名摘要函数只剩 path_guard.digest 一个实现：
    resident / files.io / community.client / community.server 都是同一函数对象的别名，
    不允许再出现第四份逐字相同的 `sha256(data).hexdigest()` 副本。"""
    import omni.plugins.community.client as community_client
    import omni.plugins.community.server as community_server
    import omni.system.path_guard as guard
    import omni.tools.files.io as files_io
    from omni.memory import resident

    for module, name in (
        (resident, "_digest"),
        (files_io, "sha256_hex"),
        (community_client, "_digest"),
        (community_server, "_digest"),
    ):
        assert getattr(module, name) is guard.digest, f"{module.__name__}.{name} 必须是 path_guard.digest 的别名"


BODY = """## When to Use
需要在多步任务里沉淀可复用流程的时候使用本技能，这句话足够长以通过最短正文的校验要求。
## Procedure
1. 先观察现状；2. 再执行计划；3. 最后验证结果并把经验记录下来供下次复用。
## Pitfalls
- 不要跳过验证步骤，否则容易把错误流程沉淀下来。
## Verification
- 每一步都有记录，最终结果符合预期才算完成。
"""


def _skill_md(name: str = "pkg-demo", description: str = "演示技能：完整可复用流程") -> str:
    return f"---\nname: {name}\ndescription: {description}\nversion: 1\nplatforms: [windows, macos]\n---\n\n" + BODY


def _make_store(tmp_path) -> tuple[SkillStore, SkillInbox]:
    store = SkillStore(tmp_path / "skills", history_root=tmp_path / "history", owner="test")
    inbox = SkillInbox(store)
    return store, inbox


def _zip(path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _drop(inbox: SkillInbox, filename: str, entries: dict[str, bytes]) -> None:
    _zip(inbox._inbox / filename, entries)


# ---------------------------------------------------------------- 导入 → 隔离 → 激活


def test_import_pending_activate_full_flow(tmp_path):
    store, inbox = _make_store(tmp_path)
    _drop(
        inbox,
        "pkg.zip",
        {
            "pkg/SKILL.md": _skill_md().encode("utf-8"),
            "pkg/references/note.md": "参考资料：完整的四节结构说明。\n".encode("utf-8"),
        },
    )

    ok, msg = inbox.import_package("pkg.zip")
    assert ok, msg
    assert "pkg-demo" in msg and "隔离区" in msg
    assert "sha256" in msg  # 清单透明：文件级指纹可见

    # 隔离期对正式技能库不可见
    assert store.list() == []
    assert [p["name"] for p in inbox.pending()] == ["pkg-demo"]
    item = inbox.pending()[0]
    assert item["source_zip"] == "pkg.zip"
    assert [f["path"] for f in item["files"]] == ["SKILL.md", "references/note.md"]

    ok, msg = inbox.activate("pkg-demo")
    assert ok, msg
    # 激活后：正式技能库可见、参考文件可读、来源进了修订历史
    assert [s["name"] for s in store.list()] == ["pkg-demo"]
    assert "references/note.md" in store.references("pkg-demo")
    revisions = store.history("pkg-demo")
    assert revisions and revisions[0].source == "skill_import"
    assert "pkg.zip" in revisions[0].summary
    # 隔离区已清空；重复激活报没有
    assert inbox.pending() == []
    ok, msg = inbox.activate("pkg-demo")
    assert not ok and "没有待激活" in msg


def test_flat_layout_without_wrapper_dir(tmp_path):
    store, inbox = _make_store(tmp_path)
    _drop(
        inbox,
        "flat.zip",
        {
            "SKILL.md": _skill_md("flat-skill").encode("utf-8"),
        },
    )
    ok, msg = inbox.import_package("flat.zip")
    assert ok, msg
    ok, _msg = inbox.activate("flat-skill")
    assert ok
    assert [s["name"] for s in store.list()] == ["flat-skill"]


def test_package_arg_must_be_bare_filename(tmp_path):
    _store, inbox = _make_store(tmp_path)
    for bad in ("../evil.zip", "sub/pkg.zip", "C:/x.zip", ""):
        ok, msg = inbox.import_package(bad)
        assert not ok, bad
        assert "不带路径" in msg or "（空）" in msg
    ok, msg = inbox.import_package("不存在.zip")
    assert not ok and "inbox 里没有" in msg


# ---------------------------------------------------------------- 拒绝面（路径与类型白名单）


@pytest.mark.parametrize(
    "entry",
    [
        "../evil.md",
        "pkg/../../evil.md",
        "C:/evil.md",
        "evil:notes.md",
        "references/run.py",
        "references/setup.exe",
        "install.ps1",
        "extra.md",
        "references/a/b/c/d.md",
    ],
)
def test_unsafe_or_disallowed_entries_reject_whole_package(tmp_path, entry):
    _store, inbox = _make_store(tmp_path)
    entries = {"pkg/SKILL.md": _skill_md().encode("utf-8"), entry: b"x" * 10}
    _drop(inbox, "bad.zip", entries)
    ok, msg = inbox.import_package("bad.zip")
    assert not ok, entry
    assert "错误" in msg
    # 整包拒绝：隔离区不落任何东西
    assert inbox.pending() == []


def test_duplicate_entries_rejected(tmp_path):
    _store, inbox = _make_store(tmp_path)
    path = inbox._inbox / "dup.zip"
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile 对重名条目本身会告警
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("pkg/SKILL.md", _skill_md())
            zf.writestr("pkg/SKILL.md", _skill_md())
    ok, msg = inbox.import_package("dup.zip")
    assert not ok and "重名" in msg


def test_bad_skill_md_rejected(tmp_path):
    _store, inbox = _make_store(tmp_path)
    cases = {
        "没有 SKILL.md": {"pkg/references/x.md": b"ref"},
        "结构坏": {"pkg/SKILL.md": "没有 YAML 头\n" + BODY},
        "名字不合法": {"pkg/SKILL.md": _skill_md("Bad Name")},
        "正文太短": {"pkg/SKILL.md": _skill_md("short-skill").replace(BODY, "太短")},
    }
    for label, entries in cases.items():
        _drop(inbox, "case.zip", {k: v.encode("utf-8") if isinstance(v, str) else v for k, v in entries.items()})
        ok, msg = inbox.import_package("case.zip")
        assert not ok, label
        assert "错误" in msg, label
        assert inbox.pending() == [], label


def test_non_text_or_binary_content_rejected(tmp_path):
    _store, inbox = _make_store(tmp_path)
    _drop(
        inbox,
        "bin.zip",
        {
            "pkg/SKILL.md": _skill_md().encode("utf-8"),
            "pkg/references/x.md": b"\xff\xfe\x00binary",
        },
    )
    ok, msg = inbox.import_package("bin.zip")
    assert not ok and ("UTF-8" in msg or "二进制" in msg)


def test_size_and_count_caps(tmp_path):
    _store, inbox = _make_store(tmp_path)
    # 单文件超限（SKILL.md > 128 KiB）
    _drop(inbox, "big.zip", {"pkg/SKILL.md": (_skill_md() + "x" * 140_000).encode("utf-8")})
    ok, msg = inbox.import_package("big.zip")
    assert not ok and "KiB 上限" in msg
    # 单个参考文件超 64 KiB
    _drop(
        inbox,
        "ref.zip",
        {
            "pkg/SKILL.md": _skill_md().encode("utf-8"),
            "pkg/references/big.md": b"y" * (64 * 1024 + 1),
        },
    )
    ok, msg = inbox.import_package("ref.zip")
    assert not ok and "64 KiB" in msg
    # 条目数超 200
    entries = {"pkg/SKILL.md": _skill_md().encode("utf-8")}
    for i in range(200):
        entries[f"pkg/references/f{i}.md"] = b"z"
    _drop(inbox, "many.zip", entries)
    ok, msg = inbox.import_package("many.zip")
    assert not ok and "条目超过" in msg
    # 压缩包本体超 4 MiB
    import os

    payload = os.urandom(5 * 1024 * 1024)  # 随机字节不可压缩
    _drop(inbox, "huge.zip", {"pkg/SKILL.md": payload})
    ok, msg = inbox.import_package("huge.zip")
    assert not ok and "MiB 上限" in msg


# ---------------------------------------------------------------- 激活边界


def test_activate_detects_tampering_after_import(tmp_path):
    store, inbox = _make_store(tmp_path)
    _drop(
        inbox,
        "pkg.zip",
        {
            "pkg/SKILL.md": _skill_md().encode("utf-8"),
            "pkg/references/note.md": "原始参考内容。\n".encode("utf-8"),
        },
    )
    assert inbox.import_package("pkg.zip")[0]

    # 导入后被改动：激活拒绝
    (store.root / ".quarantine" / "pkg-demo" / "references" / "note.md").write_text(
        "被改过的参考内容。\n", encoding="utf-8"
    )
    ok, msg = inbox.activate("pkg-demo")
    assert not ok and "不一致" in msg and "被改动" in msg
    assert store.list() == []  # 正式库未被污染

    # 导入后被塞进新文件：同样拒绝
    (store.root / ".quarantine" / "pkg-demo" / "references" / "extra.md").write_text("多余文件", encoding="utf-8")
    ok, msg = inbox.activate("pkg-demo")
    assert not ok and "新增" in msg

    # 清单被删：无法核对，拒绝激活
    (store.root / ".quarantine" / "pkg-demo" / "references" / "extra.md").unlink()
    (store.root / ".quarantine" / "pkg-demo" / "references" / "note.md").write_text(
        "原始参考内容。\n", encoding="utf-8"
    )
    (store.root / ".quarantine" / "pkg-demo.manifest.json").unlink()
    ok, msg = inbox.activate("pkg-demo")
    assert not ok and "清单缺失或损坏" in msg


def test_activate_refuses_existing_skill(tmp_path):
    store, inbox = _make_store(tmp_path)
    _drop(inbox, "pkg.zip", {"pkg/SKILL.md": _skill_md("clash-demo").encode("utf-8")})
    assert inbox.import_package("pkg.zip")[0]
    # 等待激活期间有人用 skill_create 建了同名技能：激活必须拒绝
    assert store.create("clash-demo", "演示技能：完整可复用流程", BODY)[0]
    ok, msg = inbox.activate("clash-demo")
    assert not ok and "已有同名技能" in msg
    # 导入阶段的碰撞同样在源头拒绝
    _drop(inbox, "again.zip", {"pkg/SKILL.md": _skill_md("clash-demo").encode("utf-8")})
    ok, msg = inbox.import_package("again.zip")
    assert not ok and "同名技能" in msg


def test_discard_and_reimport_replaces_pending(tmp_path):
    store, inbox = _make_store(tmp_path)
    _drop(inbox, "pkg.zip", {"pkg/SKILL.md": _skill_md().encode("utf-8")})
    assert inbox.import_package("pkg.zip")[0]
    ok, msg = inbox.discard("pkg-demo")
    assert ok and "已丢弃" in msg
    assert inbox.pending() == []
    ok, msg = inbox.discard("pkg-demo")
    assert not ok and "没有" in msg
    # 重复导入同名：替换隔离区里的旧待激活包
    assert inbox.import_package("pkg.zip")[0]
    assert inbox.import_package("pkg.zip")[0]
    assert len(inbox.pending()) == 1


# ---------------------------------------------------------------- 工具层与审批边界


def test_inbox_tools_registered_with_approval_gates(tmp_path):
    _store, inbox = _make_store(tmp_path)
    registry = ToolRegistry()
    register_skill_inbox_tools(registry, inbox)

    assert registry.get("skill_pending").read_only is True
    assert registry.get("skill_import").read_only is False  # 写隔离区：串行
    activate = registry.get("skill_activate")
    assert activate.risk is Risk.CONFIRM and activate.requires_human
    assert registry.get("skill_discard").risk is Risk.SAFE

    _drop(inbox, "pkg.zip", {"pkg/SKILL.md": _skill_md().encode("utf-8")})

    # 导入是 SAFE：无人值守也能做（只进隔离区，不进正式库）
    result = registry.execute("skill_import", {"package": "pkg.zip"}, approver=DenyApprover())
    assert "已导入" in result.to_message()
    assert registry.dispatch("skill_pending", {}).count("pkg-demo") >= 1

    # 激活是 CONFIRM + requires_human：无人值守一律拒绝
    result = registry.execute("skill_activate", {"name": "pkg-demo"}, approver=DenyApprover())
    assert "未获批准" in result.to_message()
    # 有人在环（AllowAllApprover 代表用户已确认）才激活成功
    result = registry.execute("skill_activate", {"name": "pkg-demo"}, approver=AllowAllApprover())
    assert "已激活" in result.to_message()
    assert [s["name"] for s in _store.list()] == ["pkg-demo"]


def test_pending_tool_lists_inbox_zips_and_quarantine(tmp_path):
    _store, inbox = _make_store(tmp_path)
    registry = ToolRegistry()
    register_skill_inbox_tools(registry, inbox)
    _drop(inbox, "a.zip", {"pkg/SKILL.md": _skill_md().encode("utf-8")})
    _drop(inbox, "b.zip", {"pkg/SKILL.md": _skill_md("other-one").encode("utf-8")})

    out = registry.dispatch("skill_pending", {})
    assert "a.zip" in out and "b.zip" in out and "待导入" in out

    assert inbox.import_package("a.zip")[0]
    out = registry.dispatch("skill_pending", {})
    assert "pkg-demo" in out and "文件清单" in out
    assert "b.zip" in out  # 未导入的 zip 仍提示
