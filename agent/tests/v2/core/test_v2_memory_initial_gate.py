"""K15 isInitial gate regression: text alone never promotes a memory; only explicit is_first does.

The conservative current behavior is intentional until the owner chooses D0-D5 in
docs/v2/contracts/V2_MEMORY_INITIAL.md. This suite locks the gate; it does not implement isInitial.
"""

from __future__ import annotations

from omni.domain.memory_scoring import score_memory


def test_first_meet_text_alone_does_not_promote():
    common = {"source": "qq-owner", "is_owner": True, "is_private": True}
    plain = score_memory("很高兴认识你", **common)
    first_text = score_memory("第一次见面，很高兴认识你", **common)
    assert first_text == plain
    assert first_text["kind"] != "first"
    assert "first" not in first_text["tags"]
    assert not first_text["pinned"]


def test_explicit_is_first_is_the_only_promotion_path_for_owner():
    promoted = score_memory(
        "很高兴认识你",
        source="qq-owner",
        is_owner=True,
        is_private=True,
        is_first=True,
    )
    assert promoted["importance"] == 100
    assert promoted["pinned"] and promoted["kind"] == "first"
    assert "first" in promoted["tags"]


def test_explicit_is_first_without_owner_does_not_force_full_importance_or_pin():
    promoted = score_memory("第一次一起看展览", source="qq-group", is_owner=False, is_first=True)
    assert promoted["kind"] == "first" and "first" in promoted["tags"]
    assert promoted["importance"] == 38
    assert not promoted["pinned"]
