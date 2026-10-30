"""有界 HTTP 抓取（D14）：协议、目标网络、重定向、大小、超时全部受控。

为什么不用 `requests` / `urllib.request` 一把梭：
- `urllib` 会跟随重定向到任意目标，包括 `http://169.254.169.254/`（云元数据）和局域网设备；
- DNS 解析与连接是两次独立动作，解析结果可以被换掉（DNS rebinding），所以这里**先解析、
  校验所有地址、再把连接钉在校验过的 IP 上**，而不是"校验域名然后让 socket 自己再解析一遍"；
- 需要能明确说"读到多少就停"：超大流不能先落盘再看大小。

明确不做：不跟随非 http(s) 协议；不携带任何 Cookie/Authorization；不读环境变量里的代理；
不做反爬绕过与登录（登录页只能识别并如实说明）。
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_URL_CHARS = 2048
USER_AGENT = "OmniOperator/0.1 (personal local agent; single-page fetch)"
TEXTUAL_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
)
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class WebError(RuntimeError):
    """抓取失败：`code` 稳定，`reason` 给人看（不回显任何本机路径或凭据）。"""

    def __init__(self, reason: str, *, code: str = "fetch_failed", retryable: bool = False) -> None:
        self.reason = reason
        self.code = code
        self.retryable = retryable
        super().__init__(reason)


@dataclass(frozen=True)
class FetchPolicy:
    """一次抓取的硬边界（来自 config.web，由 bootstrap 注入）。"""

    timeout_s: float = 15.0
    total_deadline_s: float = 30.0
    max_bytes: int = 2_000_000
    max_redirects: int = 5
    allow_private_hosts: tuple[str, ...] = ()
    user_agent: str = ""

    @property
    def ua(self) -> str:
        """配置留空就用内置 UA：明确是自动抓取，不伪装成浏览器。"""
        return self.user_agent.strip() or USER_AGENT

    def host_allowed(self, host: str, port: int) -> bool:
        """内网/本机目标只有在配置里逐条写下的 host 或 host:port 才算授权。"""
        target = host.lower()
        with_port = f"{target}:{port}"
        for item in self.allow_private_hosts:
            allowed = str(item).strip().lower()
            if allowed and allowed in {target, with_port}:
                return True
        return False


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content_type: str
    body: bytes
    truncated: bool
    redirects: list[str] = field(default_factory=list)
    charset: str = ""
    elapsed_s: float = 0.0
    retry_after: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str          # ASCII（IDNA 编码后）
    port: int
    path: str          # 已百分号编码的路径
    query: str = ""    # 已百分号编码的查询串

    @property
    def netloc(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"

    @property
    def request_target(self) -> str:
        return f"{self.path}?{self.query}" if self.query else self.path

    @property
    def url(self) -> str:
        return urlunsplit((self.scheme, self.netloc, self.path, self.query, ""))


# ---------------------------------------------------------------- URL 与目标


def parse_target(raw: str) -> Target:
    text = str(raw or "").strip()
    if not text:
        raise WebError("url 不能为空", code="empty_url")
    if len(text) > MAX_URL_CHARS:
        raise WebError(f"url 过长（>{MAX_URL_CHARS} 字符）", code="url_too_long")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise WebError("url 含控制字符", code="bad_url")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise WebError(f"url 不合法：{exc}", code="bad_url") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise WebError(
            f"只支持 http/https，收到 {scheme or '空协议'}"
            "（file/ftp/data/javascript 等一律不读取）",
            code="bad_scheme",
        )
    if parts.username or parts.password:
        raise WebError(
            "url 里带账号密码：拒绝发送。本工具不携带任何登录态，凭据请让用户另行授权",
            code="url_credentials",
        )
    host = (parts.hostname or "").strip()
    if not host:
        raise WebError("url 里没有主机名", code="bad_url")
    if _ip_literal(host):
        ascii_host = host.lower()  # IP 字面量不走 IDNA：冒号与方括号是语法的一部分
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise WebError(f"主机名不合法：{host}", code="bad_host") from exc
        if len(ascii_host) > 253 or any(len(label) > 63 for label in ascii_host.split(".")):
            raise WebError("主机名过长", code="bad_host")
        if not all(ch.isalnum() or ch in ".-_" for ch in ascii_host):
            raise WebError(f"主机名含不允许的字符：{ascii_host}", code="bad_host")
    default_port = 443 if scheme == "https" else 80
    final_port = int(port if port is not None else default_port)
    if not 1 <= final_port <= 65535:
        raise WebError("端口不合法", code="bad_port")
    # 非 ASCII 路径要编码：http.client 只发 ASCII，中文路径原样塞进去会直接抛
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&%+:/?@!$'()*,;.-_~")
    return Target(scheme=scheme, host=ascii_host, port=final_port, path=path, query=query)


def _ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False
    return bool(addr.is_global) and not addr.is_multicast


def resolve_host(host: str, port: int) -> list[str]:
    """解析成 IP 列表（去重、保序）。测试用替换此函数模拟 DNS 变化。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise WebError(f"域名解析失败：{host}（{exc}）", code="dns_failed") from exc
    ips: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in ips:
            ips.append(address)
    return ips


