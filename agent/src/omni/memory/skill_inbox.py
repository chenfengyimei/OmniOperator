"""外来技能包隔离安装（D18 后置项）：inbox 收包 → 隔离区校验 → 用户激活。

纪律（见 docs/DEEPSEEK_DEVELOPMENT_OUTLINE.md D18 的既定边界）：
- **数据化解包，绝不执行**：zip 只按类型白名单逐文件落成文本，没有任何
  postinstall/脚本执行环节——"安装"在这里只是移动文本文件；
- **隔离区不落技能根**：解包进 `.quarantine/<name>/`，激活（CONFIRM 级，
  需人工确认）前对模型与 skills_list 都不可见；
- **清单透明**：导入结果与待激活列表都带完整文件清单与 sha256——用户
  激活前看得见包里到底有什么；
- **类型白名单 = SKILL.md + references 文本**（与 SkillStore 的阅读边界
  完全一致）：可执行/脚本/二进制一律在导入时就拒绝，而不是进来再扫；
- 路径纪律与技能阅读同源：绝对路径 / `..` / 盘符 / ADS（`:`）/ Windows
  保留名 / 越界条目全部拒绝（zip-slip 对 Windows 与 POSIX 语法各查一遍）；
- 激活与技能编辑共用同一把跨进程文件锁（`skills/<name>.lock`），并把
  来源记进修订历史（provenance），激活前对隔离区内容做防篡改复核。
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

from ..system.atomic import atomic_write_bytes, atomic_write_text
from ..system.filelock import FileLock, LockTimeout
from ..system.path_guard import digest as _digest
from ..system.path_guard import safe_parts as _safe_parts
from .skills import (
    MAX_BODY,
    MAX_DESCRIPTION,
    MAX_REFERENCE_BYTES,
    MAX_REFERENCES,
    MIN_BODY,
    NAME_RE,
    REFERENCE_SUFFIXES,
    SkillStore,
    parse_skill_text,
)

MAX_ZIP_BYTES = 4 * 1024 * 1024        # 压缩包本身 ≤ 4 MiB
MAX_UNPACKED_BYTES = 16 * 1024 * 1024  # 解包总量 ≤ 16 MiB
MAX_ENTRIES = 200                      # 条目数上限（含目录记录）
MAX_SKILL_MD_BYTES = 128 * 1024        # 单个 SKILL.md ≤ 128 KiB
MAX_REFERENCE_DEPTH = 4                 # references/a/b/file.md 最深四级


def _package_name(filename: str) -> str | None:
    """用户上传/删除的收件箱文件名：必须是裸 .zip，不能带路径、盘符或 ADS。"""
    raw = filename or ""
    if raw != raw.strip():
        return None  # 首尾空格会被 Windows 静默去掉，等于悄悄改名，直接拒绝
    name = raw.strip()
    if not name or len(name) > 120 or name != Path(name).name:
        return None
    if (
        PureWindowsPath(name).drive
        or ":" in name
        or "\x00" in name
        or name.endswith((" ", "."))
        or PureWindowsPath(name).is_reserved()
        or not name.lower().endswith(".zip")
    ):
        return None
    return name


def _entry_shape(parts: tuple[str, ...]) -> str | None:
    """落位后的条目形状：'skill' = SKILL.md，'ref' = references 文本，其余拒绝。"""
    if parts == ("SKILL.md",):
        return "skill"
    if len(parts) < 2 or parts[0] != "references" or len(parts) > MAX_REFERENCE_DEPTH:
        return None
    if Path(parts[-1]).suffix.lower() not in REFERENCE_SUFFIXES:
        return None
    return "ref"


class SkillInbox:
    """外来技能包的收件箱与隔离区（都在技能根的隐藏目录下，随 skills_root 隔离）。"""

    def __init__(
        self,
        skills: SkillStore,
        *,
        inbox: Path | None = None,
        quarantine: Path | None = None,
        lock_timeout_s: float = 8.0,
    ) -> None:
        self._skills = skills
        self._inbox = Path(inbox) if inbox else skills.root / ".inbox"
        self._quarantine = Path(quarantine) if quarantine else skills.root / ".quarantine"
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._quarantine.mkdir(parents=True, exist_ok=True)
        self._lock_timeout = float(lock_timeout_s)

    # ---------------------------------------------------------------- 导入

    def import_package(self, package: str) -> tuple[bool, str]:
        """把 inbox 里的一个 zip 解包进隔离区（数据化，绝不执行任何内容）。"""
        package = (package or "").strip()
        if not package or package != Path(package).name or _package_name(package) is None:
            return False, (
                "错误：package 必须是 skills/.inbox/ 里的文件名（不带路径），"
                f"且必须是 .zip（当前 {package or '（空）'}）"
            )
        zip_path = self._inbox / package
        if zip_path.is_symlink() or not zip_path.is_file():
            return False, (
                f"错误：inbox 里没有 {package or '（空）'}。"
                "请把技能包 zip 放到 skills/.inbox/ 目录后再导入"
            )
        try:
            size = zip_path.stat().st_size
        except OSError as exc:
            return False, f"错误：读取压缩包失败（{exc}）"
        if size > MAX_ZIP_BYTES:
            return False, f"错误：压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)} MiB 上限（当前 {size} 字节）"
        try:
            entries = self._plan_entries(zip_path)
        except _Reject as exc:
            return False, f"错误：{exc}"
        # 校验通过才动隔离区：临时目录解包 → 校验内容 → 原子改名落位
        staging = self._quarantine / f".staging-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=True)
        try:
            files = self._extract(zip_path, entries, staging)
            problem = self._validate_skill(staging)
            if problem:
                return False, problem
            name, description, version = self._skill_meta(staging)
            if self._skills.exists(name):
                return False, (
                    f"错误：正式技能库已有同名技能 {name}。先查看现有内容再决定："
                    "合并进来，或先删除旧版再导入"
                )
            target = self._quarantine / name
            if target.is_symlink():
                return False, f"错误：隔离区同名路径是链接，拒绝写入 {name}"
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
            manifest = {
                "name": name,
                "description": description,
                "version": version,
                "source_zip": package,
                "zip_sha256": _digest(zip_path.read_bytes()),
                "imported_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                "files": [
                    {"path": rel, "bytes": len(data), "sha256": _digest(data)}
                    for rel, data in files
                ],
            }
            atomic_write_text(
                self._quarantine / f"{name}.manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        except _Reject as exc:
            return False, f"错误：{exc}"
        except OSError as exc:
            return False, f"错误：解包失败（{exc}），隔离区未变化"
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        total = sum(item["bytes"] for item in manifest["files"])
        lines = [
            f"已导入 {package} 到隔离区（技能名 {name}，v{version}，"
            f"{len(files)} 个文件、{total} 字节）：",
            f"- 描述：{description}",
            f"- 压缩包指纹：{manifest['zip_sha256'][:16]}",
        ]
        for item in manifest["files"]:
            lines.append(f"- {item['path']}（{item['bytes']} B，sha256 {item['sha256'][:12]}）")
        lines.append("隔离区对 skills_list 不可见。确认内容后用 skill_activate 激活，或 skill_discard 丢弃。")
        return True, "\n".join(lines)

    def upload(self, filename: str, data: bytes) -> tuple[bool, str]:
        """工作台上传技能包 zip 到收件箱（只落文件，不导入、不执行）。

        上传只是把字节放进 `skills/.inbox/`；真正的解包校验仍在
        `import_package`。这里先做文件名与体积边界，避免一个坏请求
        把几 GB 内容写进技能根。
        """
        name = _package_name(filename)
        if name is None:
            return False, "错误：文件名必须是裸 .zip（不能带路径、盘符或冒号）"
        if not data:
            return False, "错误：上传内容为空"
        if len(data) > MAX_ZIP_BYTES:
            return False, (
                f"错误：压缩包超过 {MAX_ZIP_BYTES // (1024 * 1024)} MiB 上限"
                f"（当前 {len(data)} 字节）"
            )
        if not data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            return False, "错误：不是 zip 压缩包（文件头不匹配）"
        target = self._inbox / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, data)
        except OSError as exc:
            return False, f"错误：保存上传文件失败（{exc}）"
        return True, f"已上传 {name} 到技能包收件箱（{len(data)} 字节），可点击「导入」进入隔离区"

    def remove_inbox(self, package: str) -> tuple[bool, str]:
        """从收件箱移除一个尚未导入的 zip（不碰隔离区与正式技能库）。"""
        name = _package_name(package)
        if name is None:
            return False, "错误：package 必须是收件箱里的裸 .zip 文件名"
        path = self._inbox / name
        if path.is_symlink() or not path.is_file():
            return False, f"错误：收件箱里没有 {name}"
        try:
            path.unlink()
        except OSError as exc:
            return False, f"错误：移除失败（{exc}）"
        return True, f"已从收件箱移除 {name}"

    def _plan_entries(self, zip_path: Path) -> list[tuple[str, tuple[str, ...], str]]:
        """读 zip 中央目录：先校验名字安全，剥掉公共顶层目录后逐条目做
        形状/重名/数量/体积校验。

        返回 (原名, 规范化部件, 形状)；任何越界/非白名单条目直接整包拒绝。
        """
        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = zf.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise _Reject(f"不是可读的 zip 压缩包（{exc}）") from exc
        if not infos:
            raise _Reject("压缩包是空的")
        if len(infos) > MAX_ENTRIES:
            raise _Reject(f"压缩包条目超过 {MAX_ENTRIES} 个上限（当前 {len(infos)}）")
        raw: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for info in infos:
            parts = _safe_parts(info.filename)
            if parts is None:
                raise _Reject(f"条目名不安全，整包拒绝：{info.filename!r}")
            if info.is_dir():
                continue  # 目录记录只用于校验名字，不落盘
            raw.append((info, parts))
        if not raw:
            raise _Reject("压缩包里没有文件")
        unpacked = sum(info.file_size for info, _ in raw)
        if unpacked > MAX_UNPACKED_BYTES:
            raise _Reject(f"解包总量超过 {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB 上限")
        planned: list[tuple[str, tuple[str, ...], str]] = []
        seen: set[tuple[str, ...]] = set()
        refs = 0
        for info, parts in self._strip_common_root(raw):
            shape = _entry_shape(parts)
            if shape is None:
                raise _Reject(
                    f"条目 {info.filename} 不在类型白名单内（只允许 SKILL.md 与 "
                    "references/ 内的 .md/.txt/.json/.yaml 等文本）；"
                    "脚本与二进制不允许进包"
                )
            if parts in seen:
                raise _Reject(f"条目重名（{info.filename!r}），整包拒绝")
            seen.add(parts)
            refs += shape == "ref"
            planned.append((info.filename, parts, shape))
        if refs > MAX_REFERENCES:
            raise _Reject(f"references 文件超过 {MAX_REFERENCES} 个上限（当前 {refs}）")
        return planned

    @staticmethod
    def _strip_common_root(
        raw: list[tuple[zipfile.ZipInfo, tuple[str, ...]]],
    ) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
        """所有条目共享同一个顶层目录时剥掉它（包一层目录是常见打包格式）。"""
        firsts = {parts[0] for _, parts in raw}
        if len(firsts) != 1 or not any(len(parts) > 1 for _, parts in raw):
            return raw
        stripped: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        for info, parts in raw:
            inner = parts[1:]
            if _entry_shape(inner) is None:
                raise _Reject(f"剥掉顶层目录后条目不合法：{info.filename!r}")
            stripped.append((info, inner))
        return stripped

    def _extract(
        self, zip_path: Path, entries: list[tuple[str, tuple[str, ...], str]], staging: Path
    ) -> list[tuple[str, bytes]]:
        """按白名单把每个条目落成规范化 UTF-8 文本（去 BOM、拒 NUL、限体积）。"""
        caps = {"skill": MAX_SKILL_MD_BYTES, "ref": MAX_REFERENCE_BYTES}
        files: list[tuple[str, bytes]] = []
        with zipfile.ZipFile(zip_path) as zf:
            for original, parts, shape in entries:
                cap = caps[shape]
                try:
                    with zf.open(original) as stream:
                        data = stream.read(cap + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise _Reject(f"读取条目 {original} 失败（{exc}）") from exc
                if len(data) > cap:
                    limit = f"{MAX_SKILL_MD_BYTES // 1024} KiB" if shape == "skill" else "64 KiB"
                    raise _Reject(f"条目 {original} 超过 {limit} 上限")
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeError as exc:
                    raise _Reject(f"条目 {original} 不是 UTF-8 文本") from exc
                if "\x00" in text:
                    raise _Reject(f"条目 {original} 含二进制内容（NUL 字节）")
                target = staging.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(text.encode("utf-8"))
                files.append(("/".join(parts), text.encode("utf-8")))
        return files

    @staticmethod
    def _validate_skill(staging: Path) -> str | None:
        """结构校验：SKILL.md 必须在，且过与 skill_create 相同的 linter。"""
        skill_md = staging / "SKILL.md"
        if not skill_md.is_file():
            return "错误：包里没有 SKILL.md（应位于顶层，或唯一顶层目录之内）"
        try:
            text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "错误：SKILL.md 不是可读的 UTF-8 文本"
        parsed = parse_skill_text(text)
        if parsed is None:
            return "错误：SKILL.md 不符合结构（YAML 头 + 四节固定正文）"
        meta = parsed["meta"]
        name = str(meta.get("name", "")).strip()
        if not NAME_RE.match(name):
            return (
                "错误：SKILL.md 的 name 需为 3-40 位小写字母/数字/连字符"
                "（如 bilibili-watch），且不以连字符开头/结尾"
            )
        description = str(meta.get("description", "")).strip()
        if not description or len(description) > MAX_DESCRIPTION:
            return f"错误：description 必须非空且 ≤{MAX_DESCRIPTION} 字（当前 {len(description)}）"
        body = str(parsed["body"]).strip()
        if len(body) < MIN_BODY:
            return f"错误：正文太短（<{MIN_BODY} 字），技能应是完整流程而非一句话"
        if len(body) > MAX_BODY:
            return f"错误：正文超过 {MAX_BODY} 字，请精简或拆分成多个技能"
        return None

    @staticmethod
    def _skill_meta(staging: Path) -> tuple[str, str, str]:
        try:
            text = (staging / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "", "", "1"
        parsed = parse_skill_text(text)
        meta = (parsed or {}).get("meta", {})
        return (
            str(meta.get("name", "")).strip(),
            str(meta.get("description", "")).strip(),
            str(meta.get("version", "1")),
        )

    # ---------------------------------------------------------------- 隔离区清单

    def _load_manifest(self, name: str) -> dict[str, Any] | None:
        """隔离区清单；缺失/损坏/结构不对一律返回 None（调用方决定口径）。"""
        path = self._quarantine / f"{name}.manifest.json"
        if path.is_symlink():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("files"), list):
            return None
        return data

    def pending(self) -> list[dict[str, Any]]:
        """待激活清单（隔离区），另附 inbox 里还没导入的 zip。"""
        out: list[dict[str, Any]] = []
        for pkg_dir in sorted(self._quarantine.glob("*")):
            if pkg_dir.is_symlink() or not pkg_dir.is_dir():
                continue
            skill_md = pkg_dir / "SKILL.md"
            if skill_md.is_symlink() or not skill_md.is_file():
                continue
            name = pkg_dir.name
            if not NAME_RE.match(name):
                continue
            manifest = self._load_manifest(name)
            manifest_missing = manifest is None
            if manifest is None:
                manifest = {}
            try:
                parsed = parse_skill_text(skill_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                parsed = None
            meta = (parsed or {}).get("meta", {})
            files = [
                {
                    "path": str(item.get("path", "")),
                    "bytes": int(item.get("bytes", 0)),
                    "sha256": str(item.get("sha256", "")),
                }
                for item in manifest.get("files", [])
                if isinstance(item, dict)
            ]
            out.append(
                {
                    "name": name,
                    "description": str(meta.get("description", "")) if parsed is not None else "",
                    "version": str(meta.get("version", "1")) if parsed is not None else "",
                    "source_zip": str(manifest.get("source_zip", "")),
                    "zip_sha256": str(manifest.get("zip_sha256", "")),
                    "imported_at": str(manifest.get("imported_at", "")),
                    "files": files,
                    "manifest_missing": manifest_missing,
                    "valid": parsed is not None,
                    "can_activate": bool(not manifest_missing and parsed is not None),
                }
            )
        return out

    def inbox_files(self) -> list[dict[str, Any]]:
        """inbox 里待导入的 zip（用户手动放进去或工作台上传）。"""
        out: list[dict[str, Any]] = []
        try:
            for path in sorted(self._inbox.iterdir()):
                if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".zip":
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                out.append({
                    "name": path.name,
                    "bytes": stat.st_size,
                    "modified_at": dt.datetime.fromtimestamp(stat.st_mtime)
                    .astimezone().isoformat(timespec="seconds"),
                })
        except OSError:
            pass
        return out

    # ---------------------------------------------------------------- 激活 / 丢弃

    def activate(self, name: str) -> tuple[bool, str]:
        """把隔离区里的包激活进正式技能库（调用方保证这是人工确认后的动作）。"""
        name = (name or "").strip()
        if not NAME_RE.match(name):
            return False, "错误：技能名需为 3-40 位小写字母/数字/连字符（先 skill_pending 查看）"
        pkg_dir = self._quarantine / name
        skill_md = pkg_dir / "SKILL.md"
        if pkg_dir.is_symlink() or skill_md.is_symlink() or not skill_md.is_file():
            return False, f"错误：隔离区没有待激活的技能 {name}（先 skill_pending 查看）"
        manifest = self._load_manifest(name)
        if manifest is None:
            return False, (
                f"错误：技能 {name} 的隔离区清单缺失或损坏，无法核对内容是否被改动；"
                "请重新导入后再激活"
            )
        # 防篡改复核：导入与激活之间文件可能被改过，激活前重新过同一套校验
        problem = self._validate_skill(pkg_dir)
        if problem:
            return False, f"{problem}（隔离区内容与导入时不一致，可能被改动过；请重新导入）"
        problem = self._verify_against_manifest(pkg_dir, manifest)
        if problem:
            return False, f"错误：{problem}"
        if self._skills.exists(name):
            return False, f"错误：正式技能库已有同名技能 {name}，拒绝覆盖"
        source_zip = str(manifest.get("source_zip", ""))
        lock = FileLock(
            self._skills.root / ".locks" / f"{name}.lock",
            timeout_s=self._lock_timeout,
            owner=f"skill-inbox:{name}",
        )
        try:
            lock.acquire()
        except LockTimeout as exc:
            return False, f"错误：技能 {name} 正被其它进程编辑（{exc}），稍后再激活"
        try:
            if self._skills.exists(name):  # 锁内复核：等待锁的间隙可能有人刚建了同名技能
                return False, f"错误：正式技能库已有同名技能 {name}，拒绝覆盖"
            pkg_dir.rename(self._skills.root / name)
        except OSError as exc:
            return False, f"错误：激活失败（{exc}），隔离区保持原样"
        finally:
            lock.release()
        # 来源登记失败不撤销激活：技能已就位，历史由下一次写入的快照兜底
        summary = f"外来技能包激活（来源 zip：{source_zip}）" if source_zip else "外来技能包激活"
        ok, note = self._skills.adopt(name, author="user", source="skill_import", summary=summary)
        (self._quarantine / f"{name}.manifest.json").unlink(missing_ok=True)
        if ok:
            return True, (
                f"已激活技能 {name}（skills/{name}/，进入正式技能库）。{note}"
                "原 zip 仍保留在 inbox，可自行删除。"
            )
        return True, f"已激活技能 {name}，但{note}"

    def _verify_against_manifest(
        self, pkg_dir: Path, manifest: dict[str, Any]
    ) -> str | None:
        """隔离区当前内容与导入清单逐文件比对（路径集合 + sha256 全等）。"""
        recorded: dict[str, str] = {}
        for item in manifest.get("files", []):
            if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
                return "隔离区清单条目不完整，无法核对；请重新导入后再激活"
            recorded[str(item["path"])] = str(item["sha256"])
        actual: dict[str, str] = {}
        try:
            for path in sorted(pkg_dir.rglob("*")):
                if path.is_symlink():
                    return "隔离区包含符号链接，拒绝激活；请重新导入"
                if not path.is_file():
                    continue
                rel = path.relative_to(pkg_dir).as_posix()
                data = path.read_bytes()
                if len(data) > MAX_SKILL_MD_BYTES:
                    return f"文件 {rel} 超过大小上限，疑似被替换；请重新导入"
                actual[rel] = _digest(data)
        except OSError as exc:
            return f"读取隔离区内容失败（{exc}）；请重新导入"
        if actual == recorded:
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
        return f"隔离区内容与导入清单不一致（{detail}）；内容可能被改动过，请重新导入后再激活"

    def discard(self, name: str) -> tuple[bool, str]:
        """丢弃隔离区里的一个待激活包（只删隔离区，不碰正式技能库）。"""
        name = (name or "").strip()
        if not NAME_RE.match(name):
            return False, "错误：技能名需为 3-40 位小写字母/数字/连字符（先 skill_pending 查看）"
        pkg_dir = self._quarantine / name
        if pkg_dir.is_symlink() or not pkg_dir.is_dir():
            return False, f"错误：隔离区没有 {name}（先 skill_pending 查看）"
        shutil.rmtree(pkg_dir, ignore_errors=True)
        (self._quarantine / f"{name}.manifest.json").unlink(missing_ok=True)
        return True, f"已丢弃隔离区里的 {name}（正式技能库不受影响）"


class _Reject(RuntimeError):
    """导入校验失败：整包拒绝，隔离区不落任何文件。"""
