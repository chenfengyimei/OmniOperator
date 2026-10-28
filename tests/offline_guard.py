"""默认测试的 Python 出网闸门；子进程经 sitecustomize 继承。"""
import ipaddress
import os
import socket


class OfflineViolation(RuntimeError):
    pass


def local_host(host):
    if isinstance(host, bytes):
        host = host.decode('ascii', errors='replace')
    if host in {'localhost', None, ''}:
        return True
    try:
        address = ipaddress.ip_address(str(host).split('%')[0])
        return address.is_loopback or bool(getattr(address, 'ipv4_mapped', None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def deny(kind):
    path = os.environ.get('OMNI_TEST_VIOLATIONS')
    if path:
        with open(path, 'ab') as log:
            log.write((kind + '\n').encode('ascii'))
    raise OfflineViolation('默认测试禁止真实网络/模型调用，请注入 transport: ' + kind)


def install():
    if getattr(socket, '_omni_offline_installed', False):
        return
    socket._omni_offline_installed = True
    connect, connect_ex, getaddrinfo, sendto = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo, socket.socket.sendto
    def guard(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local_host(address[0]):
            deny('socket.connect')
        return connect(self, address)
    def guard_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not local_host(address[0]):
            deny('socket.connect_ex')
        return connect_ex(self, address)
    def resolve(host, *args, **kwargs):
        if not local_host(host):
            try:
                ipaddress.ip_address(host)  # 数字地址解析不发 DNS；连接仍被上方闸门拒绝。
            except ValueError:
                deny('socket.getaddrinfo')
        return getaddrinfo(host, *args, **kwargs)
    def send(self, data, *args):
        if self.family in (socket.AF_INET, socket.AF_INET6) and args and not local_host(args[-1][0]):
            deny('socket.sendto')
        return sendto(self, data, *args)
    socket.socket.connect, socket.socket.connect_ex = guard, guard_ex
    socket.getaddrinfo, socket.socket.sendto = resolve, send
