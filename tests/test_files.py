"""D13 结构化文件工具：路径边界、编码、并发与回滚。

这些用例刻意都用**临时工作区**：文件工具的失败模式是"改到工作区外的真实文件"，
测试一旦指向真机目录，就等于自己制造了一次事故。
"""
from __future__ import annotations

import codecs
import os
import stat
import threading
from pathlib import Path

import pytest

from omni.safety.gates import DenyApprover, Risk
from omni.tools.files import FileHistory, Workspace, register_file_tools
from omni.tools.files import io as fio
from omni.tools.registry import ToolRegistry


@pytest.fixture()
def env(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    history = FileHistory(tmp_path / "hist")
    registry = ToolRegistry()
    register_file_tools(
        registry, Workspace(root=root), history, lock_dir=tmp_path / "hist" / "locks"
    )
    return registry, root, history


def run(registry: ToolRegistry, name: str, **args) -> str:
    from omni.safety.gates import AllowAllApprover

    return registry.dispatch(name, args, approver=AllowAllApprover())


def _fingerprint(text: str) -> str:
    for line in text.splitlines():
        marker = "指纹 "
        if marker in line:
            return line.split(marker, 1)[1].split("）", 1)[0]
    raise AssertionError(f"输出里没有指纹：{text[:200]}")


def _link(link: Path, target: Path) -> None:
    """优先建 junction（Windows 上不需要管理员），失败再试符号链接。"""
    try:
        if os.name == "nt" and target.is_dir():
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError, ImportError):
        pytest.skip("本机不支持创建链接（需要符号链接权限）")


# ---------------------------------------------------------------- 基本读写


def test_read_and_list_keep_chinese_and_spaces(env):
    registry, root, _ = env
    (root / "说明 文档.txt").write_text("第一行\n第二行\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")

    listing = run(registry, "files_list", path=".")
    assert "说明 文档.txt" in listing and "src/" in listing

    body = run(registry, "files_read", path="说明 文档.txt")
    assert "第一行" in body and "共 2 行" in body and "指纹 " in body

    content = run(registry, "files_write", path="说明 文档.txt", content="第三行\n")
    assert "缺少 expected_hash" in content  # 没有指纹不许覆盖
    assert (root / "说明 文档.txt").read_text(encoding="utf-8") == "第一行\n第二行\n"


def test_read_window_reports_range_and_total(env):
    registry, root, _ = env
    (root / "many.txt").write_text("\n".join(f"line{i}" for i in range(1, 51)) + "\n", encoding="utf-8")

    head = run(registry, "files_read", path="many.txt", offset=1, limit=10)
    assert "共 50 行" in head and "第 1-10 行" in head and "line10" in head
    assert "line11" not in head

    tail = run(registry, "files_read", path="many.txt", offset=45, limit=10)
    assert "line50" in tail and "还有后续内容" not in tail

    beyond = run(registry, "files_read", path="many.txt", offset=99)
    assert "只有 50 行" in beyond


def test_gbk_and_crlf_round_trip(env):
    """GBK 文件读出来写回去还是 GBK + CRLF：一次编辑不该变成整文件重写。"""
    registry, root, _ = env
    target = root / "旧编码.txt"
    target.write_bytes("中文内容\r\n第二行\r\n".encode("gbk"))

    body = run(registry, "files_read", path="旧编码.txt")
    assert "gb18030" in body and "中文内容" in body

    written = run(
        registry,
        "files_write",
        path="旧编码.txt",
        content="中文内容\r\n改过的行\r\n",
        expected_hash=_fingerprint(body),
    )
    assert "已写入" in written
    assert target.read_bytes() == "中文内容\r\n改过的行\r\n".encode("gbk")


def test_bom_is_preserved(env):
    registry, root, _ = env
    target = root / "bom.txt"
    target.write_bytes(codecs.BOM_UTF8 + "hello\n".encode("utf-8"))
    body = run(registry, "files_read", path="bom.txt")
    assert "+BOM" in body

    run(
        registry,
        "files_write",
        path="bom.txt",
        content="hello world\n",
        expected_hash=_fingerprint(body),
    )
    assert target.read_bytes().startswith(codecs.BOM_UTF8)


