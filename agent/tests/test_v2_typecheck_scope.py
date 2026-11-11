"""Strict-typing scope guard: config, documented scope and files stay in lockstep."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "pyrightconfig.strict.json"
DOC = ROOT / "docs/TYPE_CHECKING.md"


def scope():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    doc_rows = {
        match.group(1)
        for match in re.finditer(r"^\| `(agent/src/omni/domain/[a-z_]+\.py)` \|", DOC.read_text(encoding="utf-8"), re.M)
    }
    return config, doc_rows


def test_config_and_document_list_the_same_strict_files():
    config, doc_rows = scope()
    include = config["include"]
    assert config["typeCheckingMode"] == "strict"
    assert len(include) == len(set(include)), "duplicate strict files"
    assert set(include) == doc_rows, f"strict scope drift: {set(include) ^ doc_rows}"
    for relative in include:
        assert relative.startswith("agent/src/omni/domain/"), f"{relative} is outside the initial domain scope"
        assert "*" not in relative and not relative.endswith("/"), f"{relative} must name one file"
        assert (ROOT / relative).is_file(), f"{relative} does not exist"


def test_document_keeps_the_unfinished_and_exception_baseline():
    text = DOC.read_text(encoding="utf-8")
    assert "全仓严格类型尚未完成" in text
    assert "2321" in text and "domain/model_stream.py" in text
    assert "不得写“全仓严格类型已通过”" in text
    assert "uv run --no-sync pyright --project pyrightconfig.strict.json" in text
