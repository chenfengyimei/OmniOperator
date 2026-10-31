"""state.db 的异常语义与错误翻译（唯一来源）。

之前这些异常类定义在 `state.py`，各域 mixin 也需要抛它们——放独立模块
让 `state.py` 与域 mixin 都单向依赖这里，斩断循环 import。

纪律：
- 损坏/只读/版本未知/数据库忙分别有明确状态，**绝不当成空数据继续**；
- "环境问题"翻译成可解释状态，其余 SQL 错误保持原样（那是代码 bug，不该被吞）。
"""
from __future__ import annotations

import sqlite3


class StateError(RuntimeError):
    """状态库无法给出可信结果。"""


class StateUnavailable(StateError):
    """相关能力必须停用（版本未知 / 旧数据损坏 / 目录只读），不得当成空数据继续。"""


class StateConflict(StateError):
    """revision 冲突：调用方基于旧快照提交。"""


class StateBusy(StateError):
    """数据库忙（另一个进程持有写事务）。忙不是成功，调用方必须如实上报。"""


def translate_operational(exc: sqlite3.OperationalError) -> StateError:
    """把"环境问题"翻译成可解释状态；其它 SQL 错误保持原样。

    只读文件/目录、锁冲突、磁盘错误都不是"没有数据"——调用方必须能分辨并停用能力。
    """
    text = str(exc).lower()
    if "readonly" in text or "read-only" in text:
        return StateUnavailable(f"状态库不可写（文件或目录只读）：{exc}")
    if "locked" in text or "busy" in text:
        return StateBusy(f"状态库忙（另一个进程正在写）：{exc}")
    if "disk i/o" in text or "full" in text:
        return StateUnavailable(f"状态库写入失败（磁盘）：{exc}")
    return StateError(f"状态库操作失败：{exc}")