@pytest.mark.parametrize("bom,label", [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")])
def test_utf16_with_bom_round_trip(env, bom, label):
    """D13 剩余限制 3 收口：记事本「Unicode」/PS 5.1 Out-File 存档的 UTF-16 可读写。

    编码与字节序原样写回；CRLF 在 UTF-16 字节形态下计数正确。
    无 BOM 的 UTF-16 与二进制区分不开，继续按二进制拒绝（fail-closed）。
    """
    registry, root, _ = env
    target = root / "unicode.txt"
    payload = "中文内容\r\n第二行\r\n"
    target.write_bytes(bom + payload.encode(label))

    body = run(registry, "files_read", path="unicode.txt")
    assert label in body and "中文内容" in body and "第二行" in body

    new_payload = "中文内容\r\n改过的行\r\n"
    written = run(
        registry,
        "files_write",
        path="unicode.txt",
        content=new_payload,
        expected_hash=_fingerprint(body),
    )
    assert "已写入" in written
    assert target.read_bytes() == bom + new_payload.encode(label)


def test_utf16_without_bom_stays_binary(env):
    """无 BOM 的 UTF-16 不猜：含 NUL 的（ASCII 为主）维持二进制拒绝。

    边界如实钉住：纯 CJK 内容的无 BOM UTF-16 恰好没有 NUL 字节
    （如「中文」= 2D 4E 87 65），会按 GB18030 误读——这是既有行为，
    与本批改动无关；无 BOM 场景不做 UTF-16 猜测正是为了避免这类误判。
    """
    registry, root, _ = env
    target = root / "no-bom-utf16.txt"
    target.write_bytes("ascii content".encode("utf-16-le"))
    assert "二进制" in run(registry, "files_read", path="no-bom-utf16.txt")
    assert "二进制" in run(
        registry, "files_write", path="no-bom-utf16.txt", content="x", mode="overwrite"
    )
    # 纯 CJK 无 BOM 无 NUL：按 GB18030 读（既有降级行为，不假装能识别）
    cjk = root / "cjk-no-bom.txt"
    cjk.write_bytes("中文".encode("utf-16-le"))
    body = run(registry, "files_read", path="cjk-no-bom.txt")
    assert "gb18030" in body and "二进制" not in body


def test_utf16_bom_is_not_reported_as_binary_anywhere(env):
    """UTF-16 全链路生效：写、diff、搜索都不再把带 BOM 的 UTF-16 当二进制。"""
    registry, root, _ = env
    target = root / "notes.txt"
    target.write_bytes(codecs.BOM_UTF16_LE + "线索：钥匙在抽屉\r\n".encode("utf-16-le"))
    found = run(registry, "files_search", query="钥匙")
    assert "notes.txt" in found and "线索：钥匙在抽屉" in found
    diff = run(registry, "files_diff", path="notes.txt", content="线索：钥匙在花盆\r\n")
    assert "花盆" in diff and "抽屉" in diff
    written = run(
        registry,
        "files_write",
        path="notes.txt",
        mode="replace",
        old_text="钥匙在抽屉",
        content="钥匙在花盆",
        expected_hash=_fingerprint(run(registry, "files_read", path="notes.txt")),
    )
    assert "已写入" in written
    assert target.read_bytes() == codecs.BOM_UTF16_LE + "线索：钥匙在花盆\r\n".encode("utf-16-le")


def test_binary_file_is_refused(env):
    registry, root, _ = env
    (root / "blob.bin").write_bytes(b"\x00\x01\x02PK\x03\x04")
    assert "二进制" in run(registry, "files_read", path="blob.bin")
    assert "二进制" in run(
        registry, "files_write", path="blob.bin", content="x", mode="overwrite"
    )
    assert (root / "blob.bin").read_bytes() == b"\x00\x01\x02PK\x03\x04"


def test_large_file_reads_by_window_without_fingerprint(env):
    registry, root, _ = env
    target = root / "big.log"
    target.write_text(("x" * 999 + "\n") * 4600, encoding="utf-8")  # ≈4.6MB
    body = run(registry, "files_read", path="big.log", limit=3)
    assert "总行数未知" in body and "未计算指纹" in body
    assert body.count("│") == 3


# ---------------------------------------------------------------- 路径边界


def test_path_outside_root_is_rejected(env):
    registry, _root, _ = env
    for path in ("../secret.txt", "C:/Windows/win.ini", "/etc/passwd"):
        assert "不在允许的根内" in run(registry, "files_read", path=path), path


def test_unc_device_and_ads_paths_are_rejected(env):
    registry, root, _ = env
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    cases = {
        r"\\?\C:\Windows\win.ini": "UNC",
        r"\\server\share\x.txt": "UNC",
        "a.txt:secret": "冒号",
        "C:relative.txt": "驱动器相对",
        "a.txt.": "点结尾",
        "dir /a.txt": "空格或点结尾",
        "CON.txt": "保留设备名",
    }
    for path, hint in cases.items():
        message = run(registry, "files_read", path=path)
        assert message.startswith("错误：") and hint in message, (path, message)


def test_link_escaping_root_is_rejected(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("不该被读到\n", encoding="utf-8")
    _link(root / "jump", outside)

    history = FileHistory(tmp_path / "hist")
    registry = ToolRegistry()
    register_file_tools(
        registry, Workspace(root=root), history, lock_dir=tmp_path / "hist" / "locks"
    )

    message = run(registry, "files_read", path="jump/secret.txt")
    assert "不在允许的根内" in message
    listing = run(registry, "files_list", path=".")
    assert "jump" in listing  # 列目录只显示链接本身，不展开内容


def test_extra_root_requires_explicit_authorization(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "note.md").write_text("授权目录里的内容\n", encoding="utf-8")

    strict = ToolRegistry()
    register_file_tools(
        strict, Workspace(root=root), FileHistory(tmp_path / "h1"), lock_dir=tmp_path / "h1"
    )
    assert "不在允许的根内" in run(strict, "files_read", path=str(other / "note.md"))

    allowed = ToolRegistry()
    register_file_tools(
        allowed,
        Workspace(root=root, extra_roots=[other]),
        FileHistory(tmp_path / "h2"),
        lock_dir=tmp_path / "h2",
    )
    body = run(allowed, "files_read", path=str(other / "note.md"))
    assert "授权目录里的内容" in body

    # 模型自己写一个 root 参数不会扩权：路径仍然要过根校验
    sneaky = run(allowed, "files_read", path=str(other / "note.md"), root="/")
    assert "授权目录里的内容" in sneaky  # 参数被忽略（没有 root 这个入参）
    assert "不在允许的根内" in run(allowed, "files_read", path="C:/Windows/win.ini")


def test_sensitive_files_are_hidden_everywhere(env):
    registry, root, _ = env
    (root / ".env").write_text("API_KEY=sk-canary\n", encoding="utf-8")
    (root / "id_rsa").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "state.db").write_text("secret-row\n", encoding="utf-8")
    (root / "notes.md").write_text("正常内容\n", encoding="utf-8")

    listing = run(registry, "files_list", path=".")
    assert ".env" not in listing and "id_rsa" not in listing and "data/" not in listing
    assert "notes.md" in listing

    hits = run(registry, "files_search", query="sk-canary")
    # 查询词本身会回显（那是模型给的），不能出现的是**命中行**与文件名
    assert "命中 0 处" in hits and ".env:" not in hits and "API_KEY=sk-canary" not in hits
    assert "跳过" in hits

    assert "密钥" in run(registry, "files_read", path=".env")
    assert "受保护目录" in run(registry, "files_read", path="data/state.db")


def test_workspace_metadata_dirs_are_not_writable(env):
    registry, root, _ = env
    result = run(registry, "files_write", path="data/new.txt", content="x", mode="create")
    assert "受保护目录" in result or "不可写" in result
    assert not (root / "data").exists()


# ---------------------------------------------------------------- 写入语义


def test_write_conflict_does_not_overwrite_user_changes(env):
    registry, root, _ = env
    target = root / "config.txt"
    target.write_text("v1\n", encoding="utf-8")
    stale = _fingerprint(run(registry, "files_read", path="config.txt"))

    target.write_text("用户刚改成了 v2\n", encoding="utf-8")  # 用户/编辑器改过

    message = run(registry, "files_write", path="config.txt", content="v3\n", expected_hash=stale)
    assert "拒绝覆盖" in message and "本次没有写入任何内容" in message
    assert target.read_text(encoding="utf-8") == "用户刚改成了 v2\n"


def test_write_is_atomic_and_reports_readback(env):
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    body = run(registry, "files_read", path="a.txt")

    result = run(
        registry, "files_write", path="a.txt", content="new\n", expected_hash=_fingerprint(body)
    )
    assert "回读校验通过" in result and "备份 rev 1" in result and "+new" in result
    assert target.read_text(encoding="utf-8") == "new\n"
    assert [s.summary for s in FileHistory(root.parent / "hist").list("a.txt")] == [
        "写入前自动备份"
    ]


def test_replace_requires_unique_old_text(env):
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("foo\nfoo\nbar\n", encoding="utf-8")
    body = run(registry, "files_read", path="a.txt")

    twice = run(
        registry,
        "files_write",
        path="a.txt",
        mode="replace",
        old_text="foo",
        content="baz",
        expected_hash=_fingerprint(body),
    )
    assert "出现 2 次" in twice

    missing = run(
        registry,
        "files_write",
        path="a.txt",
        mode="replace",
        old_text="nope",
        content="baz",
        expected_hash=_fingerprint(body),
    )
    assert "出现 0 次" in missing
    assert target.read_text(encoding="utf-8") == "foo\nfoo\nbar\n"

    ok = run(
        registry,
        "files_write",
        path="a.txt",
        mode="replace",
        old_text="bar",
        content="baz",
        expected_hash=_fingerprint(body),
    )
    assert "已写入" in ok
    assert target.read_text(encoding="utf-8") == "foo\nfoo\nbaz\n"


def test_create_mode_and_missing_parents(env):
    registry, root, _ = env
    refused = run(registry, "files_write", path="deep/new.txt", content="x", mode="create")
    assert "父目录" in refused and "create_dirs" in refused

    made = run(
        registry,
        "files_write",
        path="deep/new.txt",
        content="x\n",
        mode="create",
        create_dirs=True,
    )
    assert "已写入" in made and (root / "deep" / "new.txt").read_text(encoding="utf-8") == "x\n"

    again = run(registry, "files_write", path="deep/new.txt", content="y", mode="create")
    assert "已存在" in again


def test_diff_preview_does_not_write(env):
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    preview = run(registry, "files_diff", path="a.txt", content="one\nthree\n")
    assert "-two" in preview and "+three" in preview
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"

    same = run(registry, "files_diff", path="a.txt", content="one\ntwo\n")
    assert "没有变化" in same


def test_denied_approval_never_touches_disk(env):
    registry, root, _ = env
    tool = registry.get("files_write")
    assert tool is not None and tool.risk is Risk.CONFIRM

    result = registry.dispatch(
        "files_write",
        {"path": "new.txt", "content": "x", "mode": "create"},
        approver=DenyApprover(),
    )
    assert "未获批准" in result and not (root / "new.txt").exists()


def test_readonly_target_fails_without_damage(env):
    registry, root, _ = env
    if os.name != "nt":
        pytest.skip("只读文件阻挡替换是 Windows 语义（POSIX 只看目录权限）")
    target = root / "locked.txt"
    target.write_text("original\n", encoding="utf-8")
    body = run(registry, "files_read", path="locked.txt")
    os.chmod(target, stat.S_IREAD)
    try:
        result = run(
            registry,
            "files_write",
            path="locked.txt",
            content="changed\n",
            expected_hash=_fingerprint(body),
        )
        assert result.startswith("错误：") and "原文件未改动" in result
        os.chmod(target, stat.S_IWRITE)
        assert target.read_text(encoding="utf-8") == "original\n"
    finally:
        os.chmod(target, stat.S_IWRITE)


def test_concurrent_writers_only_one_wins(env):
    """同一个指纹只能换来一次写入：第二个必须在锁内重新读到新内容后失败。"""
    registry, root, _ = env
    target = root / "race.txt"
    target.write_text("v0\n", encoding="utf-8")
    token = _fingerprint(run(registry, "files_read", path="race.txt"))
    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker(text: str) -> None:
        barrier.wait()
        results.append(
            run(
                registry,
                "files_write",
                path="race.txt",
                content=text,
                expected_hash=token,
            )
        )

    threads = [threading.Thread(target=worker, args=(f"v{i}\n",)) for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum("已写入" in item for item in results) == 1
    assert sum("拒绝覆盖" in item for item in results) == 1
    assert target.read_text(encoding="utf-8") in {"v1\n", "v2\n"}


def test_readback_mismatch_is_reported(env, monkeypatch):
    """磁盘上写的不是我们以为的内容时，不能说"已保存"。"""
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("old\n", encoding="utf-8")
    body = run(registry, "files_read", path="a.txt")

    from omni.tools.files import tools as file_tools

    def truncated(path: Path, data: bytes) -> None:
        path.write_bytes(b"half")

    monkeypatch.setattr(file_tools, "atomic_write_bytes", truncated)
    result = run(
        registry, "files_write", path="a.txt", content="new\n", expected_hash=_fingerprint(body)
    )
    assert "回读校验不通过" in result and "不要假设已经保存成功" in result


# ---------------------------------------------------------------- 历史与回滚


def test_history_and_restore(env):
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    first = _fingerprint(run(registry, "files_read", path="a.txt"))
    run(registry, "files_write", path="a.txt", content="v2\n", expected_hash=first, summary="升级")

    after = run(registry, "files_read", path="a.txt")
    history = run(registry, "files_history", path="a.txt")
    assert "rev 1" in history and "升级（改前）" in history

    restored = run(
        registry,
        "files_restore",
        path="a.txt",
        rev=1,
        expected_hash=_fingerprint(after),
    )
    assert "恢复到 rev 1" in restored
    assert target.read_text(encoding="utf-8") == "v1\n"

    # 回滚本身也留了一版：再写回去还能找到 v2
    names = [item.brief() for item in FileHistory(root.parent / "hist").list("a.txt")]
    assert any("回滚到 rev 1" in item for item in names)


def test_restore_requires_current_fingerprint(env):
    registry, root, _ = env
    target = root / "a.txt"
    target.write_text("v1\n", encoding="utf-8")
    first = _fingerprint(run(registry, "files_read", path="a.txt"))
    run(registry, "files_write", path="a.txt", content="v2\n", expected_hash=first)
    target.write_text("用户改的\n", encoding="utf-8")

    result = run(registry, "files_restore", path="a.txt", rev=1, expected_hash=first)
    assert "拒绝回滚" in result and target.read_text(encoding="utf-8") == "用户改的\n"


# ---------------------------------------------------------------- 搜索


def test_search_is_bounded_and_scoped(env):
    registry, root, _ = env
    for index in range(5):
        (root / f"f{index}.py").write_text(f"needle {index}\n", encoding="utf-8")
    (root / "skip.md").write_text("needle in markdown\n", encoding="utf-8")

    result = run(registry, "files_search", query="needle", glob="*.py")
    assert result.count("needle ") == 5 and "skip.md" not in result

    limited = run(registry, "files_search", query="needle", limit=2)
    assert "命中已达上限" in limited

    bad = run(registry, "files_search", query="[", regex=True)
    assert "正则表达式不合法" in bad

    empty = run(registry, "files_search", query="   ")
    assert "不能为空" in empty


def test_identity_check_detects_swapped_file(tmp_path: Path):
    """检查与使用之间被换成另一个文件时，fstat/stat 的身份比对能发现。"""
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("a", encoding="utf-8")
    right.write_text("b", encoding="utf-8")
    fd = os.open(left, os.O_RDONLY)
    try:
        assert fio.identity_matches(fd, left)
        assert not fio.identity_matches(fd, right)
    finally:
        os.close(fd)


def test_web_approval_detail_is_truncated_for_huge_writes(env):
    """确认框里塞 2MB 内容等于没有确认：超长参数必须截断并说明。"""
    from omni.safety.gates import Decision, WebApprover

    registry, _root, _ = env
    tool = registry.get("files_write")

    class _Broker:
        def __init__(self) -> None:
            self.detail = ""

        def request(self, _tool, detail: str = "", **_kwargs):
            self.detail = detail
            return Decision(True)

    broker = _Broker()
    approver = WebApprover(broker)
    approver.check(tool, {"path": "a.txt", "content": "x" * 30000})
    assert len(broker.detail) <= approver.MAX_DETAIL_CHARS + 100
    assert "已截断" in broker.detail and "a.txt" in broker.detail


def test_windows_path_case_is_case_insensitive(env):
    """Windows 上 `README.md` 与 `readme.md` 是同一个文件：不能因为大小写就判越界。"""
    if os.name != "nt":
        pytest.skip("大小写不敏感是 Windows 语义")
    registry, root, _ = env
    (root / "Readme.md").write_text("hello\n", encoding="utf-8")
    body = run(registry, "files_read", path="readme.md")
    assert "hello" in body


def test_read_aborts_when_identity_check_fails(tmp_path, monkeypatch):
    """身份比对不通过时**一个字节都不读**。

    真实的"打开后被换成链接/另一个文件"在 Windows 上无法用 os.replace 构造
    （目标被占用时替换会被拒），所以这里直接让比对返回 False——
    比对本身能否发现换文件由 `test_identity_check_detects_swapped_file` 覆盖。
    """
    target = tmp_path / "a.txt"
    target.write_text("original\n", encoding="utf-8")
    monkeypatch.setattr("omni.tools.files.io.identity_matches", lambda fd, path: False)
    with pytest.raises(fio.PathRejected) as caught:
        fio.read_window(target, limit=5)
    assert caught.value.code == "path_changed"


def test_recheck_detects_parent_swapped_to_link(tmp_path: Path):
    """校验时是普通目录，写入前被换成指向根外的 junction：必须放弃。"""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "sub").mkdir()
    (root / "sub" / "a.txt").write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_text("outside\n", encoding="utf-8")

    workspace = Workspace(root=root)
    resolved = workspace.resolve("sub/a.txt")
    assert resolved.rel == "sub/a.txt"

    (root / "sub" / "a.txt").unlink()
    (root / "sub").rmdir()
    _link(root / "sub", outside)

    with pytest.raises(Exception) as caught:
        workspace.assert_unchanged(resolved)
    assert getattr(caught.value, "code", "") == "path_changed"
