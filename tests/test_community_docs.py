"""社区门面守卫（开源体系计划 Phase 3）。

- README.md「模块地图」必须与 system.catalog.schema.MODULES 的分类与模块完全一致（防漂移）；
- README.md / README.en.md 的相对链接目标必须真实存在（防死链）；
- LICENSE 必须是 Apache-2.0 标准文本。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from omni.system.catalog.schema import MODULES

ROOT = Path(__file__).resolve().parents[1]


def test_license_is_apache2_boilerplate() -> None:
    text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
    assert 'Apache License' in text
    assert 'Version 2.0, January 2004' in text
    assert 'APPENDIX: How to apply the Apache License to your work' in text
    assert 'Copyright 2026 OmniOperator contributors' in text


def _module_map_section(readme: str) -> str:
    match = re.search(r'^## 模块地图\s*$(.*?)(?=^## |\Z)', readme, re.M | re.S)
    assert match, 'README.md 缺少「## 模块地图」章节'
    return match.group(1)


def _module_ids(block: str) -> set[str]:
    return {found.group(1) for found in re.finditer(r'^\|\s*`([a-z][a-z0-9-]*)`', block, re.M)}


def test_readme_module_map_matches_catalog() -> None:
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    section = _module_map_section(readme)
    got: dict[str, set[str]] = {}
    for block in re.split(r'^### ', section, flags=re.M)[1:]:
        category, _, rest = block.partition('\n')
        got[category.strip()] = _module_ids(rest)
    expected: dict[str, set[str]] = {}
    for module in MODULES:
        expected.setdefault(module.category, set()).add(module.id)
    assert set(got) == set(expected), (
        f'模块地图分类与 schema 不一致：README 多了 {sorted(set(got) - set(expected))}，'
        f'缺了 {sorted(set(expected) - set(got))}')
    for category in sorted(expected):
        assert got.get(category, set()) == expected[category], (
            f'分类「{category}」模块不一致：缺 {sorted(expected[category] - got.get(category, set()))}，'
            f'多 {sorted(got.get(category, set()) - expected[category])}——'
            f'请在 README.md 模块地图该分类下补/改一行：| `模块id` | **标题** — 职责 | [文档](docs/xxx.md) |')


@pytest.mark.parametrize('name', ['README.md', 'README.en.md'])
def test_readme_relative_links_resolve(name: str) -> None:
    text = (ROOT / name).read_text(encoding='utf-8')
    targets = {found.group(1).split('#')[0] for found in re.finditer(r'\]\(([^)\s]+)\)', text)}
    missing = [item for item in sorted(targets)
               if item and not item.startswith(('http://', 'https://', 'mailto:'))
               and not (ROOT / item).is_file()]
    assert not missing, f'{name} 存在死链：{missing}'


@pytest.mark.parametrize('name', [
    'CONTRIBUTING.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', 'CHANGELOG.md', 'docs/README.md',
])
def test_community_files_exist_and_linked_from_readme(name: str) -> None:
    path = ROOT / name
    assert path.is_file() and path.stat().st_size > 500, f'{name} 缺失或内容过短'
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert f'({name})' in readme, f'README.md 未链接 {name}'
