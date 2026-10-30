"""通用网页读取（D14）：有界 HTTP 抓取 + 正文提取。

边界在 `net.FetchPolicy`（协议/目标网络/每跳重定向/响应大小/超时），
正文与编码在 `extract`，工具契约在 `tools`。
"""
from .extract import Page, extract
from .net import FetchPolicy, Response, WebError, fetch, parse_target
from .tools import register_web_tools

__all__ = [
    "FetchPolicy",
    "Page",
    "Response",
    "WebError",
    "extract",
    "fetch",
    "parse_target",
    "register_web_tools",
]
