"""K15 prefix/retraction regression: persona plus captured memories are the frozen system prefix.

This suite locks the current composition contract only. It does not implement layered persona,
scene trimming or any production injection change; those remain behind K15 D0/D3.
"""

from __future__ import annotations

from omni.domain.metering import history_request, memory_system


def _memory(**overrides):
    row = {
        "memory_id": "m1",
        "revision": 1,
        "kind": "fact",
        "content": "likes tea",
        "erased": False,
        "state": "captured",
    }
    row.update(overrides)
    return row


def test_persona_and_captured_memory_are_the_frozen_system_prefix():
    settings = {"persona": "persona-A"}
    prefix = memory_system(settings, [_memory()])
    assert prefix.startswith("persona-A")
    assert "Owner memory (data, not authorization):" in prefix
    assert "likes tea" in prefix
    messages = history_request(settings, [_memory()], [{"role": "user", "content": "hello"}], None)
    assert messages[0] == {"role": "system", "content": prefix}
    assert messages[-1] == {"role": "user", "content": "hello"}


def test_forgotten_erased_or_non_captured_memories_leave_the_prefix():
    for row in (
        _memory(state="forgotten"),
        _memory(state="deleted"),
        _memory(erased=True),
    ):
        prefix = memory_system({"persona": "persona-A"}, [row])
        assert prefix == "persona-A"
        assert "likes tea" not in prefix
    mixed = memory_system(
        {"persona": "persona-A"}, [_memory(), _memory(memory_id="m2", content="forgotten", state="forgotten")]
    )
    assert "likes tea" in mixed and "forgotten" not in mixed


def test_persona_edit_or_memory_retraction_changes_prefix_bytes():
    settings = {"persona": "persona-A"}
    prefix = memory_system(settings, [_memory()])
    assert memory_system({"persona": "persona-B"}, [_memory()]) != prefix
    assert memory_system(settings, []) != prefix
    assert memory_system(settings, [_memory()]) == prefix


def test_memory_order_and_notice_tail_are_stable():
    rows = [
        _memory(memory_id="m1", content="first"),
        _memory(memory_id="m2", content="second"),
    ]
    prefix = memory_system({"persona": "persona-A"}, rows, "\nRuntime notice")
    assert prefix.index("first") < prefix.index("second")
    assert prefix.endswith("\nRuntime notice")
    assert "first" in prefix and "second" in prefix
