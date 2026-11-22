"""K14 explore_app offline core: structural screen keys, bounded graph and transitions; no device/model."""

from __future__ import annotations

import pytest

from omni.domain.android_explore import (
    MAX_EDGES,
    MAX_SCREENS,
    ExploreGraph,
    build_graph,
    skeleton_hash,
)
from omni.domain.contracts import BusinessError


def _node(index: int = 0, **overrides):
    row = {
        "class": "android.widget.TextView",
        "resource-id": f"app:id/node{index}",
        "actions": ["tap"],
        "text": f"dynamic text {index}",
        "description": f"dynamic description {index}",
        "password": False,
    }
    row.update(overrides)
    return row


def _graph_with_two_screens() -> ExploreGraph:
    graph = ExploreGraph()
    left = graph.add_screen("org.fixture", ".Main", [_node(0)])
    right = graph.add_screen("org.fixture", ".Detail", [_node(1)])
    assert left.screen_id != right.screen_id
    return graph


def test_skeleton_merges_copy_changes_but_not_structure():
    base = skeleton_hash("org.fixture", ".Main", [_node(0)])
    copy_changed = skeleton_hash(
        "org.fixture", ".Main", [_node(0, text="other copy", description="another description")]
    )
    assert base == copy_changed
    assert base != skeleton_hash("org.fixture", ".Main", [_node(0, **{"class": "android.widget.Button"})])
    assert base != skeleton_hash("org.fixture", ".Main", [_node(0, **{"resource-id": "app:id/other"})])
    assert base != skeleton_hash("org.fixture", ".Main", [_node(0, actions=["tap", "toggle"])])


def test_password_nodes_never_enter_the_skeleton():
    base = skeleton_hash("org.fixture", ".Main", [_node(0)])
    with_password = skeleton_hash(
        "org.fixture",
        ".Main",
        [_node(0), _node(1, password=True, **{"class": "android.widget.EditText", "actions": []})],
    )
    assert base == with_password


def test_invalid_observations_are_rejected():
    for nodes in ("no", [1], [{"actions": "tap"}]):
        with pytest.raises(BusinessError):
            skeleton_hash("org.fixture", ".Main", nodes)
    for app, activity in (("", ".Main"), ("app", ""), ("x" * 201, ".Main")):
        with pytest.raises(BusinessError):
            skeleton_hash(app, activity, [_node(0)])


def test_screen_cap_is_explicit():
    graph = ExploreGraph()
    for index in range(MAX_SCREENS + 1):
        graph.add_screen(f"org.fixture{index}", ".Main", [_node(0)])
    assert len(graph.screens) == MAX_SCREENS and graph.truncated
    assert graph.summary()["screens"] == MAX_SCREENS


def test_edges_are_bounded_deduplicated_and_never_fabricated():
    graph = _graph_with_two_screens()
    source, target = list(graph.screens)
    assert graph.add_edge(source, target, "node-1", "tap")
    assert not graph.add_edge(source, target, "node-1", "tap")
    assert graph.add_edge(source, target, "node-2", "tap")
    assert graph.add_edge(source, target, "node-2", "toggle")
    assert not graph.add_edge(source, "missing", "node-3", "tap")
    assert not graph.add_edge(source, source, "node-3", "tap")
    for index in range(MAX_EDGES + 1):
        graph.add_edge(source, target, f"node-{index + 10}", f"action{index}"[:32])
    assert len(graph.edges) == MAX_EDGES and graph.truncated


def test_summary_and_build_graph_are_bounded():
    observations = [
        {"app": "org.fixture", "activity": ".Main", "nodes": [_node(0)]},
        {"app": "org.fixture", "activity": ".Detail", "nodes": [_node(1)]},
    ]
    graph = build_graph(observations, [])
    assert graph.summary()["screens"] == 2 and graph.summary()["apps"] == ["org.fixture"]
    source, target = list(graph.screens)
    built = build_graph(
        observations,
        [{"source": source, "target": target, "node_key": "node-1", "action": "tap"}],
    )
    assert len(built.edges) == 1 and built.edges[0].source == source
    with pytest.raises(BusinessError):
        build_graph([{"app": "x", "activity": ".Y", "nodes": "bad"}], [])
    with pytest.raises(BusinessError):
        build_graph(observations, [{"source": source, "target": target, "node_key": "", "action": "tap"}])
