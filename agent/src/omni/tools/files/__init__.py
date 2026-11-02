"""结构化文件工具域（D13）：工作区内的有界读写、差异预览与回滚。

边界在 `paths.Workspace`（根校验 / 敏感项 / 链接与 ADS），
读写实现在 `io`（编码、二进制、窗口、指纹），搜索在 `search`（有界遍历），
备份在 `history`，工具契约在 `tools`。
"""

from .history import FileHistory, Snapshot, SnapshotTooLarge
from .paths import PathRejected, Resolved, Workspace
from .tools import register_file_tools

__all__ = [
    "FileHistory",
    "PathRejected",
    "Resolved",
    "Snapshot",
    "SnapshotTooLarge",
    "Workspace",
    "register_file_tools",
]
