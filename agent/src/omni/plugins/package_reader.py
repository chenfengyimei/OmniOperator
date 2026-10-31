"""插件包读取与类型校验：纯数据检查，**零执行**。

职责边界：把 zip / 工作区目录读成 `{相对路径: 字节}` 并按白名单校验形状
（manifest.json + 唯一顶层入口 .py + README.md + references 文本），以及
包内容指纹与逐文件比对。**本模块不做装载、不写隔离区、不接触注册表**——
装载与文件状态分别在 `plugins/host.py` 与 `plugins/quarantine.py`。

信任模型（如实声明）：这里的校验是数据检查，不是安全证明；激活（人工
确认）即同意执行该插件的代码。
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from ..system.path_guard import digest, safe_parts  # 共享原语：与 skill_inbox 同一实现
from .manifest import (
    MANIFEST_NAME,
    PluginManifest,
    MAX_MANIFEST_BYTES,
)

MAX_ZIP_BYTES = 4 * 1024 * 1024        # 压缩包本身 ≤ 4 MiB
MAX_UNPACKED_BYTES = 8 * 1024 * 1024    # 解包总量 ≤ 8 MiB
MAX_ENTRIES = 50                        # 条目数上限（含目录记录）
MAX_CODE_BYTES = 128 * 1024             # entry 单文件 ≤ 128 KiB
MAX_REFERENCE_BYTES = 64 * 1024         # 单个参考文件 ≤ 64 KiB
MAX_REFERENCES = 20
MAX_REFERENCE_DEPTH = 4
REFERENCE_SUFFIXES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".csv"}


class Reject(RuntimeError):
    """导入校验失败：整包拒绝，隔离区不落任何文件。"""


def entry_shape(parts: tuple[str, ...]) -> str | None:
    """落位后的条目形状；v1 白名单：manifest / 唯一顶层 py / README / references 文本。"""
    if parts == ("manifest.json",):
        return "manifest"
    if len(parts) == 1 and parts[0].endswith(".py"):
        return "code"
    if parts == ("README.md",):
        return "readme"
    if len(parts) < 2 or parts[0] != "references" or len(parts) > MAX_REFERENCE_DEPTH:
        return None
    if Path(parts[-1]).suffix.lower() not in REFERENCE_SUFFIXES:
        return None
    return "ref"


def is_artifact(parts: tuple[str, ...]) -> bool:
    """Python 生成的字节码缓存：不属于包内容——读包、指纹与防篡改一律跳过。

    背景：导入插件时解释器会在包目录写 __pycache__/*.pyc；工作区开发目录里
    也常有本地调试留下的缓存。它们不是作者内容，也不被加载（入口按源码加载）。
    """
    return bool(parts) and parts[0] == "__pycache__"


def read_zip(zip_path: Path) -> dict[str, bytes]:
    """读 zip → {规范化相对路径: 字节}；任何越界/白名单外条目整包拒绝。"""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise Reject(f"不是可读的 zip 压缩包（{exc}）") from exc
    with zf:
        infos = zf.infolist()
        if not infos:
            raise Reject("压缩包是空的")
        if len(infos) > MAX_ENTRIES:
            raise Reject(f"压缩包条目超过 {MAX_ENTRIES} 个上限（当前 {len(infos)}）")
        raw: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for info in infos:
            parts = safe_parts(info.filename)
            if parts is None:
                raise Reject(f"条目名不安全，整包拒绝：{info.filename!r}")
            if info.is_dir() or is_artifact(parts):
                continue  # 目录记录与 __pycache__ 缓存不落盘
            raw.append((info, parts))
        if not raw:
            raise Reject("压缩包里没有文件")
        # 剥掉公共顶层目录（"打包整个文件夹"的常见 zip 形态），剥前已过名字安全检查
        firsts = {parts[0] for _, parts in raw}
        if len(firsts) == 1 and any(len(parts) > 1 for _, parts in raw):
            raw = [(info, parts[1:]) for info, parts in raw]
        files: dict[str, bytes] = {}
        unpacked = 0
        seen: set[tuple[str, ...]] = set()
        for info, parts in raw:
            shape = entry_shape(parts)
            if shape is None:
                raise Reject(
                    f"条目 {info.filename} 不在类型白名单内（只允许 manifest.json、"
                    "唯一顶层入口 .py、README.md 与 references/ 文本）"
                )
            if parts in seen:
                raise Reject(f"条目重名（{info.filename!r}），整包拒绝")
            seen.add(parts)
            unpacked += info.file_size
            cap = MAX_MANIFEST_BYTES if shape == "manifest" else (
                MAX_CODE_BYTES if shape == "code" else MAX_REFERENCE_BYTES
            )
            try:
                with zf.open(info) as stream:
                    data = stream.read(cap + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise Reject(f"读取条目 {info.filename} 失败（{exc}）") from exc
            if len(data) > cap:
                raise Reject(f"条目 {info.filename} 超过大小上限")
            files["/".join(parts)] = data
        if unpacked > MAX_UNPACKED_BYTES:
            raise Reject(f"解包总量超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB 上限")
    return files


def read_directory(source_dir: Path) -> dict[str, bytes]:
    """读工作区目录 → {相对路径: 字节}；与 zip 同一套形状/上限校验。"""
    files: dict[str, bytes] = {}
    total = 0
    count = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = tuple(path.relative_to(source_dir).as_posix().split("/"))
        if is_artifact(rel_parts):
            continue  # 开发时留下的字节码缓存不属于包内容
        count += 1
        if count > MAX_ENTRIES:
            raise Reject(f"目录条目超过 {MAX_ENTRIES} 个上限")
        shape = entry_shape(rel_parts)
        if shape is None:
            raise Reject(
                f"文件 {path.name} 不在类型白名单内（只允许 manifest.json、"
                "唯一顶层入口 .py、README.md 与 references/ 文本）"
            )
        cap = MAX_MANIFEST_BYTES if shape == "manifest" else (
            MAX_CODE_BYTES if shape == "code" else MAX_REFERENCE_BYTES
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise Reject(f"读取 {path.name} 失败（{exc}）") from exc
        if len(data) > cap:
            raise Reject(f"文件 {path.name} 超过大小上限")
        total += len(data)
        files["/".join(rel_parts)] = data
    if not files:
        raise Reject("目录是空的")
    if total > MAX_UNPACKED_BYTES:
        raise Reject(f"内容总量超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB 上限")
    return files


def validate_files(files: dict[str, bytes]) -> PluginManifest:
    """清单解析 + 形状终检（入口必须恰好是唯一顶层 .py）。"""
    raw = files.get(MANIFEST_NAME)
    if raw is None:
        raise Reject("包里没有 manifest.json（应在顶层或唯一顶层目录之内）")
    try:
        manifest = PluginManifest.from_dict(json.loads(raw.decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Reject(f"manifest.json 不是合法 UTF-8 JSON（{exc}）") from exc
    if isinstance(manifest, str):
        raise Reject(manifest)
    py_files = [name for name in files if name.endswith(".py") and "/" not in name]
    if py_files != [manifest.entry]:
        raise Reject(
            f"入口必须恰好是唯一的顶层 .py：manifest 声明 {manifest.entry}，"
            f"实际有 {py_files or '（无）'}"
        )
    refs = [name for name in files if name.startswith("references/")]
    if len(refs) > MAX_REFERENCES:
        raise Reject(f"references 文件超过 {MAX_REFERENCES} 个上限（当前 {len(refs)}）")
    return manifest


def content_sha256(pkg_dir: Path) -> str:
    """包内容指纹：全部**非生成物**文件按路径序做 sha256（升级/篡改都以它为准）。"""
    digest = hashlib.sha256()
    try:
        for path in sorted(pkg_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(pkg_dir).as_posix()
            if rel.split("/")[0] == "__pycache__":
                continue  # 解释器生成的字节码不属于内容
            digest.update(rel.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
    except OSError:
        pass
    return digest.hexdigest()


def compare_manifests(recorded: dict[str, str], actual: dict[str, str]) -> str | None:
    if recorded == actual:
        return None
    changed = sorted(k for k in actual if k in recorded and actual[k] != recorded[k])
    extra = sorted(k for k in actual if k not in recorded)
    missing = sorted(k for k in recorded if k not in actual)
    detail = "；".join(
        part
        for part in (
            f"被改动：{'、'.join(changed)}" if changed else "",
            f"新增：{'、'.join(extra)}" if extra else "",
            f"缺失：{'、'.join(missing)}" if missing else "",
        )
        if part
    )
    return f"隔离区内容与导入记录不一致（{detail}）；可能被改动过，请重新导入"
