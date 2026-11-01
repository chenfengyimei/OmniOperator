"""D36：人格场景层（群聊/私聊各一段，只做追加）。

抄 surfcore `docs/cases/2026-10-31-persona-layers.md` 与
`docs/cases/2026-10-31-group-tiers-tone.md` 的**判定逻辑**，不抄形态
（它是四层文件 + DSH 插件 + 备份回档；我们是 config.yaml + 一个提示词组装函数）。

抄到的三条纪律：

1. **核心层是底座**：场景层为空时行为与旧版**逐字节一致**（不迁移、不强制生成空文件）；
2. **每层一个显式分支**：不给「未知的场景」兜底路径——未知场景就是没有场景层；
3. **注入顺序固定**：核心 → 场景，场景层只**追加**，不改写核心。

我们的取舍：**只加一层「场景层」，只分群聊/私聊两档**。不抄 tone/daily 两层——
那两层的前提是它有一整套「人格卡编辑 + 备份 + 回档」的产品形态，我们只有
config.yaml 里的一个正文，加两层只会让保存契约变复杂。

**全部离线**：不联网、不碰真实账号与真实目录、不调模型。
"""
from __future__ import annotations

from omni.core.context import (
    SCENE_HEADER_GROUP,
    SCENE_HEADER_PRIVATE,
    system_prompt,
)
from omni.system.persona_rules import (
    PERSONA_SCENE_MAX_CHARS,
    PERSONA_SCENE_MIN_CHARS,
    PersonaRuleError,
    check_persona_scene,
    validate_persona_update,
)

CORE = "你是核心人格，这一行足够长，可以通过最小长度闸门。"


# ---------- 组装：核心 → 场景 ----------


def test_empty_scene_keeps_prompt_identical_to_legacy() -> None:
    """**核心层是底座**：场景层为空 → 与旧版逐字节一致。

    这条是最重要的兼容性保证：没有配置场景层的用户，行为一点不变。
    """
    legacy = system_prompt(CORE, include_personal=True)
    with_empty = system_prompt(
        CORE, include_personal=True, group_prompt="", private_prompt="", is_group=False
    )
    assert with_empty == legacy
    group_legacy = system_prompt(CORE, include_personal=False)
    with_empty_group = system_prompt(
        CORE, include_personal=False, group_prompt="", private_prompt="", is_group=True
    )
    assert with_empty_group == group_legacy


def test_group_scene_is_appended_only_in_group() -> None:
    """群聊场景层只在群聊里出现。"""
    group = system_prompt(
        CORE, include_personal=False,
        group_prompt="在群里说话要简短，不要抢话。",
        private_prompt="在私聊里可以展开讲。",
        is_group=True,
    )
    assert SCENE_HEADER_GROUP in group
    assert "在群里说话要简短" in group
    assert "在私聊里可以展开讲" not in group


def test_private_scene_is_appended_only_in_private() -> None:
    """私聊场景层只在私聊里出现。"""
    private = system_prompt(
        CORE, include_personal=True,
        group_prompt="在群里说话要简短，不要抢话。",
        private_prompt="在私聊里可以展开讲。",
        is_group=False,
    )
    assert SCENE_HEADER_PRIVATE in private
    assert "在私聊里可以展开讲" in private
    assert "在群里说话要简短" not in private


def test_scene_comes_after_core_and_before_protocol() -> None:
    """注入顺序固定：核心 → 场景 → 固定协议。"""
    text = system_prompt(
        CORE, include_personal=True,
        group_prompt="这是群聊场景层的内容，足够长。", is_group=True,
    )
    assert text.index(CORE) < text.index(SCENE_HEADER_GROUP)
    assert text.index(SCENE_HEADER_GROUP) < text.index("运行上下文协议")


def test_scene_never_replaces_core() -> None:
    """场景层只做**追加**，不改写核心层。"""
    text = system_prompt(
        CORE, include_personal=True,
        private_prompt="这段会试图改写核心，但它只是追加。", is_group=False,
    )
    assert CORE in text
    assert text.startswith(CORE)


def test_group_scene_keeps_group_policy() -> None:
    """群聊场景层与群聊资料策略是两件事，都要保留。"""
    text = system_prompt(
        CORE, include_personal=False,
        group_prompt="在群里说话要简短，不要抢话。", is_group=True,
    )
    assert SCENE_HEADER_GROUP in text
    assert "当前是群聊场景" in text


def test_non_owner_private_uses_private_scene() -> None:
    """非所有者的私聊是「有个人上下文、但属于私聊场景」。

    `include_personal`（注入哪些资料）与 `is_group`（用哪个人格场景）
    是**正交**的两件事——这个用例守住它们不被混为一谈。
    """
    text = system_prompt(
        CORE, include_personal=True,
        group_prompt="群里的说话方式", private_prompt="私聊的说话方式",
        is_group=False,
    )
    assert SCENE_HEADER_PRIVATE in text
    assert "群里的说话方式" not in text
    # 有个人上下文 → 不加群聊资料策略
    assert "当前是群聊场景" not in text


# ---------- 保存契约 ----------


def test_empty_scene_is_valid() -> None:
    """空场景层 = 没有这一层，**合法**。

    与核心层「空=拒绝」不同：核心层清空会让人设凭空消失，场景层清空只是
    回到「没有场景层」的状态，两种情况行为完全一样，不存在二义。
    """
    check_persona_scene([], label="群聊场景层")
    check_persona_scene(["   "], label="群聊场景层")
    check_persona_scene("", label="群聊场景层")


def test_scene_with_credentials_is_rejected() -> None:
    """场景层同样每轮进提示词，凭据一样不能写。"""
    with __import__("pytest").raises(PersonaRuleError) as exc:
        check_persona_scene(["这里面藏着 sk-abcdefghijklmnopqrstuvwxyz123456"], label="群聊场景层")
    assert "凭据" in str(exc.value)


def test_scene_too_short_is_rejected() -> None:
    with __import__("pytest").raises(PersonaRuleError) as exc:
        check_persona_scene(["短"], label="私聊场景层")
    assert str(PERSONA_SCENE_MIN_CHARS) in str(exc.value)


def test_scene_too_long_is_rejected() -> None:
    with __import__("pytest").raises(PersonaRuleError) as exc:
        check_persona_scene(["字" * (PERSONA_SCENE_MAX_CHARS + 1)], label="私聊场景层")
    assert str(PERSONA_SCENE_MAX_CHARS) in str(exc.value)


def test_validate_update_checks_scene_layers() -> None:
    """保存其它段不该被人格规则挡住；动了场景层才校验。"""
    # 不动 persona → 放行
    validate_persona_update({"qq": {"enabled": True}})
    # 场景层合法 → 放行
    validate_persona_update({"persona": {"group_prompt": ["群里说话要简短，别抢话。"]}})
    # 场景层含凭据 → 拒绝
    try:
        validate_persona_update({
            "persona": {"private_prompt": ["长度足够但是藏着 sk-abcdefghijklmnopqrstuvwxyz123456"]}
        })
    except PersonaRuleError as exc:
        assert "凭据" in str(exc)
    else:
        raise AssertionError("含凭据的场景层应当被拒绝")


def test_scene_update_requires_string_list() -> None:
    try:
        validate_persona_update({"persona": {"group_prompt": "不是列表"}})
    except PersonaRuleError as exc:
        assert "字符串列表" in str(exc)
    else:
        raise AssertionError("非列表的场景层应当被拒绝")
