"""只读运维入口共用的路径选择，不组装服务、不建库、不创建目录。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ...config import CONFIG_PATH, DATA_DIR, PROJECT_ROOT, RuntimePaths, load_config
from .catalog import read_catalog, validate_catalog


def load_runtime_paths(
    *,
    data_dir: Path | str | None = None,
    config_path: Path | str | None = None,
    skills_root: Path | str | None = None,
    memory_path: Path | str | None = None,
    agent_id: str | None = None,
    directory_catalog: Path | str | None = None,
) -> RuntimePaths:
    """与启动入口相同的环境/显式参数优先级，解析一次冻结快照。

    注入 data_dir 时不继承机器上的 Agent/config/catalog 环境选择。
    memory_path/skills_root 是运维调用者的显式覆盖，与 build_runtime 一致。
    """
    isolated = data_dir is not None
    config_file = Path(
        config_path or (os.environ.get("OMNI_CONFIG_PATH") if not isolated else "") or CONFIG_PATH
    ).resolve()
    anchor = Path(data_dir or os.environ.get("OMNI_STORAGE_BASE") or DATA_DIR).resolve()
    legacy = RuntimePaths.create(anchor, config_path=config_file, skills_root=skills_root, memory_path=memory_path)
    legacy = replace(legacy, updates=(anchor if isolated else PROJECT_ROOT / "data") / "control" / "updates")
    config = load_config(config_file)
    workspace = Path(config.files.root) if config.files.root else PROJECT_ROOT
    if not workspace.is_absolute():
        workspace = PROJECT_ROOT / workspace
    legacy = replace(legacy, workspace=workspace.resolve())
    catalog_file = Path(
        directory_catalog
        or (
            anchor / "control" / "agents.json"
            if isolated
            else os.environ.get("OMNI_AGENT_CATALOG") or config_file.with_name("agents.json")
        )
    ).resolve()
    data, _revision = read_catalog(catalog_file)
    _application, layouts = validate_catalog(data, legacy, catalog=catalog_file, isolated=isolated)
    identifier = agent_id or (os.environ.get("OMNI_AGENT_ID") if not isolated else None) or data.active
    selected = next((layout for layout in layouts if layout.profile.id == identifier), None)
    if selected is None:
        raise ValueError("指定的 Agent 不存在")
    paths = selected.paths
    if memory_path is not None:
        paths = replace(paths, memory=Path(memory_path))
    if skills_root is not None:
        paths = replace(paths, skills=Path(skills_root))
    return paths
