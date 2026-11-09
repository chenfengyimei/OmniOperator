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


def filesystem_auditor(roots, refuse):
    """Block writes in this Python process, independently from other services' activity."""
    protected = tuple(Path(root).resolve() for root in roots)
    modifying = {
        "os.mkdir",
        "os.remove",
        "os.rmdir",
        "os.rename",
        "os.link",
        "os.symlink",
        "os.chmod",
        "os.chown",
        "os.utime",
    }

    def guard(event, args):
        if event == "open":
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                return
            paths = args[:1]
        elif event == "sqlite3.connect":
            paths = args[:1]
        elif event in modifying:
            paths = args[:2] if event in {"os.rename", "os.link", "os.symlink"} else args[:1]
        else:
            return
        for value in paths:
            if not isinstance(value, (str, bytes, os.PathLike)):
                continue
            value = os.fsdecode(value)
            if value.startswith("file:"):
                value = unquote(urlsplit(value).path)
                if os.name == "nt" and len(value) > 2 and value[0] == "/" and value[2] == ":":
                    value = value[1:]
            if value == ":memory:":
                continue
            path = Path(value).resolve()
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
