"""全历史敏感内容审计：枚举 git 全部 blob 与提交信息，扫描敏感串形态。

用法（字面量与输出都放**仓库外**私有目录——报告本身含敏感串，禁止入库）：

    uv run --no-sync python scripts/audit_sensitive.py --literals <私有字面量文件> --output <私有报告目录>

    --literals  每行一个"已知敏感字面量"（真实 QQ 号、密钥等）；**不写进本脚本**，
                防止敏感串随脚本再次入库。`#` 开头行为注释，空行忽略。
    --output    报告目录；生成 report.txt（人读报告）与 replacements.txt
                （`git filter-repo --replace-text` 用的替换映射，仅含有命中的字面量）。

内置通用形态（只报告、供人工复核，不自动生成替换）：中国大陆手机号、长形态 sk- 密钥、
非空 SESSDATA/access_token、本机盘符路径（D:\\xm、D:\\tools）。

纪律：只读 git 对象，不改任何历史与工作区；控制台输出 ASCII（GBK 安全），报告文件 UTF-8。
幂等：可重复运行，每次重写同一组报告文件。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 10 * 1024 * 1024  # 超过 10 MiB 的 blob 跳过内容扫描（仍计入跳过数）

# 通用形态：名字 → 正则。命中一律标记 REVIEW（测试桩/文档示例会误报，人工判断）。
GENERIC_PATTERNS: dict[str, str] = {
    'cn-mobile(11位手机号形态)': r'(?<!\d)1[3-9]\d{9}(?!\d)',
    'api-key(sk-长密钥形态)': r'sk-[A-Za-z0-9_-]{20,}',
    'bili-cookie(SESSDATA非空)': r'SESSDATA=\S+',
    'access-token(非空值)': r'access_token\s*[:=]\s*[^"\s]+',
    'local-path(D盘本机路径)': r'D:(?:\\|/)(?:xm|tools)\S*',
}


def git_bytes(*args: str) -> bytes:
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True)
    return result.stdout


def git_text(*args: str) -> str:
    return git_bytes(*args).decode('utf-8', errors='replace')


@dataclass
class Hit:
    occurrences: int = 0
    blob_count: int = 0
    paths: Counter = field(default_factory=Counter)
    in_commit_messages: int = 0


def read_literals(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f'literals 文件不存在: {path}（敏感字面量只放仓库外私有目录）')
    lines = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line and not line.startswith('#'):
            lines.append(line)
    return lines


def iter_blobs() -> list[tuple[str, str, int]]:
    """全部可达对象中的 blob：(sha, 首见路径, 字节数)。同 sha 多路径只记一次。"""
    entries: dict[str, str] = {}
    for line in git_text('rev-list', '--objects', '--all').splitlines():
        if not line:
            continue
        sha, _, path = line.partition(' ')
        entries.setdefault(sha, path.strip())
    if not entries:
        return []
    proc = subprocess.run(['git', '-C', str(ROOT), 'cat-file', '--batch-check'],
                          input='\n'.join(entries).encode('utf-8'),
                          capture_output=True, check=True)
    blobs: list[tuple[str, str, int]] = []
    for line in proc.stdout.decode('utf-8', errors='replace').splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == 'blob':
            blobs.append((parts[0], entries.get(parts[0], ''), int(parts[2])))
    return blobs


def scan_blobs(blobs: list[tuple[str, str, int]],
               literal_scanners: list[tuple[str, re.Pattern[str]]],
               generic_scanners: list[tuple[str, re.Pattern[str]]],
               hits: dict[str, Hit]) -> int:
    """流式读取全部 blob 内容并扫描；返回跳过的大 blob 数。"""
    proc = subprocess.Popen(['git', '-C', str(ROOT), 'cat-file', '--batch'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdin and proc.stdout
    skipped = 0
    try:
        for sha, path, _size in blobs:
            proc.stdin.write(f'{sha}\n'.encode('utf-8'))
            proc.stdin.flush()
            header = proc.stdout.readline().decode('utf-8', errors='replace').split()
            if len(header) < 3 or header[1] != 'blob':
                continue  # 对象消失等异常：跳过该行（协议上批处理逐行返回）
            real_size = int(header[2])
            if real_size > MAX_BLOB_BYTES:
                remaining = real_size
                while remaining > 0:
                    chunk = proc.stdout.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                skipped += 1
                continue
            content = proc.stdout.read(real_size)
            proc.stdout.read(1)  # 协议尾随换行
            text = content.decode('utf-8', errors='replace')
            for name, pattern in literal_scanners + generic_scanners:
                found = len(pattern.findall(text))
                if found:
                    hit = hits.setdefault(name, Hit())
                    hit.occurrences += found
                    hit.blob_count += 1
                    hit.paths[path or '<无名对象>'] += found
    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.wait()
    return skipped


def scan_commit_messages(literals: list[str], hits: dict[str, Hit]) -> int:
    """扫描全部提交信息；返回命中次数。"""
    total = 0
    records = git_text('log', '--all', '--format=%H%x00%B%x00').split('\0')
    for i in range(0, len(records) - 1, 2):
        body = records[i + 1] if i + 1 < len(records) else ''
        for literal in literals:
            found = body.count(literal)
            if found:
                hits.setdefault(literal, Hit()).in_commit_messages += found
                total += found
        for name, pattern in GENERIC_PATTERNS.items():
            found = len(re.findall(pattern, body))
            if found:
                hits.setdefault(name, Hit()).in_commit_messages += found
                total += found
    return total


def write_report(out_dir: Path, literals: list[str], hits: dict[str, Hit],
                *, commits: int, blobs: int, skipped: int) -> None:
    lines = [
        '== SENSITIVE AUDIT REPORT ==',
        f'repo: {ROOT}',
        f'generated: {datetime.now().isoformat(timespec="seconds")}',
        f'commits scanned: {commits}',
        f'blobs scanned: {blobs - skipped} (skipped >10MiB: {skipped})',
        '',
    ]
    for name in list(literals) + list(GENERIC_PATTERNS):
        hit = hits.get(name)
        if not hit:
            lines.append(f'[{name}] CLEAN (0 hits)')
            lines.append('')
            continue
        kind = 'CONFIRMED' if name in literals else 'REVIEW(测试桩/示例会误报,人工判断)'
        lines.append(f'[{name}] {kind}')
        lines.append(f'  total occurrences: {hit.occurrences}  in {hit.blob_count} blobs'
                     + (f'  + {hit.in_commit_messages} in commit messages' if hit.in_commit_messages else ''))
        for path, count in hit.paths.most_common(30):
            lines.append(f'  {count:6d}  {path}')
        lines.append('')
    (out_dir / 'report.txt').write_text('\n'.join(lines), encoding='utf-8')

    replacements = []
    for index, literal in enumerate(literals, start=1):
        if literal in hits:
            # 数字字面量给数字占位（保持 config.yaml 列表 / HTML placeholder 语境合法），其余用 REMOVED。
            placeholder = f'1000000000{index}' if literal.isdigit() else '***REMOVED***'
            replacements.append(f'{literal}==>{placeholder}')
    (out_dir / 'replacements.txt').write_text('\n'.join(replacements) + ('\n' if replacements else ''),
                                              encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='git 全历史敏感内容审计（只读）')
    parser.add_argument('--literals', type=Path, required=True, help='私有字面量文件（仓库外）')
    parser.add_argument('--output', type=Path, required=True, help='私有报告目录（仓库外）')
    args = parser.parse_args()

    literals = read_literals(args.literals)
    args.output.mkdir(parents=True, exist_ok=True)

    commits = int(git_text('rev-list', '--all', '--count').strip())
    blobs = iter_blobs()
    literal_scanners = [(lit, re.compile(re.escape(lit))) for lit in literals]
    generic_scanners = [(name, re.compile(pattern)) for name, pattern in GENERIC_PATTERNS.items()]

    hits: dict[str, Hit] = {}
    skipped = scan_blobs(blobs, literal_scanners, generic_scanners, hits)
    message_hits = scan_commit_messages(literals, hits)

    write_report(args.output, literals, hits,
                 commits=commits, blobs=len(blobs), skipped=skipped)

    confirmed = sum(1 for lit in literals if lit in hits)
    print(f'audit done: commits={commits} blobs={len(blobs)} skipped={skipped}')
    print(f'literal hits: {confirmed}/{len(literals)} literals found in history')
    print(f'commit-message hits: {message_hits}')
    print(f'report: {args.output / "report.txt"}')
    print(f'replacements: {args.output / "replacements.txt"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
