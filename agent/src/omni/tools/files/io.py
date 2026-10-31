"""文本读写的有界实现（D13）：编码识别、二进制拒绝、窗口读取、原子替换。

几条刻意的取舍：
- **不猜二进制**：前 8KB 出现 NUL 就按二进制拒绝，不尝试"尽力解码"——
  把 xlsx/db 解成乱码再喂给模型，只会浪费上下文并泄露片段；
  例外是**带 BOM 的 UTF-16**（记事本「Unicode」/PowerShell 5.1 `Out-File` 的默认
  存档）：NUL 高频是编码本身，不是二进制特征。无 BOM 的 UTF-16 与真二进制
  区分不开，维持拒绝（fail-closed）；
- **保留原有编码与换行**：GBK 文件读出来写回去还是 GBK，CRLF 不会因为一次编辑变成 LF，
  UTF-16 连字节序（LE/BE）都原样写回，否则每次改一行都会在 git 里显示整文件重写；
- **检查后不变**：打开文件后用 `fstat(fd)` 与 `stat(path)` 比对文件身份，
  防止"校验时是普通文件、写入时已换成链接"的竞态。
"""
from __future__ import annotations

import codecs
import difflib
import io
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...system.path_guard import digest as sha256_hex
from .paths import PathRejected

BINARY_SNIFF_BYTES = 8192
MAX_LINE_CHARS = 2000
MIN_HASH_PREFIX = 8

# UTF-16 只认显式 BOM；编码名带上字节序，写回时不会静默换端序
UTF16_ENCODINGS = ("utf-16-le", "utf-16-be")


def hash_matches(expected: str, actual: str) -> bool:
    """内容指纹比对：允许短前缀（≥8 位），大小写不敏感。

    不让模型每次都回填 64 位十六进制：它抄错一位就会被判成冲突，
    而冲突的代价是让用户重来一遍。
    """
    want = str(expected or "").strip().lower()
    if len(want) < MIN_HASH_PREFIX:
        return False
    return actual.lower().startswith(want)


# ---------------------------------------------------------------- 解码


@dataclass(frozen=True)
class Decoded:
    text: str
    encoding: str
    bom: bool
    newline: str


def newline_of_bytes(data: bytes, encoding: str = "") -> str:
    """按原始字节判断换行风格：CRLF 占多数就按 CRLF 写回（UTF-16 按其字节形态计数）。"""
    if encoding == "utf-16-le":
        crlf = data.count(b"\r\x00\n\x00")
        lf = data.count(b"\n\x00") - crlf
    elif encoding == "utf-16-be":
        crlf = data.count(b"\x00\r\x00\n")
        lf = data.count(b"\x00\n") - crlf
    else:
        crlf = data.count(b"\r\n")
        lf = data.count(b"\n") - crlf
    return "\r\n" if crlf and crlf >= lf else "\n"


def is_binary(data: bytes) -> bool:
    head = data[:BINARY_SNIFF_BYTES]
    # UTF-16 带 BOM 是明确的文本：NUL 高频是编码本身；无 BOM 的 UTF-16不猜
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return False
    return b"\x00" in head


def detect_encoding(sample: bytes) -> tuple[str, bool]:
    """返回 (编码, 是否有 BOM)；识别不了就按 UTF-8 替换解码（不抛异常）。

    UTF-16 只认显式 BOM，且 LE/BE 分别成编码名——写回保持原字节序；
    无 BOM 的 UTF-16 与二进制区分不开，按二进制拒绝。
    """
    if sample.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le", True
    if sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be", True
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8", True
    try:
        sample.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError:
        pass
    # GB18030 是 GBK 的超集：中文 Windows 上的文本基本都落在这里
    try:
        sample.decode("gb18030")
        return "gb18030", False
    except UnicodeDecodeError:
        return "utf-8", False


def decode_bytes(data: bytes) -> Decoded:
    encoding, bom = detect_encoding(data[:BINARY_SNIFF_BYTES])
    newline = newline_of_bytes(data, encoding)
    if encoding in UTF16_ENCODINGS:
        # BOM 由本层显式剥离/写回：utf-16-le/be 编码名不会自动吞 BOM
        text = (data[2:] if bom else data).decode(encoding, errors="replace")
        return Decoded(
            text=text.replace("\r\n", "\n").replace("\r", "\n"),
            encoding=encoding,
            bom=bom,
            newline=newline,
        )
    codec = "utf-8-sig" if bom else encoding
    try:
        text = data.decode(codec)
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return Decoded(
        text=text.replace("\r\n", "\n").replace("\r", "\n"),
        encoding=encoding,
        bom=bom,
        newline=newline,
    )


def encode_text(text: str, *, encoding: str, bom: bool, newline: str) -> bytes:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    try:
        payload = normalized.encode(encoding)
    except UnicodeEncodeError:
        # 原文件是 GBK，新内容里有 GBK 装不下的字：改存 UTF-8 而不是丢字
        payload = normalized.encode("utf-8")
        encoding, bom = "utf-8", False
    if encoding == "utf-16-le":
        return (codecs.BOM_UTF16_LE + payload) if bom else payload
    if encoding == "utf-16-be":
        return (codecs.BOM_UTF16_BE + payload) if bom else payload
    return (codecs.BOM_UTF8 + payload) if bom else payload


