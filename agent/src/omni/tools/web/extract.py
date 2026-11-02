"""正文提取（D14）：把 HTML 变成"能读的文本"，不执行任何东西。

为什么自己写解析器：`readability` 类库会把整段 HTML 载入并做大量启发式处理，
面对畸形页面/超大文档时行为不可预测；这里只需要"去掉脚本样式、保留块结构、
收集标题与链接"，`html.parser` 足够，而且边界清楚（不联网、不执行脚本、不解析 CSS）。

编码推断顺序：HTTP 头 → `<meta charset>` → UTF-8 → GB18030 → 替换解码。
中文站点大量是 GBK 且不写头，顺序反了就会得到一屏乱码。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "iframe", "canvas", "object"})
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "blockquote",
        "pre",
        "table",
        "ul",
        "ol",
        "hr",
        "main",
        "aside",
        "form",
        "figure",
        "figcaption",
    }
)
MAX_LINK_TEXT = 80


@dataclass
class Page:
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)
    description: str = ""
    looks_like_login: bool = False
    charset: str = ""
    truncated: bool = False
    original_chars: int = 0


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0
        self._in_title = False
        self._title: list[str] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._links: list[tuple[str, str]] = []
        self.description = ""
        self.has_password = False

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[override]
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag in BLOCK_TAGS:
            self._parts.append("\n")
        if tag == "a" and values.get("href"):
            self._href = values["href"]
            self._link_text = []
        if tag == "input" and values.get("type", "").lower() == "password":
            self.has_password = True
        if tag == "meta" and not self.description and values.get("name", "").lower() == "description":
            self.description = values.get("content", "")

    def handle_endtag(self, tag: str) -> None:  # type: ignore[override]
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag in BLOCK_TAGS:
            self._parts.append("\n")
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._link_text).split())[:MAX_LINK_TEXT]
            self._links.append((self._href, text))
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:  # type: ignore[override]
        if self._in_title:
            self._title.append(data)
            return
        if self._skip:
            return
        if self._href is not None:
            self._link_text.append(data)
        self._parts.append(data)

    @property
    def title(self) -> str:
        return " ".join("".join(self._title).split())

    @property
    def text(self) -> str:
        return _normalise("".join(self._parts))

    @property
    def links(self) -> list[tuple[str, str]]:
        return list(self._links)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v\u00a0\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_body(body: bytes, header_charset: str = "", *, sniff_bytes: int = 4096) -> tuple[str, str]:
    """按 头 → meta → UTF-8 → GB18030 的顺序解码；返回 (文本, 实际编码)。"""
    candidates: list[str] = []
    if header_charset:
        candidates.append(_normalise_charset(header_charset))
    meta = _meta_charset(body[:sniff_bytes])
    if meta:
        candidates.append(_normalise_charset(meta))
    candidates += ["utf-8", "gb18030"]
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        try:
            return body.decode(name), name
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8"


def _normalise_charset(name: str) -> str:
    lowered = name.strip().strip("'\"").lower()
    # gb2312 其实是 GBK 的子集，很多站点声明 gb2312 但正文里有扩展字
    if lowered in {"gb2312", "gbk", "gb_2312-80", "cp936"}:
        return "gb18030"
    return lowered


_META_RE = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)


def _meta_charset(head: bytes) -> str:
    match = _META_RE.search(head)
    return match.group(1).decode("ascii", errors="ignore") if match else ""


def is_html(content_type: str) -> bool:
    media = content_type.split(";", 1)[0].strip().lower()
    return media in {"text/html", "application/xhtml+xml"} or not media


def extract(
    body: bytes,
    content_type: str,
    *,
    base_url: str,
    header_charset: str = "",
    max_chars: int = 8000,
    max_links: int = 0,
) -> Page:
    text, charset = decode_body(body, header_charset)
    if not is_html(content_type):
        page = Page(text="", charset=charset)
        page.original_chars = len(text)
        page.text = _clip(text.strip(), max_chars)
        page.truncated = page.original_chars > len(page.text)
        return page

    parser = _Extractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001, S110  —— 畸形 HTML 不该让整次读取失败
        pass
    body_text = parser.text
    page = Page(
        title=parser.title,
        description=" ".join(parser.description.split())[:300],
        charset=charset,
        original_chars=len(body_text),
        looks_like_login=_looks_like_login(parser, body_text),
    )
    page.text = _clip(body_text, max_chars)
    page.truncated = page.original_chars > len(page.text)
    page.links = _links(parser.links, base_url, max_links)
    return page


def _clip(text: str, max_chars: int) -> str:
    limit = max(int(max_chars), 1)
    return text if len(text) <= limit else text[:limit]


def _looks_like_login(parser: _Extractor, text: str) -> bool:
    if parser.has_password:
        return True
    title = parser.title.lower()
    hints = ("登录", "登陆", "login", "sign in", "signin")
    return any(word in title for word in hints) and len(text) < 600


def _links(raw: list[tuple[str, str]], base_url: str, max_links: int) -> list[tuple[str, str]]:
    if max_links <= 0:
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for href, label in raw:
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append((label or parts.path or absolute, absolute))
        if len(out) >= max_links:
            break
    return out
