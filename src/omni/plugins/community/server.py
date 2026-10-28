"""插件社区参考服务器（FastAPI + 文件存储，单 owner 部署）。

定位：**索引与分发**，不是商店、不是安全背书——激活前的人工审阅仍在
每台机器上发生（见 docs/PLUGIN_SYSTEM.md §5）。

部署（项目所有者提供服务器后）：

    uv run python -m omni.plugins.server --root /srv/omni-community \
        --token "$COMMUNITY_TOKEN" --host 0.0.0.0 --port 8080

存储布局（全部文件，无数据库）：

    <root>/index.json                    # 插件索引（搜索/详情从这里读）
    <root>/packages/<id>/<version>.zip   # 包本体
    <root>/packages/<id>/meta.json       # 各版本记录

API（v1）：
    GET  /api/health
    GET  /api/plugins?query=&limit=&offset=     → {"total", "items": [索引项]}
    GET  /api/plugins/{id}                       → 详情（含版本列表）
    GET  /api/download/{id}?version=             → zip 字节（sha256 与索引一致）
    POST /api/publish （multipart: file=zip, token）→ 登记新版本

纪律：
- 发布需要 token（服务器配置；hmac.compare_digest 恒时比较）；
- 发布时只做**纯数据校验**（zip 里必须有合法 manifest.json；入口按声明
  必须在包内）——深度校验（类型白名单/上限/防篡改）仍在每台机器安装时做；
- 版本不可变：同一 id + version 重复发布拒绝（409），升级只能加新版本号；
- 服务器对 zip 内容量、发布频率不做复杂治理——单 owner 参考实现，
  面向公网运营前应按实际规模加反代限流。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import io
import json
import threading
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile

from ...system.atomic import atomic_write_text
from ...system.filelock import FileLock, LockTimeout
from ..manifest import MANIFEST_NAME, PluginManifest

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 发布上限（收件侧安装仍按 4 MiB 收紧）


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def create_app(root: Path, *, token: str = "") -> FastAPI:
    """构造社区服务应用；root 是存储目录，token 为空表示允许匿名发布（不推荐）。"""
    expected_token = token
    root = Path(root)
    (root / "packages").mkdir(parents=True, exist_ok=True)
    index_path = root / "index.json"
    write_lock = threading.Lock()

    app = FastAPI(title="OmniOperator Plugin Community", version="1")

    def _index() -> dict[str, Any]:
        return _read_json(index_path, {"plugins": {}})

    def _save_index(index: dict[str, Any]) -> None:
        atomic_write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2))

    def _load_manifest_from_zip(data: bytes) -> PluginManifest | str:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
                candidates = [n for n in names if n in (MANIFEST_NAME,) or n.endswith("/" + MANIFEST_NAME)]
                if not candidates:
                    return "zip 里没有 manifest.json（应在顶层）"
                raw = zf.read(candidates[0])
                if len(raw) > 16 * 1024:
                    return "manifest.json 超过 16 KiB 上限"
                manifest = PluginManifest.from_dict(json.loads(raw.decode("utf-8")))
        except (zipfile.BadZipFile, OSError, UnicodeError, json.JSONDecodeError) as exc:
            return f"zip 或 manifest 解析失败（{exc}）"
        if isinstance(manifest, str):
            return manifest
        if manifest.entry not in names and f"{manifest.id}/{manifest.entry}" not in names:
            return f"manifest 声明的入口 {manifest.entry} 不在包内"
        return manifest

    def _item(index: dict[str, Any], plugin_id: str) -> dict[str, Any] | None:
        return index.get("plugins", {}).get(plugin_id)

    def _version_record(plugin_id: str, version: str) -> dict[str, Any]:
        meta = _read_json(root / "packages" / plugin_id / "meta.json", {"versions": {}})
        return (meta.get("versions") or {}).get(version) or {}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "plugins": len(_index().get("plugins", {}))}

    @app.get("/api/plugins")
    def search_plugins(
        query: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        index = _index()
        items = []
        needle = query.strip().lower()
        for plugin in sorted(index.get("plugins", {}).values(), key=lambda p: str(p.get("id", ""))):
            haystack = " ".join(
                str(plugin.get(key, "")) for key in ("id", "name", "description", "author")
            ).lower()
            if needle and needle not in haystack:
                continue
            items.append(plugin)
        total = len(items)
        return {"total": total, "items": items[offset : offset + limit]}

    @app.get("/api/plugins/{plugin_id}")
    def plugin_detail(plugin_id: str) -> dict[str, Any]:
        item = _item(_index(), plugin_id)
        if item is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        record = _version_record(plugin_id, str(item.get("latest_version", "")))
        item = dict(item)
        item["latest_sha256"] = str(record.get("sha256", ""))
        item["latest_size"] = int(record.get("size", 0))
        return item

    @app.get("/api/download/{plugin_id}")
    def download(plugin_id: str, version: str = Query(default="")) -> Response:
        index = _index()
        item = _item(index, plugin_id)
        if item is None:
            raise HTTPException(status_code=404, detail="插件不存在")
        if not version:
            version = str(item.get("latest_version", ""))
        path = root / "packages" / plugin_id / f"{version}.zip"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"版本 {version} 不存在")
        data = path.read_bytes()
        record = _version_record(plugin_id, version)
        if record and str(record.get("sha256", "")) and record["sha256"] != _digest(data):
            # 存储被改动过：拒绝分发，避免索引与实际内容不符
            raise HTTPException(status_code=503, detail="包内容与索引指纹不符，已暂停分发")
        with write_lock:
            item["downloads"] = int(item.get("downloads", 0)) + 1
            _save_index(index)
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{plugin_id}-{version}.zip"',
                "X-Plugin-Sha256": str(record.get("sha256", "")) if record else "",
            },
        )

    @app.post("/api/publish")
    async def publish(file: UploadFile = File(...), token: str = Form(default="")) -> dict[str, Any]:
        if expected_token and not hmac.compare_digest(token, expected_token):
            raise HTTPException(status_code=401, detail="发布 token 不正确")
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="上传内容为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="包超过 8 MiB 上限")
        manifest = _load_manifest_from_zip(data)
        if isinstance(manifest, str):
            raise HTTPException(status_code=400, detail=manifest)
        pkg_dir = root / "packages" / manifest.id
        pkg_dir.mkdir(parents=True, exist_ok=True)
        zip_path = pkg_dir / f"{manifest.version}.zip"
        lock = FileLock(pkg_dir / "publish.lock", timeout_s=10, owner="community-server")
        try:
            lock.acquire()
        except LockTimeout as exc:
            raise HTTPException(status_code=503, detail=f"发布冲突，稍后再试（{exc}）") from exc
        try:
            if zip_path.exists():
                raise HTTPException(
                    status_code=409,
                    detail=f"版本 {manifest.version} 已存在；版本不可变，升级请加新版本号",
                )
            zip_path.write_bytes(data)
            digest = _digest(data)
            meta_path = pkg_dir / "meta.json"
            meta = _read_json(meta_path, {"versions": {}})
            meta.setdefault("versions", {})[manifest.version] = {
                "version": manifest.version,
                "sha256": digest,
                "size": len(data),
                "published_at": _now(),
            }
            atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
            with write_lock:
                index = _index()
                plugins = index.setdefault("plugins", {})
                versions = sorted(
                    meta["versions"].keys(), key=lambda v: tuple(int(x) for x in v.split("."))
                )
                existing = plugins.get(manifest.id) or {"downloads": 0}
                plugins[manifest.id] = {
                    "id": manifest.id,
                    "name": manifest.name,
                    "description": manifest.description,
                    "author": manifest.author,
                    "homepage": manifest.homepage,
                    "latest_version": versions[-1],
                    "versions": versions,
                    "downloads": int(existing.get("downloads", 0)),
                    "updated_at": _now(),
                }
                _save_index(index)
        finally:
            lock.release()
        return {
            "id": manifest.id,
            "version": manifest.version,
            "sha256": digest,
            "size": len(data),
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniOperator 插件社区参考服务器")
    parser.add_argument("--root", required=True, help="存储目录（index.json 与 packages/）")
    parser.add_argument("--token", default="", help="发布 token；空 = 允许匿名发布（不推荐）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    import uvicorn

    app = create_app(Path(args.root), token=args.token)
    print(
        f"[插件社区] root={args.root} host={args.host}:{args.port} "
        f"token={'已启用' if args.token else '未启用（匿名可发布）'}",
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
