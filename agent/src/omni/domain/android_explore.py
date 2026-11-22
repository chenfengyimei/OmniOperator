"""Pure Android explore_app state graph: structural screen keys, bounded edges and summaries.

The graph only consumes already-authorized observations and observed action labels; it never captures,
executes, persists or calls a model. Text/description/password values never enter the skeleton hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import cast

from .contracts import BusinessError

MAX_SCREENS = 200
MAX_EDGES = 1000
MAX_NODES = 128
MAX_TEXT = 200
MAX_ACTION = 32


def _invalid(message: str) -> BusinessError:
    return BusinessError("android_explore_invalid", message, status=422)


def _text(value: object, limit: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise _invalid(f"{field_name} must be a string")
    text = value.strip()
    if not text or len(text) > limit:
        raise _invalid(f"{field_name} must be 1-{limit} characters")
    return text


def _nodes(value: object) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        raise _invalid("nodes must be an observation list")
    items = list(cast(list[object] | tuple[object, ...], value))
    if len(items) > MAX_NODES:
        raise _invalid(f"nodes must be at most {MAX_NODES}")
    result: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, dict):
            raise _invalid("each node must be an object")
        result.append(cast(dict[str, object], item))
    return result


def _structure(nodes: object) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    structure: list[dict[str, object]] = []
    seen_actions: set[str] = set()
    for node in _nodes(nodes):
        if node.get("password") is True:
            continue
        actions = node.get("actions", [])
        if not isinstance(actions, list):
            raise _invalid("node actions must be a list")
        checked_actions = sorted(str(action)[:MAX_ACTION] for action in cast(list[object], actions))
        structure.append(
            {
                "class": str(node.get("class", ""))[:128],
                "resource_id": str(node.get("resource-id", node.get("resource_id", "")))[:256],
                "actions": checked_actions,
            }
        )
        seen_actions.update(checked_actions)
    return structure, tuple(sorted(seen_actions))


def _digest(app: str, activity: str, structure: list[dict[str, object]]) -> str:
    payload = json.dumps(
        {"app": app, "activity": activity, "structure": structure},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def skeleton_hash(app: object, activity: object, nodes: object) -> str:
    """Hash only structural fields; copy changes do not fragment the same screen."""
    checked_app = _text(app, MAX_TEXT, "app")
    checked_activity = _text(activity, MAX_TEXT, "activity")
    structure, _ = _structure(nodes)
    return _digest(checked_app, checked_activity, structure)


@dataclass(frozen=True)
class ExploreScreen:
    screen_id: str
    app: str
    activity: str
    skeleton_sha256: str
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExploreEdge:
    source: str
    target: str
    node_key: str
    action: str


@dataclass
class ExploreGraph:
    screens: dict[str, ExploreScreen] = field(default_factory=dict[str, ExploreScreen])
    edges: list[ExploreEdge] = field(default_factory=list[ExploreEdge])
    truncated: bool = False

    def add_screen(self, app: object, activity: object, nodes: object) -> ExploreScreen:
        checked_app = _text(app, MAX_TEXT, "app")
        checked_activity = _text(activity, MAX_TEXT, "activity")
        structure, actions = _structure(nodes)
        digest = _digest(checked_app, checked_activity, structure)
        screen_id = hashlib.sha256(f"{checked_app}|{checked_activity}|{digest}".encode()).hexdigest()[:24]
        existing = self.screens.get(screen_id)
        if existing is not None:
            return existing
        if len(self.screens) >= MAX_SCREENS:
            self.truncated = True
            return ExploreScreen(screen_id, checked_app, checked_activity, digest, actions)
        screen = ExploreScreen(screen_id, checked_app, checked_activity, digest, actions)
        self.screens[screen_id] = screen
        return screen

    def add_edge(self, source: str, target: str, node_key: object, action: object) -> bool:
        if source not in self.screens or target not in self.screens or source == target:
            return False
        checked_node = _text(node_key, 100, "node_key")
        checked_action = _text(action, MAX_ACTION, "action")
        edge = ExploreEdge(source, target, checked_node, checked_action)
        if edge in self.edges:
            return False
        if len(self.edges) >= MAX_EDGES:
            self.truncated = True
            return False
        self.edges.append(edge)
        return True

    def reachable(self, source: str) -> list[str]:
        """Bounded deterministic BFS from a known screen; missing source returns []."""
        if source not in self.screens:
            return []
        seen = {source}
        queue = [source]
        result: list[str] = []
        while queue and len(result) < MAX_SCREENS:
            current = queue.pop(0)
            result.append(current)
            for edge in self.edges:
                if edge.source == current and edge.target not in seen:
                    seen.add(edge.target)
                    queue.append(edge.target)
        return result

    def paths(self, source: str, target: str, *, max_paths: int = 20, max_depth: int = 20) -> list[list[str]]:
        """Bounded simple paths from source to target; missing endpoints return []."""
        if type(max_paths) is not int or not 1 <= max_paths <= MAX_EDGES:
            raise _invalid("max_paths must be a bounded positive integer")
        if type(max_depth) is not int or not 1 <= max_depth <= MAX_SCREENS:
            raise _invalid("max_depth must be a bounded positive integer")
        if source not in self.screens or target not in self.screens:
            return []
        result: list[list[str]] = []
        stack: list[tuple[str, list[str]]] = [(source, [source])]
        while stack and len(result) < max_paths:
            current, path = stack.pop()
            if current == target:
                result.append(path)
                continue
            if len(path) >= max_depth:
                continue
            for edge in reversed(self.edges):
                if edge.source == current and edge.target not in path:
                    stack.append((edge.target, [*path, edge.target]))
        return result

    def unexplored(self, screen_id: str) -> list[str]:
        """Observed action kinds on a screen that have no edge yet, sorted; unknown screen -> []."""
        screen = self.screens.get(screen_id)
        if screen is None:
            return []
        used = {edge.action for edge in self.edges if edge.source == screen_id}
        return [action for action in screen.actions if action not in used]

    def summary(self) -> dict[str, object]:
        return {
            "screens": len(self.screens),
            "edges": len(self.edges),
            "apps": sorted({screen.app for screen in self.screens.values()}),
            "unexplored": sum(len(self.unexplored(screen_id)) for screen_id in self.screens),
            "truncated": self.truncated,
        }


def build_graph(observations: Sequence[object], transitions: Sequence[object]) -> ExploreGraph:
    """Build a bounded graph from observations and observed (source,target,node,action) transitions."""
    graph = ExploreGraph()
    for observation in observations:
        if not isinstance(observation, dict):
            raise _invalid("each observation must be an object")
        row = cast(dict[str, object], observation)
        graph.add_screen(row.get("app"), row.get("activity"), row.get("nodes", []))
    for transition in transitions:
        if not isinstance(transition, dict):
            raise _invalid("each transition must be an object")
        row = cast(dict[str, object], transition)
        graph.add_edge(
            str(row.get("source", "")),
            str(row.get("target", "")),
            row.get("node_key"),
            row.get("action"),
        )
    return graph
