"""服务启动源码指纹，不依赖 Git 可用性，不包含配置/凭据/运行数据。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import PROJECT_ROOT


def source_fingerprint(root: Path = PROJECT_ROOT) -> str:
    root = Path(root)
    def inventory():
        code = sorted(p for p in (root / 'src/omni').rglob('*') if p.is_file()
                      and p.suffix in {'.py', '.ps1', '.html', '.js', '.css'} and '__pycache__' not in p.parts)
        return code + [root / name for name in ('pyproject.toml', 'uv.lock') if (root / name).is_file()]
    files = inventory()
    if not files or len(files) > 2000:
        raise ValueError('源码清单缺失或过大')
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in files}
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    if inventory() != files or any(before[p] != (p.stat().st_size, p.stat().st_mtime_ns) for p in files):
        raise ValueError('源码正在变化，无法记录稳定启动版本')
    return digest.hexdigest()
