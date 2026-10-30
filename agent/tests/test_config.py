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
