"""数字具身总览与B站账号状态：只读聚合，不代替各能力域自己的操作接口。

总览接口只看本地状态（配置、凭证文件、adb 是否在 PATH），不联网、不发消息；
账号状态是这里唯一会外呼的接口（B站 nav），查不到就如实说查不到，
绝不缓存成"已登录"——这个面板是给人判断"现在能不能用"的，不能骗人。
"""
from __future__ import annotations

import json
import shutil
from typing import Any

from fastapi import APIRouter

BILI_COOKIE_KEYS = ("SESSDATA", "bili_jct", "buvid3")


def _cookie_state(runtime) -> dict[str, Any]:
    """B站凭证状态：只看文件里有没有，不读出内容（不回显任何 Cookie 值）。"""
    keys = {name: False for name in BILI_COOKIE_KEYS}
    try:
        raw = json.loads(runtime.paths.cookies.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for name in BILI_COOKIE_KEYS:
                keys[name] = bool(str(raw.get(name, "")).strip())
    except (OSError, json.JSONDecodeError):
        pass
    return {"configured": any(keys.values()), "keys": keys}


def _embodiments(runtime) -> list[dict[str, Any]]:
    cookies = _cookie_state(runtime)
    write = runtime.config.bilibili_write
    browser = runtime.config.browser
    try:
        qq = runtime.qq_policy()
    except Exception:  # noqa: BLE001 —— 策略读取失败不该让总览整个挂掉
        qq = runtime.config.qq
    adb_found = bool(shutil.which("adb"))
    return [
        {
            "id": "bilibili",
            "label": "哔哩哔哩",
            "section": "sec-media",
            "ready": cookies["configured"],
            "detail": ("登录凭证已就绪" if cookies["configured"]
                       else "未配置登录凭证：只能看公开内容，字幕/音频/发布都拿不到"),
            "capabilities": ["读视频信息与字幕", "语音转写", "抽帧拼图看画面",
                             "读评论与楼中楼", "发布评论与回复", "私信回复", "艾特（文本）"],
            "extras": {
                "write_enabled": bool(write.enabled),
                "write_forms": sorted(write.forms.keys()),
                "has_sessdata": cookies["keys"]["SESSDATA"],
            },
        },
        {
            "id": "browser",
            "label": "浏览器",
            "section": "sec-browser",
            "ready": True,
            "detail": ("后台运行（看不到它点了什么）" if browser.headless
                       else "前台可见（你能亲眼看着它操作）")
            + f"，每天最多 {browser.max_writes_per_day} 次写操作",
            "capabilities": ["打开并读取网页", "截图给模型看", "点击与填表（需确认）",
                             "B站发布走页面 UI"],
            "extras": {"headless": bool(browser.headless),
                       "max_writes_per_day": browser.max_writes_per_day},
        },
        {
            "id": "android",
            "label": "手机",
            "section": "sec-android",
            "ready": adb_found,
            "detail": ("已找到 adb，可以连手机" if adb_found
                       else "没找到 adb：把 Platform-Tools 加进 PATH 后重启工作台"),
            "capabilities": ["看屏幕", "点元素与划滑块", "输入文字", "拉 UI 树看结构"],
            "extras": {"adb_found": adb_found},
        },
        {
            "id": "qq",
            "label": "QQ",
            "section": "sec-qq",
            "ready": bool(qq.enabled),
            "detail": ("通道已启用" if qq.enabled else "通道未启用：扫码登录后才会收发消息")
            + f"，白名单 {len(qq.allowed_users)} 人 / {len(qq.allowed_groups)} 个群",
            "capabilities": ["私聊与群聊回复", "被 @ 才回群", "主动提醒（有额度与安静时段）",
                             "投递 outbox（掉线不丢）"],
            "extras": {"enabled": bool(qq.enabled),
                       "allowed_users": len(qq.allowed_users),
                       "allowed_groups": len(qq.allowed_groups)},
        },
    ]


def create_embodiments_router(runtime) -> APIRouter:
    router = APIRouter(tags=['embodiments'])

    @router.get('/api/system/embodiments')
    def embodiments():
        return {'embodiments': _embodiments(runtime)}

    @router.get('/api/system/bili-account')
    def bili_account():
        """B站当前登录账号：实时查 nav 接口，查不到就如实报错。"""
        from ...tools.bilibili.api import BiliAPI, BiliAPIError

        cookies = _cookie_state(runtime)
        api = BiliAPI(timeout=8.0, cookies_path=runtime.paths.cookies)
        try:
            account = api.account()
        except BiliAPIError as exc:
            return {'checked': True, 'logged_in': False, 'configured': cookies["configured"],
                    'error': str(exc)}
        except Exception as exc:  # noqa: BLE001 —— 网络类异常一律如实报出，不猜
            return {'checked': True, 'logged_in': False, 'configured': cookies["configured"],
                    'error': f'查询失败：{type(exc).__name__}'}
        finally:
            api.close()
        return {'checked': True, 'configured': cookies["configured"],
                'logged_in': bool(account.get('logged_in')), 'account': account}

    return router
