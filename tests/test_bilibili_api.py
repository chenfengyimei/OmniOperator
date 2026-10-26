"""BiliAPI 单测：全部走 httpx.MockTransport，不发真实网络请求。"""
import httpx
import pytest

from omni.tools.bilibili.api import BiliAPI, BiliAPIError

BV = "BV1GJ411x7h7"
AID = 928647776
CID = 123456

NAV = {
    "code": 0,
    "data": {
        "wbi_img": {
            "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
            "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png",
        }
    },
}
VIEW = {
    "code": 0,
    "data": {
        "bvid": BV,
        "aid": AID,
        "cid": CID,
        "title": "测试视频标题",
        "desc": "这是简介",
        "owner": {"name": "测试UP主"},
        "duration": 252,
        "pubdate": 1700000000,
        "stat": {"view": 1000, "like": 100, "coin": 10, "favorite": 20, "danmaku": 5, "reply": 8},
    },
}


def _api(handler) -> BiliAPI:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return BiliAPI(client=client)


# ---------- 目标归一化 ----------

def test_resolve_bvid_and_url():
    api = _api(lambda request: httpx.Response(200, json={}))
    assert api.resolve(BV)["bvid"] == BV
    assert api.resolve(f"https://www.bilibili.com/video/{BV}/?p=2")["bvid"] == BV
    assert api.resolve("av170001")["aid"] == 170001


def test_resolve_short_link_follows_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if "b23.tv" in str(request.url):
            return httpx.Response(
                302, headers={"Location": f"https://www.bilibili.com/video/{BV}"}
            )
        return httpx.Response(200, json={})

    api = _api(handler)
    assert api.resolve("https://b23.tv/abc123")["bvid"] == BV


def test_resolve_unrecognized():
    api = _api(lambda request: httpx.Response(200, json={}))
    with pytest.raises(BiliAPIError):
        api.resolve("这是啥玩意")


# ---------- 读端点 ----------

def test_video_info_parses():
    api = _api(lambda request: httpx.Response(200, json=VIEW))
    info = api.video_info(BV)
    assert info["title"] == "测试视频标题"
    assert info["owner"] == "测试UP主"
    assert info["cid"] == CID
    assert info["stat"]["view"] == 1000


def test_api_error_code_raises():
    api = _api(lambda request: httpx.Response(200, json={"code": -404, "message": "啥都木有"}))
    with pytest.raises(BiliAPIError, match="-404"):
        api.video_info(BV)


def test_video_comments_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(200, json=VIEW)  # 先补 aid
        assert "oid=928647776" in str(request.url)  # reply 用 view 拿到的 aid
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "replies": [
                        {
                            "member": {"uname": "张三"},
                            "like": 233,
                            "content": {"message": "好活"},
                        }
                    ]
                },
            },
        )

    api = _api(handler)
    comments = api.video_comments(BV, 5)
    assert comments[0] == {"user": "张三", "like": 233, "content": "好活"}


def test_video_subtitle_full_flow():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/x/web-interface/nav":
            return httpx.Response(200, json=NAV)
        if path == "/x/web-interface/view":
            return httpx.Response(200, json=VIEW)
        if path == "/x/player/wbi/v2":
            url = str(request.url)
            assert "wts=" in url and "w_rid=" in url  # WBI 签名已生效
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "subtitle": {
                            "subtitles": [
                                {"lan": "zh-CN", "subtitle_url": "//aisubtitle.hdslb.com/s.json"}
                            ]
                        }
                    },
                },
            )
        if path == "/s.json":
            return httpx.Response(
                200,
                json={"body": [{"content": "你好"}, {"content": "世界"}]},
            )
        raise AssertionError(f"unexpected request: {request.url}")

    api = _api(handler)
    assert api.video_subtitle(BV) == "你好\n世界"


def test_video_subtitle_none_available():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/x/web-interface/nav":
            return httpx.Response(200, json=NAV)
        if path == "/x/web-interface/view":
            return httpx.Response(200, json=VIEW)
        if path == "/x/player/wbi/v2":
            return httpx.Response(200, json={"code": 0, "data": {"subtitle": {"subtitles": []}}})
        raise AssertionError(f"unexpected request: {request.url}")

    api = _api(handler)
    assert api.video_subtitle(BV) == ""
