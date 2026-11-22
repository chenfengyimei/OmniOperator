"""K17 persona growth rendering and managed-cache safety without HTTP or model wiring."""

from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from omni.adapters.persona_files import PersonaGrowthFiles
from omni.domain.contracts import BusinessError
from omni.domain.persona_growth import (
    EMPTY,
    MAX_GROWTH_BYTES,
    MAX_GROWTH_LINES,
    GrowthEntry,
    display_sentence,
    render,
)


def entry(day: str, journal: str, sentence: str) -> GrowthEntry:
    return GrowthEntry(approved_day=day, journal_id=journal, sentence=sentence)


def test_render_orders_newest_first_keeps_same_day_stable_and_hashes_its_bytes():
    values = [
        entry("2026-11-20", "journal-b", "older"),
        entry("2026-11-22", "journal-z", "newest"),
        entry("2026-11-22", "journal-a", "same day first"),
    ]
    rendered = render(values)
    lines = [line for line in rendered.content.splitlines() if line.startswith("- [")]
    assert lines == [
        "- [2026-11-22] same day first",
        "- [2026-11-22] newest",
        "- [2026-11-20] older",
    ]
    assert rendered.approved == 3 and rendered.included == 3 and not rendered.truncated
    assert rendered.sha256 == hashlib.sha256(rendered.content.encode()).hexdigest()
    assert rendered.bytes == len(rendered.content.encode())
    assert render(values) == rendered


def test_display_sentence_collapses_line_breaks_only():
    assert display_sentence("a\r\nb\rc\nd") == "a b c d"
    assert display_sentence("keep  tabs\tand spaces ") == "keep  tabs\tand spaces "


def test_empty_ledger_renders_a_fixed_empty_state():
    rendered = render([])
    assert EMPTY in rendered.content
    assert rendered.approved == rendered.included == 0
    assert rendered.bytes == len(rendered.content.encode())
    assert render([]).content == rendered.content


def test_byte_limit_truncates_with_a_deterministic_marker():
    values = [entry("2026-11-22", f"journal-{index:04d}", "字" * 240) for index in range(6000)]
    rendered = render(values)
    assert rendered.truncated and rendered.included < len(values)
    assert rendered.bytes <= MAX_GROWTH_BYTES
    assert rendered.content.count("\n") <= MAX_GROWTH_LINES
    marker = f"<!-- truncated: {len(values) - rendered.included} more approved entries -->"
    assert rendered.content.rstrip("\n").endswith(marker)
    assert render(values).content == rendered.content


def test_line_limit_truncates_entries_before_the_marker():
    values = [entry("2026-11-22", f"journal-{index:04d}", "x") for index in range(MAX_GROWTH_LINES + 50)]
    rendered = render(values)
    assert rendered.truncated
    assert rendered.content.count("\n") <= MAX_GROWTH_LINES
    assert rendered.included == MAX_GROWTH_LINES - 5  # four header lines plus the marker
    assert "more approved entries" in rendered.content


@pytest.mark.parametrize(
    "value",
    [
        entry("2026-11-2", "journal", "text"),
        entry("2026-11-22", "journal", ""),
        entry("2026-11-22", "journal", "   "),
        entry("2026-11-22", "journal", "x" * 241),
    ],
)
def test_render_rejects_malformed_entries(value):
    with pytest.raises(ValueError):
        render([value])


AGENT = "123e4567-e89b-42d3-a456-426614174000"
OTHER = "123e4567-e89b-42d3-a456-426614174001"


def test_publish_then_read_requires_exactly_the_expected_bytes(tmp_path):
    files = PersonaGrowthFiles(tmp_path / "persona")
    assert files.read(AGENT, b"anything") is None
    built = files.publish(AGENT, b"expected\n")
    assert built and files.read(AGENT, b"expected\n") == (b"expected\n", built)
    assert files.read(AGENT, b"other\n") is None


def test_agents_never_share_a_cache_file_and_identity_must_be_canonical(tmp_path):
    files = PersonaGrowthFiles(tmp_path / "persona")
    files.publish(AGENT, b"one\n")
    assert files.read(OTHER, b"one\n") is None
    files.publish(OTHER, b"two\n")
    assert files.read(AGENT, b"one\n")[0] == b"one\n"
    assert files.read(OTHER, b"two\n")[0] == b"two\n"
    for invalid in ("not-a-uuid", AGENT.upper(), "../../escape"):
        with pytest.raises(BusinessError) as error:
            files.read(invalid, b"one\n")
        assert error.value.code == "persona_growth_invalid_agent"


def test_publish_cleans_stale_temporary_files_and_invalidation_removes_the_cache(tmp_path):
    root = tmp_path / "persona"
    files = PersonaGrowthFiles(root)
    files.publish(AGENT, b"first\n")
    directory = root / AGENT
    (directory / "growth.md.tmp-leftover").write_bytes(b"partial")
    files.publish(AGENT, b"second\n")
    assert not (directory / "growth.md.tmp-leftover").exists()
    assert files.invalidate(AGENT) is True
    assert not (directory / "growth.md").exists()
    assert files.invalidate(AGENT) is False


def test_publish_keeps_the_previous_cache_when_publication_fails(tmp_path, monkeypatch):
    files = PersonaGrowthFiles(tmp_path / "persona")
    files.publish(AGENT, b"old\n")

    def unavailable(source, target, *, replace=False):
        raise OSError("publication failed")

    monkeypatch.setattr("omni.adapters.persona_files.publish_file", unavailable)
    with pytest.raises(OSError, match="publication failed"):
        files.publish(AGENT, b"new\n")
    found = files.read(AGENT, b"old\n")
    assert found is not None and found[0] == b"old\n"
    assert list((tmp_path / "persona" / AGENT).glob("growth.md.tmp-*")) == []


def test_publish_rejects_oversize_content(tmp_path):
    files = PersonaGrowthFiles(tmp_path / "persona")
    with pytest.raises(BusinessError) as error:
        files.publish(AGENT, b"x" * (MAX_GROWTH_BYTES + 1))
    assert error.value.code == "persona_growth_too_large"


def test_hardlinked_cache_is_rejected(tmp_path):
    root = tmp_path / "persona"
    files = PersonaGrowthFiles(root)
    files.publish(AGENT, b"expected\n")
    directory = root / AGENT
    os.link(directory / "growth.md", directory / "second-link")
    with pytest.raises(BusinessError) as error:
        files.read(AGENT, b"expected\n")
    assert error.value.code == "persona_growth_unsafe"


def test_linked_directory_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "persona"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(root), str(outside)],
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    else:
        root.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(BusinessError) as error:
            PersonaGrowthFiles(root).publish(AGENT, b"never\n")
        assert error.value.code == "persona_growth_unsafe"
        assert list(outside.iterdir()) == []
    finally:
        root.unlink() if os.name != "nt" else root.rmdir()


def test_linked_cache_file_is_rejected(tmp_path):
    root = tmp_path / "persona"
    files = PersonaGrowthFiles(root)
    files.publish(AGENT, b"expected\n")
    directory = root / AGENT
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"expected\n")
    target = directory / "growth.md"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks require extra Windows privileges")
    with pytest.raises(BusinessError) as error:
        files.read(AGENT, b"expected\n")
    assert error.value.code == "persona_growth_unsafe"
