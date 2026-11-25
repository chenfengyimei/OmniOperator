"""默认测试的 Python 出网闸门；子进程经 sitecustomize 继承。"""

import ipaddress
import os
import socket
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from platformdirs import user_data_path


class OfflineViolation(RuntimeError):
    pass


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if host in {"localhost", None, ""}:
        return True
    try:
        address = ipaddress.ip_address(str(host).split("%")[0])
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def deny(kind):
    path = os.environ.get("OMNI_TEST_VIOLATIONS")
    if path:
        with open(path, "ab") as log:
            log.write((kind + "\n").encode("ascii"))
    raise OfflineViolation("默认测试禁止真实网络/模型调用，请注入 transport: " + kind)


def _directory_for_fd(fd):
    if sys.platform == "darwin":
        import fcntl

        name = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\0", 1)[0]
        return Path(os.fsdecode(name))
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


def filesystem_auditor(roots, refuse):
    """Block writes in this Python process, independently from other services' activity."""
    protected = tuple(Path(root).resolve() for root in roots)
    # CPython reports paths before resolving their optional directory descriptors.
    # Each tuple maps a path argument to its descriptor argument, if available.
    modifying = {
        "os.mkdir": ((0, 2),),
        "os.remove": ((0, 1),),
        "os.rmdir": ((0, 1),),
        "os.rename": ((0, 2), (1, 3)),
        "os.link": ((0, 2), (1, 3)),
        "os.symlink": ((0, None), (1, 2)),
        "os.chmod": ((0, 2),),
        "os.chown": ((0, 3),),
        "os.utime": ((0, 3),),
    }

    def guard(event, args):
        if event == "open":
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                return
            operands = ((0, None),)
        elif event == "sqlite3.connect":
            operands = ((0, None),)
        elif event in modifying:
            operands = modifying[event]
        else:
            return
        for path_index, fd_index in operands:
            value = args[path_index]
            if not isinstance(value, (str, bytes, os.PathLike)):
                continue
            value = os.fsdecode(value)
            if value.startswith("file:"):
                value = unquote(urlsplit(value).path)
                if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
                    value = value[1:]
            if value == ":memory:":
                continue
            path = Path(value)
            if fd_index is not None and args[fd_index] != -1 and not path.is_absolute():
                try:
                    directory = _directory_for_fd(args[fd_index])
                except OSError:
                    refuse("filesystem.dir_fd_unresolved")
                    continue
                if not directory.is_absolute():
                    refuse("filesystem.dir_fd_unresolved")
                    continue
                path = directory / path
            path = path.resolve()
            if any(
                path == root
                or path.is_relative_to(root)
                or (root.name == ".env" and path.parent == root.parent and path.name.startswith(".env."))
                for root in protected
            ):
                refuse("filesystem.real_instance")

    return guard


def install():
    if getattr(socket, "_omni_offline_installed", False):
        return
    socket._omni_offline_installed = True
    project = Path(__file__).resolve().parents[2]
    roots = [project / "data", project / "config", project / ".env", project / "skills"]
    roots.append(user_data_path("OmniOperator", appauthor=False) / "v2")
    if os.environ.get("OMNI_TEST_EXTRA_PROTECTED_ROOT"):
        roots.append(Path(os.environ["OMNI_TEST_EXTRA_PROTECTED_ROOT"]))
    sys.addaudithook(filesystem_auditor(roots, deny))
    connect, connect_ex, getaddrinfo, sendto = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
        socket.socket.sendto,
    )

    def guard(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local_host(address[0]):
            deny("socket.connect")
        return connect(self, address)

    def guard_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local_host(address[0]):
            deny("socket.connect_ex")
        return connect_ex(self, address)

    def resolve(host, *args, **kwargs):
        if not local_host(host):
            try:
                ipaddress.ip_address(host)  # 数字地址解析不发 DNS；连接仍被上方闸门拒绝。
            except ValueError:
                deny("socket.getaddrinfo")
        return getaddrinfo(host, *args, **kwargs)

    def send(self, data, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6) and args and not local_host(args[-1][0]):
            deny("socket.sendto")
        return sendto(self, data, *args)

    socket.socket.connect, socket.socket.connect_ex = guard, guard_ex
    socket.getaddrinfo, socket.socket.sendto = resolve, send