def check_target(target: Target, policy: FetchPolicy) -> list[str]:
    """解析并校验目标；返回可连接的 IP（已确认全部为公网，或该 host 被显式授权）。"""
    ips = resolve_host(target.host, target.port)
    if not ips:
        raise WebError(f"域名解析失败：{target.host}", code="dns_failed")
    allowed = policy.host_allowed(target.host, target.port)
    if allowed:
        return ips
    for ip in ips:
        if not is_public_ip(ip):
            raise WebError(
                f"{target.host} 解析到内网/本机地址 {ip}：默认拒绝（云元数据、局域网设备、"
                "本机服务都不能从这里读）。确实需要时在 config.yaml 的 web.allow_private_hosts "
                "里显式写下该主机",
                code="private_target",
            )
    return ips


# ---------------------------------------------------------------- 连接（钉 IP）

class _PinnedHTTP(http.client.HTTPConnection):
    """连接钉在校验过的 IP 上；`Host` 头仍然是域名（虚拟主机才能正常响应）。"""

    def __init__(self, host: str, port: int, *, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self) -> None:  # type: ignore[override]
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(
        self, host: str, port: int, *, ip: str, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip

    def connect(self) -> None:  # type: ignore[override]
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        # SNI 与证书校验用域名，连接用 IP：既避免二次解析，也不放弃 TLS 校验
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def build_ssl_context() -> ssl.SSLContext:
    """系统证书库 + 公共 CA 包（certifi）都加载，取并集。

    只用系统库：很多 Windows 机器上 `create_default_context()` 拿不到公共根证书，
    `https` 直接报 `unable to get local issuer certificate`；
    只用 certifi：装了企业代理根证书的环境反而连不上。两边都加载才是"都能连"。
    明确不做：不提供"跳过证书校验"的开关——那等于把中间人攻击变成默认。
    """
    context = ssl.create_default_context()
    try:  # certifi 随 httpx/openai 一起装进来；没有也不影响系统库路径
        import certifi

        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return context


def _open(target: Target, ip: str, timeout: float) -> http.client.HTTPConnection:
    if target.scheme == "https":
        context = build_ssl_context()
        return _PinnedHTTPS(target.host, target.port, ip=ip, timeout=timeout, context=context)
    return _PinnedHTTP(target.host, target.port, ip=ip, timeout=timeout)


# ---------------------------------------------------------------- 抓取


def fetch(raw_url: str, policy: FetchPolicy) -> Response:
    started = time.monotonic()
    deadline = started + max(float(policy.total_deadline_s), 0.1)
    target = parse_target(raw_url)
    redirects: list[str] = []

    for hop in range(max(int(policy.max_redirects), 0) + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WebError(
                f"总时限 {policy.total_deadline_s:.0f}s 用尽（重定向 {len(redirects)} 次后仍未完成）",
                code="timeout",
                retryable=True,
            )
        ips = check_target(target, policy)
        response, location = _request_once(target, ips, min(policy.timeout_s, remaining), policy)
        if response.status in REDIRECT_STATUSES and location:
            if hop >= policy.max_redirects:
                raise WebError(
                    f"重定向次数超过上限 {policy.max_redirects}（最后一跳：{target.url}）",
                    code="too_many_redirects",
                )
            try:
                nxt = parse_target(urljoin(target.url, location))
            except WebError as exc:
                raise WebError(
                    f"重定向目标不被允许（{exc.reason}）；已停在 {target.url}",
                    code=exc.code,
                ) from exc
            redirects.append(target.url)
            target = nxt
            continue
        body, truncated = _read_body(response, policy.max_bytes, deadline)
        elapsed = time.monotonic() - started
        return Response(
            url=target.url,
            status=response.status,
            content_type=str(response.headers.get("content-type") or ""),
            body=body,
            truncated=truncated,
            redirects=redirects,
            charset=_header_charset(str(response.headers.get("content-type") or "")),
            elapsed_s=elapsed,
            retry_after=str(response.headers.get("retry-after") or ""),
        )

    raise WebError(  # pragma: no cover - 循环里已处理
        "重定向次数过多", code="too_many_redirects"
    )


def _request_once(
    target: Target, ips: list[str], timeout: float, policy: FetchPolicy
) -> tuple[http.client.HTTPResponse, str]:
    last_error: Exception | None = None
    timed_out = False
    for ip in ips[:2]:  # 最多试两个地址（通常是 v6/v4 各一个）
        conn = _open(target, ip, timeout)
        try:
            conn.request(
                "GET",
                target.request_target,
                headers={
                    "Host": target.netloc,
                    "User-Agent": policy.ua,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
                    "Accept-Encoding": "identity",  # 不协商压缩：读多少字节就是多少
                    "Connection": "close",
                },
            )
            response = conn.getresponse()
            # 响应对象持有连接；调用方读完 body 后由它自己关闭
            response._omni_conn = conn  # type: ignore[attr-defined]
            location = str(response.headers.get("location") or "").strip()
            if response.status in REDIRECT_STATUSES:
                # 重定向响应不读 body（有的服务器会在这里挂一个巨大页面）
                conn.close()
            return response, location
        except (socket.timeout, TimeoutError) as exc:
            last_error = exc
            timed_out = True
            conn.close()
        except WebError:
            conn.close()
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            last_error = exc
            conn.close()
    if timed_out:
        raise WebError(
            f"连接 {target.host}:{target.port} 超时（{timeout:.1f}s 内没有响应）",
            code="timeout",
            retryable=True,
        )
    raise WebError(
        f"连接 {target.host}:{target.port} 失败（{type(last_error).__name__}: {last_error}）",
        code="connect_failed",
        retryable=True,
    )


def _read_body(
    response: http.client.HTTPResponse, max_bytes: int, deadline: float
) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    try:
        while total <= max_bytes:
            if time.monotonic() > deadline:
                raise WebError("读取响应超时（总时限用尽）", code="timeout", retryable=True)
            try:
                chunk = response.read(min(65536, max_bytes + 1 - total))
            except (socket.timeout, TimeoutError) as exc:
                raise WebError(
                    "读取响应超时（服务器在传输中途停住）", code="timeout", retryable=True
                ) from exc
            except (OSError, http.client.HTTPException) as exc:
                raise WebError(
                    f"读取响应失败（{type(exc).__name__}: {exc}）", code="read_failed"
                ) from exc
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            truncated = True
    finally:
        conn = getattr(response, "_omni_conn", None)
        if conn is not None:
            conn.close()
    body = b"".join(chunks)
    return body[:max_bytes], truncated


def _header_charset(content_type: str) -> str:
    for part in content_type.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip("'\"")
    return ""


def is_textual(content_type: str) -> bool:
    """没有 Content-Type 时按文本处理（很多老站点不写）；明确是图片/压缩包等则拒绝。"""
    media = content_type.split(";", 1)[0].strip().lower()
    if not media:
        return True
    if media.startswith("text/"):
        return True
    return any(media == allowed for allowed in TEXTUAL_TYPES)
