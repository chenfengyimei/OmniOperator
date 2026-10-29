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
import json
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

COOKIE_NAME = "omni_session"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# 本地单用户工具：码长命。15 分钟一换的代价是"发出去的链接随时会死"，
# 收益几乎为零——防暴力枚举靠的是 MAX_PAIR_FAILURES 换码，不是靠短 TTL。
PAIR_TTL_S = 24 * 3600
MAX_PAIR_FAILURES = 5
# 记住最近几个换掉的码：用户常去点聊天记录里更早那条，要能说清"这条是旧的"，
# 而不是判成"码错了"再记一次失败（点几次就把自己锁出去）。
RETIRED_KEEP = 8
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
        pair_ttl_s: float = PAIR_TTL_S,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._secret_path = Path(data_dir) / "web_secret"
        self._secret = self._load_or_create_secret()
        self.session_ttl = int(session_days * 86400)
        hosts = set(LOOPBACK_HOSTS)
        hosts.add(_split_host(bind_host))
        hosts.update(_split_host(h) for h in (extra_hosts or set()))
        self.allowed_hosts = frozenset(h for h in hosts if h)
        self._display_host = bind_host
        self._display_port = port
        self._pair_ttl = float(pair_ttl_s)
        self._retired: deque[str] = deque(maxlen=RETIRED_KEEP)
        restored = self._load_pair_state()
        if restored is None:
            self.pairing_code = self._new_code()
            self._pair_deadline = time.monotonic() + self._pair_ttl
        else:
            self.pairing_code, self._pair_deadline = restored
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

    @property
    def pair_deadline_epoch(self) -> float:
        """配对码的到期时刻（墙上时钟）。monotonic 只能算差值，落盘要给人看必须用墙上时钟。

        已过期时返回一个过去的时间点（不做 0 钳制）——读码方靠这个判断"手里这条还能不能用"，
        钳成"此刻"会让人误以为还剩 0 秒，等于没说。
        """
        return time.time() + (self._pair_deadline - time.monotonic())

    def _rotate_code(self) -> str:
        previous = getattr(self, "pairing_code", None)
        if previous:
            self._retired.append(previous)
        self.pairing_code = self._new_code()
        self._pair_deadline = time.monotonic() + getattr(self, "_pair_ttl", PAIR_TTL_S)
        self._pair_failures = 0
        # 旧码此刻已失效，落盘文件必须同步刷新，否则留下的就是一条死链
        self._dump_pair_url(self.pairing_url())
        return self.pairing_code

    def _load_pair_state(self) -> tuple[str, float] | None:
        """续用盘上还没过期的码，返回 (码, monotonic 截止时刻)。

        重启服务不该让用户手里的链接失效。配对码一旦落盘就已经是"本机可读"的
        东西，重启时复用它不扩大任何暴露面，却能省掉整轮
        「发链接 → 你点开是死的 → 我再重启」的往返。
        """
        try:
            raw = (self._data_dir / "run" / "pair_url.json").read_text(encoding="utf-8")
            data = json.loads(raw)
            code = str(data.get("code") or "").strip()
            expires_at = float(data.get("expires_at") or 0)
        except (OSError, ValueError, TypeError):
            return None
        if not code or not code.isascii():
            return None
        remaining = expires_at - time.time()
        if remaining <= 60:  # 快断气的码不值得再递出去，直接换新的
            return None
        return code, time.monotonic() + remaining

    def _ttl_text(self) -> str:
        ttl = getattr(self, "_pair_ttl", PAIR_TTL_S)
        if ttl >= 5400:
            return f"{ttl / 3600:.0f} 小时"
        return f"{ttl / 60:.0f} 分钟"

    def pairing_url(self) -> str:
        """控制台打印的配对链接：点开即完成配对（浏览器不需要前端配合）。"""
        host = self._display_host
        if ":" in host and not host.startswith("["):  # IPv6 字面量要加方括号
            host = f"[{host}]"
        return f"http://{host}:{self._display_port}/pair/{self.pairing_code}"

    def announce_pairing(self) -> str:
        """把当前配对链接打到控制台并返回该链接（唯一的码泄露渠道就是这里）。"""
        url = self.pairing_url()
        print(f"[工作台] 配对链接（{self._ttl_text()}内有效）：{url}", flush=True)
        self._dump_pair_url(url)
        return url

    def _dump_pair_url(self, url: str) -> None:
        """顺手把链接写进 data/run/，供助手/脚本读取，免去重启取码。

        两个文件：
        - `pair_url.txt`：只一行 URL，给人看。
        - `pair_url.json`：多了 `expires_at`（墙上时钟秒）。读码的一方必须先查这个
          字段——TTL 到点后服务会自动换码，而文件只在换码那一刻刷新，所以文件
          里的码**可能已经失效**。没有时间戳就无从判断，只能把死链递给用户。
        写失败无所谓：控制台输出仍是权威渠道。
        """
        try:
            run_dir = self._data_dir / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "pair_url.txt").write_text(url + "\n", encoding="utf-8")
            (run_dir / "pair_url.json").write_text(
                json.dumps(
                    {
                        "url": url,
                        "code": self.pairing_code,
                        "expires_at": round(self.pair_deadline_epoch, 3),
                        "expires_in_s": round(self.pair_deadline_epoch - time.time(), 1),
                        "ttl_s": round(getattr(self, "_pair_ttl", PAIR_TTL_S), 1),
                        "port": self._display_port,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def pair(self, code: str) -> str | Rejection:
        """校验配对码：成功返回会话 cookie 值，失败返回拒绝原因。

        输入一律先规整：空值与非 ASCII 都按"配对失败"处理，绝不抛异常——
        `hmac.compare_digest` 比较含非 ASCII 的 str 会抛 TypeError，而
        `/pair/{code}` 是任意人都能敲的路径，不能让它打成 500。
        """
        if time.monotonic() > self._pair_deadline:
            self._rotate_code()
            self.announce_pairing()
            return Rejection(
                "pair_expired", f"配对链接已过期，新的链接（{self._ttl_text()}有效）在工作台窗口里"
            )
        candidate = (code or "").strip()
        if not candidate or not candidate.isascii():
            return self._reject()
        if hmac.compare_digest(candidate, self.pairing_code):
            self._pair_failures = 0
            return self.issue_session()
        if candidate in self._retired:
            # 点的是聊天记录里更早那条。这不是暴力枚举，别记失败——
            # 否则自己人点几下旧链接就把当前的码锁掉了。
            return Rejection("pair_stale", "这条链接是早前的，已经换过码了")
        return self._reject()

    def _reject(self) -> Rejection:
        """记一次失败；超过阈值就换码，防暴力枚举。"""
        self._pair_failures += 1
        if self._pair_failures >= MAX_PAIR_FAILURES:
            self._rotate_code()
            self.announce_pairing()
            return Rejection(
                "pair_locked", "配对失败次数过多，已换码，最新链接在工作台窗口里"
            )
        return Rejection("pair_rejected", "配对码不正确")

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
