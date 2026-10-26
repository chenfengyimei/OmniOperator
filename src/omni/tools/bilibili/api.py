"""B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。

职责边界（docs/ARCHITECTURE.md §5.1）：本文件是纯 HTTP 层，不知道 LLM 的存在；
工具包装在 tools.py。写端点（评论/私信/发布）P3 走浏览器 UI，不进本层。

端点表来自 xuexi/dsh-bili-whale（见 xuexi/NOTES.md §4）。
未登录（无 Cookie）时：视频信息/评论可读；AI 字幕通常不可见，字幕接口
返回空列表属正常情况，由上层给出可读提示。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from ...config import BILI_COOKIES_PATH
from .wbi import sign_wbi

API_BASE = "https://api.bilibili.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"\bav(\d+)\b", re.IGNORECASE)


class BiliAPIError(RuntimeError):
    """B站接口调用失败（网络 / 风控 / 参数错误）。"""


class BiliAPI:
    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        cookies_path: Path | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._apply_cookies(cookies_path or BILI_COOKIES_PATH)
        self._resolve_cache: dict[str, dict[str, Any]] = {}
        self._wbi_keys: tuple[str, str] | None = None

    def _apply_cookies(self, cookies_path: Path) -> None:
        """从 data/bili_cookies.json 读浏览器 Cookie（SESSDATA/bili_jct/buvid3…）。

        匿名访问时 WBI 端点（字幕等）可能返回 -101；把登录浏览器的
        Cookie 粘到这里即可。文件不存在则静默跳过（保持匿名模式）。
        """
        try:
            raw = json.loads(cookies_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            self._client.cookies.update({str(k): str(v) for k, v in raw.items() if v})

    # ---------- 目标归一化 ----------

    def resolve(self, target: str) -> dict[str, Any]:
        """BV号 / av号 / 完整链接 / b23.tv 短链 → {"bvid":...} 或 {"aid":...}。

        短链会发一次 HTTP 跟随跳转；结果按原字符串缓存（一轮对话里
        info/comments/subtitle 连续调用只解析一次）。
        """
        target = target.strip()
        cached = self._resolve_cache.get(target)
        if cached:
            return cached
        bv = _BV_RE.search(target)
        if bv:
            resolved: dict[str, Any] = {"bvid": bv.group(0)}
        elif not target.startswith("http") and (av := _AV_RE.search(target)):
            resolved = {"aid": int(av.group(1))}
        elif target.startswith("http") or "b23.tv" in target or "bilibili.com" in target:
            url = target if target.startswith("http") else "https://" + target.lstrip("/")
            final = str(self._get(url).url)
            bv = _BV_RE.search(final)
            if bv:
                resolved = {"bvid": bv.group(0)}
            elif av := _AV_RE.search(final):
                resolved = {"aid": int(av.group(1))}
            else:
                raise BiliAPIError(f"链接里找不到 BV/av 号：{target}")
        else:
            raise BiliAPIError(
                f"无法识别的视频标识：{target}（支持 BV号 / av号 / 视频链接 / b23.tv 短链）"
            )
        if len(self._resolve_cache) < 128:
            self._resolve_cache[target] = resolved
        return resolved

    # ---------- 读端点 ----------

    def video_info(self, target: str) -> dict[str, Any]:
        """视频元信息（标题/UP主/时长/数据/简介/aid/cid）。"""
        data = self._get_json("/x/web-interface/view", params=self.resolve(target))
        cid = data.get("cid") or (data.get("pages") or [{}])[0].get("cid")
        stat = data.get("stat") or {}
        return {
            "bvid": data.get("bvid", ""),
            "aid": data.get("aid"),
            "cid": cid,
            "title": data.get("title", ""),
            "desc": data.get("desc", ""),
            "owner": (data.get("owner") or {}).get("name", ""),
            "duration_s": data.get("duration"),
            "pubdate": data.get("pubdate"),
            "stat": stat,
        }

    def video_comments(self, target: str, count: int = 10) -> list[dict[str, Any]]:
        """热门评论（按热度）。count 上限 20。"""
        aid = self._aid_of(target)
        data = self._get_json(
            "/x/v2/reply",
            params={"type": 1, "oid": aid, "sort": 2, "ps": min(max(count, 1), 20), "pn": 1},
        )
        out: list[dict[str, Any]] = []
        for reply in (data.get("replies") or [])[:count]:
            out.append(
                {
                    "user": (reply.get("member") or {}).get("uname", ""),
                    "like": reply.get("like", 0),
                    "content": (reply.get("content") or {}).get("message", ""),
                }
            )
        return out

    def video_subtitle(self, target: str) -> str:
        """字幕全文；空串 = 没有可获取字幕（未登录时 AI 字幕通常不可见）。"""
        info = self.video_info(target)
        cid = info.get("cid")
        bvid = info.get("bvid")
        if not cid or not bvid:
            return ""
        data = self._get_json(
            "/x/player/wbi/v2", params=self._wbi_params({"bvid": bvid, "cid": cid})
        )
        subs = (data.get("subtitle") or {}).get("subtitles") or []
        picked = next(
            (s for s in subs if str(s.get("lan", "")).startswith("zh")),
            subs[0] if subs else None,
        )
        if not picked:
            return ""
        url = str(picked.get("subtitle_url", ""))
        if url.startswith("//"):
            url = "https:" + url
        body = self._get(url).json()
        return "\n".join(
            str(line.get("content", "")) for line in body.get("body") or []
        ).strip()

    def audio_stream(self, target: str) -> str:
        """音频直链（DASH 最低码率，ASR 够用）；拿不到抛 BiliAPIError。"""
        info = self.video_info(target)
        bvid, cid = info.get("bvid"), info.get("cid")
        if not bvid or not cid:
            raise BiliAPIError(f"拿不到视频信息：{target}")
        # playurl 走普通端点（无需 WBI 签名，避免依赖 nav）；未登录时
        # 部分视频只回 durl（FLV/MP4 音画合流），同样可用于转写
        params = {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 16, "fnver": 0, "fourk": 0}
        data = self._get_json("/x/player/playurl", params=params)
        audio_list = (data.get("dash") or {}).get("audio") or []
        if audio_list:
            best = min(audio_list, key=lambda a: int(a.get("bandwidth") or 10**9))
            url = str(best.get("baseUrl") or best.get("base_url") or "")
            if not url:
                backups = best.get("backupUrl") or best.get("backup_url") or []
                url = str(backups[0]) if backups else ""
        else:
            durl = data.get("durl") or []
            url = str((durl[0] or {}).get("url") or "") if durl else ""
        if not url:
            raise BiliAPIError(
                "该视频没有可用的音频/视频流（可能需要登录：把浏览器 Cookie "
                "配到 data/bili_cookies.json 后重试）"
            )
        return url

    def download_binary(
        self, url: str, dest: Path, max_bytes: int = 300 * 1024 * 1024
    ) -> None:
        """流式下载二进制到 dest；m4s 直链要求带 Referer（client 已全局配置）。"""
        try:
            with self._client.stream("GET", url) as resp:
                resp.raise_for_status()
                size = int(resp.headers.get("content-length") or 0)
                if size > max_bytes:
                    raise BiliAPIError(
                        f"文件过大（{size // 1048576}MB > {max_bytes // 1048576}MB），放弃下载"
                    )
                with dest.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
        except httpx.HTTPError as exc:
            raise BiliAPIError(f"下载失败: {exc}") from exc
        if not dest.exists() or dest.stat().st_size == 0:
            raise BiliAPIError("下载结果为空文件")

    # ---------- 内部 ----------

    def _aid_of(self, target: str) -> int:
        resolved = self.resolve(target)
        if resolved.get("aid"):
            return int(resolved["aid"])
        aid = self.video_info(target).get("aid")  # resolve 缓存命中，只补一次 view
        if not aid:
            raise BiliAPIError(f"拿不到视频 aid：{target}")
        resolved["aid"] = int(aid)
        return int(aid)

    def _wbi_params(self, params: dict[str, Any]) -> dict[str, Any]:
        img_key, sub_key = self._get_wbi_keys()
        return sign_wbi(params, img_key, sub_key)

    def _get_wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys is None:
            nav = self._get_json("/x/web-interface/nav", params={})
            wbi = nav.get("wbi_img") or {}
            img_key = str(wbi.get("img_url", "")).rsplit("/", 1)[-1].split(".")[0]
            sub_key = str(wbi.get("sub_url", "")).rsplit("/", 1)[-1].split(".")[0]
            if not img_key or not sub_key:
                raise BiliAPIError("nav 接口没有返回 wbi keys（可能被风控）")
            self._wbi_keys = (img_key, sub_key)
        return self._wbi_keys

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        payload = self._get(path, params=params).json()
        code = payload.get("code", -1)
        if code != 0:
            hint = "（需要登录态：把浏览器 Cookie 配到 data/bili_cookies.json）" if code == -101 else ""
            raise BiliAPIError(
                f"B站接口 {path} 返回错误 code={code}: {payload.get('message', '')}{hint}"
            )
        return payload.get("data")

    def _get(self, path_or_url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        url = path_or_url if path_or_url.startswith("http") else API_BASE + path_or_url
        try:
            resp = self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise BiliAPIError(f"请求 {url} 失败: {exc}") from exc
        return resp

    def close(self) -> None:
        self._client.close()