# ---------------------------------------------------------------- 读


@dataclass(frozen=True)
class Window:
    """一次有界读取的结果。`total` 为 None 表示没读到文件末尾（总行数未知）。"""

    lines: list[str]
    offset: int
    total: int | None
    more: bool
    clipped_lines: int
    encoding: str
    bom: bool
    size: int
    fingerprint: str = ""


def identity_matches(fd: int, path: Path) -> bool:
    """打开的文件描述符与"现在这个路径"是否同一个文件。

    Windows 上 `st_ino` 是文件索引，换掉/链接替换都会变；POSIX 上是 inode。
    做不到的事：不阻止同一文件被原地截断改写——那需要文件系统级快照。
    """
    try:
        opened = os.fstat(fd)
        current = os.stat(path)
    except OSError:
        return False
    return (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)


@contextmanager
def opened_checked(path: Path) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        if not identity_matches(fd, path):
            raise PathRejected(
                "打开后路径变成了另一个文件（可能被链接替换），本次不读取",
                code="path_changed",
            )
        yield fd
    finally:
        os.close(fd)


def read_window(
    path: Path, *, offset: int = 1, limit: int = 200, hash_limit: int = 4 * 1024 * 1024
) -> Window:
    """按行读取 `[offset, offset+limit)`；返回行内容、已知总行数与内容指纹。

    小文件（≤ hash_limit）一次读全：拿到**原始字节**的 sha256 当写入门票——
    指纹必须与写入校验用同一份字节算，不能"读时按解码后的文本算、写时按原始字节算"。
    """
    start = max(int(offset), 1)
    count = max(int(limit), 1)
    with opened_checked(path) as fd:
        size = os.fstat(fd).st_size
        with open(fd, "rb", closefd=False) as raw:
            if size <= hash_limit:
                data = raw.read()
                if is_binary(data[:BINARY_SNIFF_BYTES]):
                    raise PathRejected(
                        f"这是二进制文件（{size} 字节），不按文本读取", code="binary_file"
                    )
                decoded = decode_bytes(data)
                every = decoded.text.split("\n")
                if every and every[-1] == "":
                    every.pop()
                window = every[start - 1 : start - 1 + count]
                lines: list[str] = []
                clipped = 0
                for line in window:
                    if len(line) > MAX_LINE_CHARS:
                        clipped += 1
                        line = line[:MAX_LINE_CHARS] + " …（本行过长，已截断）"
                    lines.append(line)
                return Window(
                    lines=lines,
                    offset=start,
                    total=len(every),
                    more=(start - 1 + len(lines)) < len(every),
                    clipped_lines=clipped,
                    encoding=decoded.encoding,
                    bom=decoded.bom,
                    size=size,
                    fingerprint=sha256_hex(data),
                )
            sample = raw.read(BINARY_SNIFF_BYTES)
            if is_binary(sample):
                raise PathRejected(
                    f"这是二进制文件（{size} 字节），不按文本读取", code="binary_file"
                )
            encoding, bom = detect_encoding(sample)
            raw.seek(0)
            if bom and encoding in UTF16_ENCODINGS:
                raw.read(2)  # UTF-16 的 BOM 由本层剥离，流式解码不再看到 U+FEFF
            text = io.TextIOWrapper(
                raw,
                encoding=("utf-8-sig" if bom and encoding == "utf-8" else encoding),
                errors="replace",
                newline="",
            )
            lines = []
            clipped = 0
            seen = 0
            more = False
            total: int | None = None
            try:
                for line in text:
                    seen += 1
                    if seen < start:
                        continue
                    if len(lines) >= count:
                        more = True
                        total = None
                        break
                    stripped = line.rstrip("\r\n")
                    if len(stripped) > MAX_LINE_CHARS:
                        clipped += 1
                        stripped = stripped[:MAX_LINE_CHARS] + " …（本行过长，已截断）"
                    lines.append(stripped)
                else:
                    total = seen
            finally:
                text.detach()  # 不让 TextIOWrapper 关掉我们的 fd
    return Window(
        lines=lines,
        offset=start,
        total=total,
        more=more,
        clipped_lines=clipped,
        encoding=encoding,
        bom=bom,
        size=size,
        fingerprint="",  # 超大文件不整读：不给出"看起来能用"的指纹
    )


def read_bytes(path: Path, *, limit: int) -> bytes:
    with opened_checked(path) as fd:
        size = os.fstat(fd).st_size
        if size > limit:
            raise PathRejected(
                f"文件 {size} 字节，超过单次读取上限 {limit} 字节；"
                "请用 files_read 按行读取或指定更小的范围",
                code="file_too_large",
            )
        with open(fd, "rb", closefd=False) as raw:
            return raw.read()


# ---------------------------------------------------------------- 差异


def unified_diff(old: str, new: str, *, name: str, context: int = 3, max_lines: int = 200) -> tuple[str, int]:
    """返回 (差异文本, 总差异行数)；超出 max_lines 时截断并标注。"""
    diff = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"{name}（当前）",
            tofile=f"{name}（写入后）",
            n=context,
            lineterm="",
        )
    )
    total = len(diff)
    if total > max_lines:
        shown = diff[:max_lines]
        shown.append(f"…（差异共 {total} 行，只显示前 {max_lines} 行）")
        return "\n".join(shown), total
    return "\n".join(diff), total
