"""固定公开 Gitee 源的有界 HTTP 读取；不接受任意仓库、令牌或执行下载内容。"""

from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import REPOSITORY, SHA, UpdateError, run
from .version import semver

API = "https://gitee.com/api/v5/repos/chenfengloveyuri/OmniOperator"
MAX_ARCHIVE = 128 * 1024 * 1024


class TrustedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if (
            target.scheme != "https"
            or target.hostname not in {"gitee.com", "foruda.gitee.com"}
            or target.port not in {None, 443}
            or target.username
            or target.password
        ):
            raise UpdateError("unsafe_redirect", "更新下载跳转到未受信任地址")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GiteeClient:
    def __init__(self, *, opener=None, execute=run):
        self.opener = opener or urllib.request.build_opener(TrustedRedirect())
        self.execute = execute

    def _read(self, url: str, *, limit: int, destination: Path | None = None):
        request = urllib.request.Request(
            url, headers={"User-Agent": "OmniOperator-Updater/1", "Accept": "application/json"}
        )
        try:
            deadline = time.monotonic() + 180
            with self.opener.open(request, timeout=15) as response:
                size, chunks = 0, []
                stream = destination.open("xb") if destination else None
                try:
                    while block := response.read(65536):
                        if time.monotonic() > deadline:
                            raise UpdateError("download_timeout", "更新下载超过总等待时限")
                        size += len(block)
                        if size > limit:
                            raise UpdateError("download_too_large", "更新响应超过大小上限")
                        if stream:
                            stream.write(block)
                        else:
                            chunks.append(block)
                finally:
                    if stream:
                        stream.close()
                return b"".join(chunks)
        except urllib.error.HTTPError as exc:
            code = (
                "rate_limited"
                if exc.code == 429 or (exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0")
                else "not_found"
                if exc.code == 404
                else "authentication_required"
                if exc.code == 401
                else "http_error"
            )
            raise UpdateError(
                code, "Gitee 请求受限，请稍后重试" if code == "rate_limited" else f"Gitee 返回 HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpdateError("network_error", "无法连接 Gitee，请检查网络后重试") from exc

    def json(self, path: str):
        try:
            return json.loads(self._read(API + path, limit=2 * 1024 * 1024))
        except (ValueError, TypeError) as exc:
            raise UpdateError("invalid_response", "Gitee 返回了无效数据") from exc

    def candidate(self, channel: str) -> dict | None:
        if channel == "development":
            data = self.json("/branches/master")
            commit = data["commit"]["sha"]
            result = {
                "version": "",
                "commit": commit,
                "notes": data["commit"].get("commit", {}).get("message", ""),
                "url": REPOSITORY + "/commit/" + commit,
                "channel": channel,
            }
        else:
            releases = []
            for page in range(1, 11):
                rows = self.json(f"/releases?per_page=100&page={page}&direction=desc")
                if not isinstance(rows, list):
                    raise UpdateError("invalid_response", "Gitee 发行版列表无效")
                for row in rows:
                    try:
                        key = semver(row["tag_name"])
                        if not row.get("prerelease") and key[3]:
                            releases.append((key, row))
                    except (KeyError, UpdateError):
                        continue
                if len(rows) < 100:
                    break
            if not releases:
                return None
            row = max(releases, key=lambda item: item[0])[1]
            tag = urllib.parse.quote(row["tag_name"], safe="")
            commit = self.json("/commits/" + tag)["sha"]
            result = {
                "version": row["tag_name"].removeprefix("v"),
                "commit": commit,
                "notes": str(row.get("body") or "")[:20000],
                "url": REPOSITORY + "/releases/tag/" + tag,
                "channel": channel,
            }
        if not SHA.fullmatch(result["commit"]):
            raise UpdateError("invalid_response", "更新提交不是完整的 SHA")
        return result

    def relation(self, current: str, target: str) -> str:
        if not SHA.fullmatch(current) or not SHA.fullmatch(target):
            return "unknown"
        if current == target:
            return "equal"
        try:
            data = self.json("/compare/" + current + "..." + target)
        except UpdateError as exc:
            if exc.code == "not_found":
                return "unknown"
            raise
        ancestor = data.get("merge_base_commit", {}).get("sha")
        if ancestor == current:
            return "behind"
        if ancestor == target:
            return "ahead"
        return "diverged" if ancestor else "unknown"

    def baseline(self, version: str) -> str | None:
        semver(version)
        for name in ("v" + version, version):
            tag = urllib.parse.quote(name, safe="")
            try:
                release = self.json("/releases/tags/" + tag)
            except UpdateError as exc:
                if exc.code == "not_found":
                    continue
                raise
            if release.get("prerelease"):
                continue
            commit = self.json("/commits/" + tag).get("sha", "")
            if not SHA.fullmatch(commit):
                raise UpdateError("invalid_response", "旧版发行记录没有有效提交")
            return commit
        return None

    def download(self, commit: str, destination: Path) -> None:
        if not SHA.fullmatch(commit):
            raise UpdateError("invalid_commit", "只允许下载固定提交")
        try:
            self._read(API + "/zipball?ref=" + commit, limit=MAX_ARCHIVE, destination=destination)
            with destination.open("rb") as stream:
                if stream.read(4) != b"PK\x03\x04":
                    raise UpdateError("authentication_required", "Gitee 返回登录页面，无法直接下载源码")
        except UpdateError as exc:
            if exc.code != "authentication_required":
                raise
            destination.unlink(missing_ok=True)
            # Gitee 公开归档接口现场也可能要求登录；通过公开 Git 协议获取固定对象。
            # 隔离裸仓库不修改用户 checkout，也不需要在更新配置中保存访问令牌。
            binary = shutil.which("git")
            if not binary:
                raise UpdateError(
                    "git_required", "Gitee 下载要求登录；请安装 Git 后重试，系统会在隔离目录获取源码"
                ) from exc
            repository = destination.parent / ("download-" + commit)
            repository.mkdir(exist_ok=False)
            self.execute([binary, "init", "--bare", str(repository)], cwd=destination.parent)
            prefix = [binary, "-c", "core.hooksPath=", "-c", "protocol.file.allow=never", "-C", str(repository)]
            self.execute(
                prefix + ["fetch", "--no-tags", "--depth=1", REPOSITORY + ".git", commit], cwd=repository, timeout=180
            )
            actual = self.execute(prefix + ["rev-parse", "FETCH_HEAD"], cwd=repository)
            if actual != commit:
                raise UpdateError("invalid_commit", "下载仓库的提交与检查结果不一致")
            self.execute(
                prefix + ["archive", "--format=zip", "--output=" + str(destination), commit], cwd=repository, timeout=60
            )
            if destination.stat().st_size > MAX_ARCHIVE:
                raise UpdateError("download_too_large", "更新源码超过下载上限")
