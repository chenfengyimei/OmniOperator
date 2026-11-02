"""配置加载单测：.env 注入优先级（缺失填充、空值覆盖、真值不覆盖）。"""

import os

from omni.config import load_env


def _write_env(tmp_path, content: str):
    env = tmp_path / ".env"
    env.write_text(content, encoding="utf-8")
    return env


def test_load_env_fills_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNI_TEST_KEY", raising=False)
    load_env(_write_env(tmp_path, "OMNI_TEST_KEY=abc\n"))
    assert os.environ["OMNI_TEST_KEY"] == "abc"


def test_load_env_overwrites_empty_but_not_real(tmp_path, monkeypatch):
    env = _write_env(tmp_path, "OMNI_TEST_KEY=from-file\n")
    # 既有空值：用文件值填充
    monkeypatch.setenv("OMNI_TEST_KEY", "")
    load_env(env)
    assert os.environ["OMNI_TEST_KEY"] == "from-file"
    # 既有真值：不覆盖（外部预配置优先）
    monkeypatch.setenv("OMNI_TEST_KEY", "real")
    load_env(env)
    assert os.environ["OMNI_TEST_KEY"] == "real"


def test_load_env_skips_comments_and_blank(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNI_TEST_KEY", raising=False)
    env = _write_env(tmp_path, "# 注释\n\nOMNI_TEST_KEY=v\nOMNI_EMPTY=\n")
    load_env(env)
    assert os.environ["OMNI_TEST_KEY"] == "v"
    assert "OMNI_EMPTY" not in os.environ or os.environ["OMNI_EMPTY"] == ""


# ---------------------------------------------------------------- 推理档位（D30）


def test_reasoning_defaults_to_auto_everywhere():
    from omni.config import REASONING_SLOTS, AppConfig

    cfg = AppConfig()
    assert all(getattr(cfg.reasoning, slot) == "auto" for slot in REASONING_SLOTS)


def test_reasoning_rejects_unknown_level():
    import pytest
    from pydantic import ValidationError

    from omni.config import Reasoning

    for bad in ("ultra", "HIGH", "on", ""):
        with pytest.raises(ValidationError):
            Reasoning(chat=bad)


def test_reasoning_level_for_maps_model_string_to_slot():
    from omni.config import Models, Reasoning

    models = Models(chat="deepseek/deepseek-flash", agent_mode="deepseek/deepseek-v4-pro")
    reasoning = Reasoning(chat="off", agent_mode="max", review="high")
    assert reasoning.level_for(models, "deepseek/deepseek-flash") == "off"
    assert reasoning.level_for(models, "deepseek/deepseek-v4-pro") == "max"
    # 没有槽位引用这个模型串 → 不干预（绝不因为"看起来像"就套一个档位）
    assert reasoning.level_for(models, "openai/gpt-5.5") == "auto"


def test_reasoning_slot_priority_is_declaration_order():
    from omni.config import Models, Reasoning

    # 同一模型同时出现在 chat 与 review：按 REASONING_SLOTS 的顺序取第一个（chat）
    models = Models(chat="deepseek/deepseek-flash", review="deepseek/deepseek-flash")
    reasoning = Reasoning(chat="low", review="max")
    assert reasoning.level_for(models, "deepseek/deepseek-flash") == "low"
