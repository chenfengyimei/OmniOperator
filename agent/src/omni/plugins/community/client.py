"""社区客户端：插件与人设的索引 / 详情 / 安装 / 发布。

本地配置存 `data/plugins/community.json`（server_url + token）——刻意不进
config.yaml：社区接入点属于用户级偏好而非运行配置，且 config.py 由并行
前端批持有；换社区服务器 = 换一个 URL，一行配置的事。

同一个站点同时分发插件（zip 包）与人设（JSON 卡）：官方源就是
`market/server.py`（内容在 `market/content/`，不进库）。两类内容共用一条
HTTP 出口与一套错误映射，调用方按需取清单/详情。

信任定位：社区只做索引与分发（sha256 校验），**不背书插件安全性**
——下载下来的包仍要走本机的隔离安装 → 人工确认激活链，谁也绕不过去。
人设卡是纯文本（没有可执行内容），但仍逐字段收窄长度后才交给前端。

发布是项目红线之一（"发布需人工确认"）：`community_publish` 是
CONFIRM + requires_human——审批流里的人工确认就是"用户同意公开"这一步。
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ...system.path_guard import digest as _digest
from ..host import MAX_ZIP_BYTES, PluginHost

CONFIG_NAME = "community.json"
MAX_PUBLISH_BYTES = 4 * 1024 * 1024  # 发布侧：客户端不传超过安装侧上限的包
MAX_PUBLISH_ENTRIES = 50
HTTP_TIMEOUT_S = 30.0
MAX_INDEX_LIMIT = 50  # 一次最多取多少条索引（界面一屏足够）
# 官方默认源：部署好市场后由部署方通过环境变量固化，未设置时保持"未配置"
# ——不硬编码某个主机，避免把别人的服务器当成默认信任锚。
SERVER_ENV = "OMNI_MARKET_URL"


def default_server_url() -> str:
    """内置默认源（`OMNI_MARKET_URL`）；空串表示没有默认源。"""
    return os.environ.get(SERVER_ENV, "").strip()


class CommunityClient:
    """社区客户端。`transport` 供测试注入 ASGI 直连；生产默认真实 HTTP。"""

    def __init__(
        self,
        config_path: Path,
        host: PluginHost,
        *,
        resolve_dir: Callable[[str], Path | None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._host = host
        self._resolve_dir = resolve_dir
        self._transport = transport

    # ---------------------------------------------------------------- 配置

    def config(self) -> dict[str, str]:
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"server_url": "", "token": ""}
        if not isinstance(data, dict):
            return {"server_url": "", "token": ""}
        return {
            "server_url": str(data.get("server_url", "")),
            "token": str(data.get("token", "")),
        }

    def configure(self, *, server_url: str | None = None, token: str | None = None) -> tuple[bool, str]:
        """None=保持不变；传值=设置；传空串=清除。"""
        current = self.config()
        merged = dict(current)
        if server_url is not None:
            server_url = server_url.strip()
            if server_url and not (server_url.startswith("http://") or server_url.startswith("https://")):
                return False, "错误：server_url 必须以 http:// 或 https:// 开头"
            merged["server_url"] = server_url
        if token is not None:
            merged["token"] = token.strip()
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        masked = "已配置" if merged["token"] else "未配置"
        return True, f"社区配置已保存：server={merged['server_url'] or '（未设置）'}，token {masked}"

    def effective_server_url(self) -> str:
        """生效源：显式配置优先，其次内置默认源（环境变量）。"""
        return self.config()["server_url"] or default_server_url()

    def _http(self) -> httpx.Client:
        server_url = self.effective_server_url()
        return httpx.Client(
            base_url=server_url.rstrip("/") + "/",
            timeout=HTTP_TIMEOUT_S,
            transport=self._transport,
            follow_redirects=True,
        )

    def _require_server(self) -> str:
        if self.effective_server_url():
            return ""
        return (
            "错误：社区服务器未配置。先用 community_config(server_url=…) 设置"
            f"（格式 http(s)://host:port），或设置环境变量 {SERVER_ENV}"
        )

    @staticmethod
    def _fail(resp: httpx.Response) -> str:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        return f"错误：社区服务器返回 {resp.status_code}（{detail}）"

    # ---------------------------------------------------------------- 结构化读取

    def fetch(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """GET 一个 JSON；返回 (数据, 错误文案)。失败时数据为 None。

        这是索引/详情读取的唯一出口：调用方（工具文案、工作台接口）都不自己发请求。
        """
        problem = self._require_server()
        if problem:
            return None, problem
        try:
            with self._http() as client:
                resp = client.get(path, params=params or {})
        except httpx.HTTPError as exc:
            return None, f"错误：连接社区服务器失败（{exc}）"
        if resp.status_code != 200:
            return None, self._fail(resp)
        try:
            data = resp.json()
        except ValueError:
            return None, "错误：社区服务器返回的不是 JSON"
        if not isinstance(data, dict):
            return None, "错误：社区服务器返回的数据结构不对（期望对象）"
        return data, ""

    def plugin_list(self, query: str = "", limit: int = MAX_INDEX_LIMIT) -> tuple[dict[str, Any] | None, str]:
        return self.fetch("/api/plugins", {"query": query, "limit": limit})

    def plugin_detail(self, plugin_id: str) -> tuple[dict[str, Any] | None, str]:
        plugin_id = (plugin_id or "").strip()
        if not plugin_id or "/" in plugin_id:
            return None, "错误：插件 id 不能为空或含斜杠"
        return self.fetch(f"/api/plugins/{plugin_id}")

    def persona_list(self, query: str = "", limit: int = MAX_INDEX_LIMIT) -> tuple[dict[str, Any] | None, str]:
        return self.fetch("/api/personas", {"query": query, "limit": limit})

    def persona_detail(self, persona_id: str) -> tuple[dict[str, Any] | None, str]:
        persona_id = (persona_id or "").strip()
        if not persona_id or "/" in persona_id:
            return None, "错误：人设 id 不能为空或含斜杠"
        return self.fetch(f"/api/personas/{persona_id}")

    # ---------------------------------------------------------------- 查询与安装

    def search(self, query: str, limit: int = 10) -> tuple[bool, str]:
        data, problem = self.plugin_list(query, limit)
        if data is None:
            return False, problem
        items = data.get("items", [])
        if not items:
            return True, f"社区里没有匹配「{query}」的插件（共索引 {data.get('total', 0)} 个）。"
        lines = [f"社区插件（匹配「{query}」，{data.get('total', 0)} 个中前 {len(items)} 个）："]
        for item in items:
            lines.append(
                f"- {item.get('id')} v{item.get('latest_version')}：{item.get('description')}"
                f"（作者 {item.get('author') or '未知'}，下载 {item.get('downloads', 0)} 次）"
            )
        lines.append("详情 community_info(id=…)；安装 community_install(id=…)（下载后仍需人工确认激活）")
        return True, "\n".join(lines)

    def info(self, plugin_id: str) -> tuple[bool, str]:
        item, problem = self.plugin_detail(plugin_id)
        if item is None:
            return False, problem
        lines = [
            f"{item.get('id')} v{item.get('latest_version')}：{item.get('description')}",
            f"作者：{item.get('author') or '未知'}　主页：{item.get('homepage') or '无'}",
            f"版本：{'、'.join(item.get('versions', []))}　下载 {item.get('downloads', 0)} 次",
            f"最新包：{item.get('latest_size', 0)} B，sha256 {str(item.get('latest_sha256', ''))[:16]}",
        ]
        lines.append("安装 community_install(id=…)；下载后仍要走隔离安装与人工确认激活")
        return True, "\n".join(lines)

    def install(self, plugin_id: str, version: str = "") -> tuple[bool, str]:
        """下载 zip → 校验 sha256 → 落 inbox → 走既有隔离安装链。"""
        problem = self._require_server()
        if problem:
            return False, problem
        plugin_id = (plugin_id or "").strip()
        version = (version or "").strip()
        try:
            with self._http() as client:
                resp = client.get(f"/api/plugins/{plugin_id}")
                if resp.status_code != 200:
                    return False, self._fail(resp)
                item = resp.json()
                if not version:
                    version = str(item.get("latest_version", ""))
                resp = client.get(f"/api/download/{plugin_id}", params={"version": version})
        except httpx.HTTPError as exc:
            return False, f"错误：下载失败（{exc}）"
        if resp.status_code != 200:
            return False, self._fail(resp)
        data = resp.content
        if not data:
            return False, "错误：下载内容为空"
        if len(data) > MAX_ZIP_BYTES:
            return False, f"错误：下载包超过 {MAX_ZIP_BYTES // (1024 * 1024)} MiB 安装上限，拒绝落盘"
        expected = str(resp.headers.get("X-Plugin-Sha256", "")).strip().lower()
        actual = _digest(data)
        if expected and actual != expected:
            return False, (
                f"错误：下载内容与服务器指纹不符（期望 {expected[:16]}，实际 {actual[:16]}），已丢弃，不入收件箱"
            )
        filename = f"{plugin_id}-{version}.zip"
        target = self._host.inbox_dir / filename
        if target.exists():
            return False, (f"错误：inbox 里已有 {filename}（先处理掉再下载：装它或删掉）。同一版本重复下载没有意义")
        target.write_bytes(data)
        ok, msg = self._host.install_package(filename)
        if not ok:
            target.unlink(missing_ok=True)  # 安装被拒：不留垃圾在收件箱
            return False, msg
        target.unlink(missing_ok=True)  # 已入隔离区（含内容+导入记录）：收件箱只做传输落点
        return True, msg + f"\n（来源：社区 {self.effective_server_url()}，sha256 {actual[:16]}）"

    # ---------------------------------------------------------------- 发布

    def publish_from_directory(self, rel_path: str) -> tuple[bool, str]:
        """把工作区插件目录打包发布到社区（发布走 CONFIRM——用户确认即同意公开）。"""
        problem = self._require_server()
        if problem:
            return False, problem
        if self._resolve_dir is None:
            return False, "错误：本入口未接线工作区目录解析（嵌入式用法）"
        source_dir = self._resolve_dir(str(rel_path or "").strip())
        if source_dir is None or not source_dir.is_dir():
            return False, f"错误：工作区里没有目录 {rel_path or '（空）'}"
        try:
            data = self._zip_directory(source_dir)
        except ValueError as exc:
            return False, f"错误：{exc}"
        return self._publish(data, f"{rel_path}")

    def publish_zip(self, package: str) -> tuple[bool, str]:
        """把 inbox 里的 zip 原样发布到社区。"""
        problem = self._require_server()
        if problem:
            return False, problem
        package = (package or "").strip()
        if not package or package != Path(package).name:
            return False, "错误：package 必须是 data/plugins/.inbox/ 里的 zip 文件名，不带路径"
        path = self._host.inbox_dir / package
        if not path.is_file():
            return False, f"错误：inbox 里没有 {package}"
        try:
            data = path.read_bytes()
        except OSError as exc:
            return False, f"错误：读取 {package} 失败（{exc}）"
        if len(data) > MAX_PUBLISH_BYTES:
            return False, f"错误：包超过 {MAX_PUBLISH_BYTES // (1024 * 1024)} MiB 发布上限"
        return self._publish(data, package)

    def _zip_directory(self, source_dir: Path) -> bytes:
        files: dict[str, bytes] = {}
        total = 0
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(source_dir).as_posix()
            if rel.split("/")[0] == "__pycache__":
                continue  # 字节码缓存不属于包内容
            if len(files) >= MAX_PUBLISH_ENTRIES:
                raise ValueError(f"目录条目超过 {MAX_PUBLISH_ENTRIES} 个上限")
            data = path.read_bytes()
            total += len(data)
            if total > MAX_PUBLISH_BYTES:
                raise ValueError(f"内容总量超过 {MAX_PUBLISH_BYTES // (1024 * 1024)} MiB 上限")
            files[rel] = data
        if not files:
            raise ValueError("目录是空的")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for rel, data in sorted(files.items()):
                zf.writestr(rel, data)
        return buffer.getvalue()

    def _publish(self, data: bytes, source: str) -> tuple[bool, str]:
        problem = self._require_server()
        if problem:
            return False, problem
        token = self.config()["token"]
        try:
            with self._http() as client:
                resp = client.post(
                    "/api/publish",
                    files={"file": ("plugin.zip", data, "application/zip")},
                    data={"token": token},
                )
        except httpx.HTTPError as exc:
            return False, f"错误：上传失败（{exc}）"
        if resp.status_code != 200:
            return False, self._fail(resp)
        record = resp.json()
        return True, (
            f"已发布到社区：{record.get('id')} v{record.get('version')}"
            f"（{record.get('size', len(data))} B，sha256 {str(record.get('sha256', ''))[:16]}，"
            f"来源 {source}）。其它机器即可 community_install 下载"
        )
