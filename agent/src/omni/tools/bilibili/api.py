"""B站读 API 封装：只读端点 + 目标归一化 + WBI 签名。

职责边界（docs/v/reference/ARCHITECTURE.md §5.1）：本文件是纯 HTTP 层，不知道 LLM 的存在；
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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

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
            raise BiliAPIError(f"无法识别的视频标识：{target}（支持 BV号 / av号 / 视频链接 / b23.tv 短链）")
        if len(self._resolve_cache) < 128:
            self._resolve_cache[target] = resolved
        return resolved

    # ---------- 读端点 ----------

    def account(self) -> dict[str, Any]:
        """当前登录账号（nav 接口）：未登录时 isLogin=False，不算错误。"""
        data = self._get_json("/x/web-interface/nav", params={})
        level = (data.get("level_info") or {}).get("current_level") or 0
        return {
            "logged_in": bool(data.get("isLogin")),
            "uid": str(data.get("mid") or ""),
            "name": str(data.get("uname") or ""),
            "level": int(level or 0),
            "coins": int(data.get("money") or 0),
            "vip": bool(data.get("vipStatus")),
            "face": str(data.get("face") or ""),
        }

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

    def video_comments(self, target: str, count: int = 10, *, sort: str = "hot", page: int = 1) -> list[dict[str, Any]]:
        """视频评论（sort=hot 热度 / time 最新；page 从 1 起）。

        先走新版 /x/v2/reply/main（带 WBI 签名，能拿到 rpid 与楼中楼数），
        失败再退回旧接口 /x/v2/reply——旧接口偶尔被风控，但能出的数据仍然有用。
        """
        aid = self._aid_of(target)
        limit = min(max(int(count), 1), 20)
        try:
            data = self._get_json(
                "/x/v2/reply/main",
                params=self._wbi_params(
                    {
                        "type": 1,
                        "oid": aid,
                        "mode": 3 if sort != "time" else 2,
                        "plat": 1,
                        "web_location": 1315875,
                        "pagination_str": json.dumps({"offset": ""}),
                    }
                ),
            )
            replies = data.get("replies") or []
            if replies:
                return [self._comment_row(reply) for reply in replies[:limit]]
        except Exception:  # noqa: BLE001, S110 —— 新接口不可用就退旧接口，错误由旧接口自己报
            pass
        data = self._get_json(
            "/x/v2/reply",
            params={
                "type": 1,
                "oid": aid,
                "sort": 2 if sort != "time" else 0,
                "ps": limit,
                "pn": max(1, int(page)),
            },
        )
        return [self._comment_row(reply) for reply in (data.get("replies") or [])[:limit]]

    def comment_replies(self, target: str, rpid: str, count: int = 10) -> list[dict[str, Any]]:
        """某条评论下的回复（楼中楼）。rpid 来自 bilibili_video_comments 的输出。"""
        aid = self._aid_of(target)
        if not str(rpid).strip().isdigit():
            raise BiliAPIError(f"评论 ID 不是数字：{rpid}")
        limit = min(max(int(count), 1), 20)
        data = self._get_json(
            "/x/v2/reply/reply",
            params={"type": 1, "oid": aid, "root": int(str(rpid).strip()), "ps": limit, "pn": 1},
        )
        return [self._comment_row(reply) for reply in (data.get("replies") or [])[:limit]]

    def user_search(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """按昵称/关键词找用户（拿到 UID 与昵称，供艾特使用）。"""
        keyword = keyword.strip()
        if not keyword:
            raise BiliAPIError("搜索关键词不能为空")
        data = self._get_json(
            "/x/web-interface/wbi/search/type",
            params=self._wbi_params(
                {
                    "search_type": "bili_user",
                    "keyword": keyword,
                    "page": 1,
                }
            ),
        )
        users = data.get("result") or []
        rows = []
        for user in users[: max(1, int(limit))]:
            rows.append(
                {
                    "uid": str(user.get("mid") or ""),
                    "name": str(user.get("uname") or ""),
                    "sign": str(user.get("usign") or ""),
                }
            )
        return rows

    @staticmethod
    def _comment_row(reply: dict[str, Any]) -> dict[str, Any]:
        """新旧接口的评论结构对齐成同一形状。"""
        member = reply.get("member") or {}
        reply_control = reply.get("reply_control") or {}
        return {
            "rpid": str(reply.get("rpid") or ""),
            "user": str(member.get("uname") or ""),
            "uid": str(member.get("mid") or ""),
            "like": int(reply.get("like") or 0),
            "content": str((reply.get("content") or {}).get("message") or ""),
            "time": int(reply.get("ctime") or 0),
            "reply_count": int(reply_control.get("reply_count") or reply.get("rcount") or 0),
            "to_user": str(((reply.get("reply_control") or {}).get("reply_who") or {}).get("uname") or ""),
        }

    def video_subtitle(self, target: str) -> str:
        """字幕全文；空串 = 没有可获取字幕（未登录时 AI 字幕通常不可见）。"""
        info = self.video_info(target)
        cid = info.get("cid")
        bvid = info.get("bvid")
        if not cid or not bvid:
            return ""
        data = self._get_json("/x/player/wbi/v2", params=self._wbi_params({"bvid": bvid, "cid": cid}))
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
        return "\n".join(str(line.get("content", "")) for line in body.get("body") or []).strip()

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
                "该视频没有可用的音频/视频流（可能需要登录：把浏览器 Cookie 配到 data/bili_cookies.json 后重试）"
            )
        return url

    def video_stream(self, target: str, *, quality: int = 32) -> str:
        """可解码的视频直链（先试 MP4 合并流，退回 DASH 视频流）。

        合并流（durl）解码最省事；DASH 是音视频分开的分片流，
        抽帧时能解但体积小，优先低码率那一路。
        """
        info = self.video_info(target)
        bvid, cid = info.get("bvid"), info.get("cid")
        if not bvid or not cid:
            raise BiliAPIError(f"拿不到视频信息：{target}")
        params = {"bvid": bvid, "cid": cid, "qn": int(quality), "fnval": 0, "fnver": 0, "fourk": 0}
        data = self._get_json("/x/player/playurl", params=params)
        durl = data.get("durl") or []
        if durl:
            url = str((durl[0] or {}).get("url") or "")
            if url:
                return url
        # 没有合并流就退回 DASH：取码率最低的视频流（抽帧够用，下载也快）
        data = self._get_json(
            "/x/player/playurl",
            params={"bvid": bvid, "cid": cid, "qn": int(quality), "fnval": 16, "fnver": 0, "fourk": 0},
        )
        videos = (data.get("dash") or {}).get("video") or []
        if not videos:
            raise BiliAPIError(
                "该视频没有可用的视频流（可能需要登录：把浏览器 Cookie 配到 data/bili_cookies.json 后重试）"
            )
        best = min(videos, key=lambda item: int(item.get("bandwidth") or 10**9))
        url = str(best.get("baseUrl") or best.get("base_url") or "")
        if not url:
            backups = best.get("backupUrl") or best.get("backup_url") or []
            url = str(backups[0]) if backups else ""
        if not url:
            raise BiliAPIError("拿不到视频流地址")
        return url

    def download_binary(
        self,
        url: str,
        dest: Path,
        max_bytes: int = 300 * 1024 * 1024,
        *,
        attempts: int = 3,
    ) -> None:
        """流式下载二进制到 dest；m4s 直链要求带 Referer（client 已全局配置）。

        **带断点续传与重试**（真机踩到的坑）：B站 CDN 下载 55MB 视频时实测中断过
        一次（收到 10MB 就断连，`peer closed connection`）。原实现是"一次流式读完"，
        中断就抛错——但**残缺文件已经写了一半留在磁盘上**，而
        `probe_video` 只读容器元信息，残缺文件照样能报出正确的时长，
        看起来"下载成功了"，直到解码时才崩。

        CDN 实测支持 Range（返回 206 + content-range），所以：
        - 断了就带着 `Range: bytes=<已收>-` 重连，接着收；
        - 服务端不支持 Range（返回 200 而不是 206）就从头重来；
        - 每次重试都重新校验累计大小，超过上限立刻放弃。

        `attempts` 是总尝试次数（含第一次）。
        """
        last_error: Exception | None = None
        for attempt in range(1, max(1, attempts) + 1):
            try:
                self._download_once(url, dest, max_bytes)
                return
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= max(1, attempts):
                    break
                # 续传前先看看磁盘上已有多少字节（有则接着收）
                have = dest.stat().st_size if dest.exists() else 0
                if have > max_bytes:
                    break
        raise BiliAPIError(f"下载失败: {last_error}")

    def _download_once(self, url: str, dest: Path, max_bytes: int) -> None:
        """下载一次（支持从已有字节处续传）。"""
        have = dest.stat().st_size if dest.exists() else 0
        headers: dict[str, str] = {}
        if have > 0:
            headers["Range"] = f"bytes={have}-"
        with self._client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            # 206 = 服务端认可续传；200 = 从头给（之前的部分作废，重写）
            resumed = resp.status_code == 206
            if not resumed and have:
                have = 0
                dest.unlink(missing_ok=True)
            total = int(resp.headers.get("content-length") or 0)
            if total and have + total > max_bytes:
                raise BiliAPIError(f"文件过大（{(have + total) // 1048576}MB > {max_bytes // 1048576}MB），放弃下载")
            with dest.open("ab" if resumed else "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
                    have += len(chunk)
                    if have > max_bytes:
                        raise BiliAPIError(f"文件过大（超过 {max_bytes // 1048576}MB），放弃下载")
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
            raise BiliAPIError(f"B站接口 {path} 返回错误 code={code}: {payload.get('message', '')}{hint}")
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
