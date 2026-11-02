"""服务运行态快照：子进程写，工作台读。

"进程还活着"不等于"它在正常工作"——心跳进程可能卡在初始化、QQ 通道可能连不上 NapCat。
只有服务进程自己知道这些，所以让它定期把状态落到 `data/run/<name>.state.json`，
工作台只负责读取和判新鲜度，不去猜内部状态。

职责：写/读这一份快照，并给出"新鲜 / 陈旧 / 不可读"的判定。
明确不做：不判断服务该不该在跑（那是 `system/supervisor.py` 的职责）、
不落 `state.db`（秒级刷新的数字进库只会带来写放大，还要同步数据模型文档）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .atomic import atomic_write_text

STATE_VERSION = 1

# 读端原因码：UI 靠它区分"没启动过""刚崩""还活着但卡住了"
REASON_MISSING = "missing"
REASON_CORRUPT = "corrupt"
REASON_LAUNCH_MISMATCH = "launch_mismatch"
REASON_STALE = "stale"
REASON_OK = "ok"


def state_path(run_dir: Path, service: str) -> Path:
    return Path(run_dir) / f"{service}.state.json"


class StateWriter:
    """一个服务进程一份写端；每次 `write()` 覆盖整份快照。

    写用原子替换：读端永远看不到半个文件（工作台每 5 秒读一次，撞上写入的概率不低）。
    """

    def __init__(self, path: Path, *, service: str, launch_id: str, pid: int | None = None, clock=time.time) -> None:
        self._path = Path(path)
        self._service = service
        self._launch_id = launch_id
        self._pid = pid
        self._clock = clock
        self._seq = 0
        self._started = clock()

    def write(self, *, health: str = "ok", stats: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
        self._seq += 1
        payload = {
            "version": STATE_VERSION,
            "service": self._service,
            "launch_id": self._launch_id,
            "pid": self._pid,
            "updated": self._clock(),
            "started": self._started,
            "seq": self._seq,
            "health": health,
            "note": note,
            "stats": dict(stats or {}),
        }
        atomic_write_text(self._path, json.dumps(payload, ensure_ascii=False))
        return payload


def read_state(
    path: Path, *, now: float | None = None, ttl_s: float = 45.0, launch_id: str | None = None
) -> dict[str, Any]:
    """读取快照；**永不抛异常**（坏文件当作"读不出来"，不是崩溃）。"""
    now = time.time() if now is None else float(now)
    blank: dict[str, Any] = {
        "present": False,
        "fresh": False,
        "reason": REASON_MISSING,
        "service": None,
        "launch_id": None,
        "pid": None,
        "updated": None,
        "age_s": None,
        "health": "",
        "note": "",
        "stats": {},
        "seq": 0,
    }
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return blank
    except OSError:
        return {**blank, "present": True, "reason": REASON_CORRUPT}
    try:
        if len(raw) > 65536:
            return {**blank, "present": True, "reason": REASON_CORRUPT}
        data = json.loads(raw)
    except ValueError:
        return {**blank, "present": True, "reason": REASON_CORRUPT}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {**blank, "present": True, "reason": REASON_CORRUPT}
    updated = data.get("updated")
    if type(updated) not in (int, float) or updated <= 0:
        return {**blank, "present": True, "reason": REASON_CORRUPT}
    age = max(0.0, now - float(updated))
    result = {
        "present": True,
        "fresh": True,
        "reason": REASON_OK,
        "service": data.get("service"),
        "launch_id": data.get("launch_id"),
        "pid": data.get("pid"),
        "updated": float(updated),
        "age_s": round(age, 1),
        "health": str(data.get("health") or ""),
        "note": str(data.get("note") or ""),
        "stats": data.get("stats") if isinstance(data.get("stats"), dict) else {},
        "seq": int(data.get("seq") or 0),
    }
    if launch_id is not None and result["launch_id"] != launch_id:
        # 换了一个实例（重启发过），旧快照不能当成本进程的现状
        return {**result, "fresh": False, "reason": REASON_LAUNCH_MISMATCH}
    if age > float(ttl_s):
        return {**result, "fresh": False, "reason": REASON_STALE}
    return result
