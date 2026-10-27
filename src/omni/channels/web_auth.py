"""工作台本地身份：配对码 → HttpOnly 会话 cookie，外加 Host/Origin/CSRF 校验。

为什么需要（A07）：工作台原先没有任何身份校验，任何能访问 127.0.0.1:8000 的页面
（含恶意网页的跨站请求）都能改配置、批审批、启停服务、开终端。仅靠"只绑 localhost"
不是身份认证——DNS rebinding 就能绕过。

设计要点：
- **本地单所有者**：启动时在控制台打印一次性配对码，浏览器用 `#pair=码` 完成配对，
  之后只凭 HttpOnly + SameSite=Strict 的签名 cookie 访问；不引入账号体系，也不声称支持公网多租户。
- **stateless 会话**：cookie 里是 `sid.exp.signature`，签名用 `data/web_secret` 的随机密钥；
  删掉该文件（或换机器）即等于让所有已配对浏览器失效。
- **CSRF 三层**：SameSite=Strict（浏览器不跨站带 cookie）+ Origin 校验 + `Sec-Fetch-Site` 校验；
  不启用 CORS，也不把 CORS 当成认证。
- **Host 校验**：只接受本地主机名，挡住 DNS rebinding 把恶意域名解析到 127.0.0.1 的玩法。
- 密钥不落日志：本模块只打印配对码，不打印任何 token。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

COOKIE_NAME = "omni_session"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
PAIR_TTL_S = 15 * 60
MAX_PAIR_FAILURES = 5
SESSION_DAYS = 30
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


@dataclass(frozen=True)
class Rejection:
    """拒绝原因：给前端可判断的机器码 + 给人看的中文说明。"""

    code: str
    detail: str


def _split_host(value: str) -> str:
    """从 `host:port` / `[::1]:8000` 里取出主机名（小写）。"""
    value = (value or "").strip().lower()
    if value.startswith("["):  # IPv6
        end = value.find("]")
        return value[1:end] if end > 0 else value
    return value.split(":", 1)[0]


def _split_origin(origin: str) -> tuple[str, str] | None:
    """把 Origin 拆成 (scheme, host)，非法形态返回 None。"""
    origin = (origin or "").strip()
    if "://" not in origin:
        return None
    scheme, rest = origin.split("://", 1)
    if scheme.lower() not in ("http", "https"):
        return None
    host = rest.split("/", 1)[0]
    if not host or host == "null":
        return None
    return scheme.lower(), _split_host(host)


class WebAuth:
    """单所有者本地身份与会话（每个工作台进程一个实例）。"""

    def __init__(
        self,
        data_dir: Path,
        *,
        bind_host: str = "127.0.0.1",
        port: int = 8000,
        extra_hosts: set[str] | None = None,
        session_days: int = SESSION_DAYS,
    ) -> None:
        self._secret_path = Path(data_dir) / "web_secret"
        self._secret = self._load_or_create_secret()
        self.session_ttl = int(session_days * 86400)
        hosts = set(LOOPBACK_HOSTS)
        hosts.add(_split_host(bind_host))
        hosts.update(_split_host(h) for h in (extra_hosts or set()))
        self.allowed_hosts = frozenset(h for h in hosts if h)
        self._display_host = bind_host
        self._display_port = port
        self.pairing_code = self._new_code()
        self._pair_deadline = time.monotonic() + PAIR_TTL_S
        self._pair_failures = 0

    # ---------- 密钥与会话 ----------

    def _load_or_create_secret(self) -> bytes:
        try:
            existing = self._secret_path.read_bytes().strip()
            if len(existing) >= 32:
                return existing
        except OSError:
            pass
        secret = secrets.token_bytes(32)
        self._secret_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret_path.write_bytes(secret)
        try:  # 尽力收紧权限（Windows 上 chmod 语义有限，不视为安全边界）
            self._secret_path.chmod(0o600)
        except OSError:
            pass
        return secret

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def issue_session(self) -> str:
        """签一个新的会话 cookie 值（配对成功后下发）。"""
        sid = secrets.token_hex(16)
        expires = int(time.time()) + self.session_ttl
        payload = f"{sid}.{expires}"
        return f"{payload}.{self._sign(payload)}"

    def verify_session(self, value: str | None) -> bool:
        if not value or value.count(".") != 2:
            return False
        sid, expires_text, signature = value.split(".")
        try:
            expires = int(expires_text)
        except ValueError:
            return False
        if expires < int(time.time()):
            return False
        expected = self._sign(f"{sid}.{expires_text}")
        return hmac.compare_digest(expected, signature)

    # ---------- 配对 ----------

    def _new_code(self) -> str:
        return f"{secrets.randbelow(10**8):08d}"

    def _rotate_code(self) -> str:
        self.pairing_code = self._new_code()
        self._pair_deadline = time.monotonic() + PAIR_TTL_S
        self._pair_failures = 0
        return self.pairing_code

    def pairing_url(self) -> str:
        """控制台打印的配对链接：点开即完成配对（浏览器不需要前端配合）。"""
        host = self._display_host
        if ":" in host and not host.startswith("["):  # IPv6 字面量要加方括号
            host = f"[{host}]"
        return f"http://{host}:{self._display_port}/pair/{self.pairing_code}"

    def announce_pairing(self) -> str:
        """把当前配对链接打到控制台并返回该链接（唯一的码泄露渠道就是这里）。"""
        url = self.pairing_url()
        print(f"[工作台] 配对链接（{PAIR_TTL_S // 60} 分钟内有效）：{url}", flush=True)
        return url

    def pair(self, code: str) -> str | Rejection:
        """校验配对码：成功返回会话 cookie 值，失败返回拒绝原因。"""
        if time.monotonic() > self._pair_deadline:
            self._rotate_code()
            self.announce_pairing()
            return Rejection("pair_expired", "配对码已过期，已在控制台输出新的配对链接")
        if not hmac.compare_digest((code or "").strip(), self.pairing_code):
            self._pair_failures += 1
            if self._pair_failures >= MAX_PAIR_FAILURES:
                self._rotate_code()
                self.announce_pairing()
                return Rejection("pair_locked", "配对失败次数过多，配对码已更换，请看控制台")
            return Rejection("pair_rejected", "配对码不正确")
        self._pair_failures = 0
        return self.issue_session()

    # ---------- Host / Origin / CSRF ----------

    def host_ok(self, host_header: str | None) -> bool:
        """Host 头必须是本地主机名（挡 DNS rebinding：恶意域名解析到 127.0.0.1 也会被拒）。"""
        return _split_host(host_header or "") in self.allowed_hosts

    def origin_ok(self, origin: str | None) -> bool:
        """Origin 缺省视为同源（非浏览器客户端）；给了就必须是本机 http(s)。"""
        if origin is None:
            return True
        parsed = _split_origin(origin)
        if parsed is None:
            return False
        _scheme, host = parsed
        return host in self.allowed_hosts

    def csrf_ok(self, method: str, origin: str | None, fetch_site: str | None) -> bool:
        if method.upper() not in _UNSAFE_METHODS:
            return True
        if not self.origin_ok(origin):
            return False
        if fetch_site is not None and fetch_site.strip().lower() not in _SAFE_FETCH_SITES:
            return False
        return True

    def check_request(
        self,
        *,
        method: str,
        path: str,
        host: str | None,
        origin: str | None = None,
        fetch_site: str | None = None,
        cookie: str | None = None,
    ) -> Rejection | None:
        """HTTP 请求的总闸门：Host → CSRF（写方法）→ 会话。返回 None 表示放行。"""
        if not self.host_ok(host):
            return Rejection("host_rejected", "Host 不被允许：工作台只服务本地主机名")
        if not self.csrf_ok(method, origin, fetch_site):
            return Rejection("csrf_rejected", "拒绝跨站请求：Origin/Sec-Fetch-Site 与工作台不一致")
        if path in PUBLIC_PATHS:
            return None
        if not self.verify_session(cookie):
            return Rejection("auth_required", "需要先在工作台完成配对（控制台有配对链接）")
        return None

    def check_websocket(
        self, *, host: str | None, origin: str | None, cookie: str | None
    ) -> Rejection | None:
        """终端 WS 握手：Host + Origin + 会话，三者缺一不可。"""
        if not self.host_ok(host):
            return Rejection("host_rejected", "Host 不被允许")
        if not self.origin_ok(origin):
            return Rejection("origin_rejected", "Origin 不被允许")
        if not self.verify_session(cookie):
            return Rejection("auth_required", "需要先在工作台完成配对")
        return None


# 无需会话即可访问的接口：状态查询与配对本身
PUBLIC_PATHS = frozenset({"/api/auth/status", "/api/auth/pair"})
