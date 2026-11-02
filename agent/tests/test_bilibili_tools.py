"""B站工具集单测：注册规格 + 包装排版（api 用假对象，不发网络）。"""

from types import SimpleNamespace

from omni.tools.bilibili.api import BiliAPI
from omni.tools.bilibili.tools import register_bilibili_tools
from omni.tools.registry import ToolRegistry


class FakeAPI:
    def video_info(self, target):
        assert target == "BV1GJ411x7h7"
        return {
            "bvid": "BV1GJ411x7h7",
            "aid": 928,
            "cid": 123,
            "title": "测试视频",
            "desc": "简介内容",
            "owner": "UP小张",
            "duration_s": 65,
            "pubdate": 1700000000,
            "stat": {"view": 10, "like": 5, "coin": 2, "favorite": 1, "danmaku": 3, "reply": 4},
        }

    def account(self):
        return {"logged_in": True, "uid": "10086", "name": "小鱼本鱼", "level": 5, "coins": 42, "vip": True, "face": ""}

    def video_comments(self, target, count=10, *, sort="hot", page=1):
        assert sort in ("hot", "time") and page >= 1
        return [
            {
                "user": "李四",
                "like": 66,
                "content": "前排",
                "rpid": "88001",
                "reply_count": 2,
                "uid": "222",
                "time": 0,
                "to_user": "",
            }
        ]

    def comment_replies(self, target, rpid, count=10):
        assert rpid == "88001"
        return [
            {
                "user": "王五",
                "like": 3,
                "content": "同问",
                "rpid": "88002",
                "reply_count": 0,
                "uid": "333",
                "time": 0,
                "to_user": "李四",
            }
        ]

    def user_search(self, keyword, limit=5):
        assert keyword == "老王"
        return [{"uid": "5566", "name": "老王", "sign": "写代码的"}]

    def video_stream(self, target):
        assert target == "BV1GJ411x7h7"
        return "https://upos.example/video.mp4"

    def video_subtitle(self, target):
        return "字幕第一句\n字幕第二句"

    def audio_stream(self, target):
        assert target == "BV1GJ411x7h7"
        return "https://upos.example/low.m4s"

    def download_binary(self, url, dest, max_bytes=None):
        dest.write_bytes(b"fake-audio")


class FakeArtifacts:
    """产物库替身：只登记不落盘。"""

    def __init__(self):
        self.saved = []

    def save_image(self, data, mime, *, session_id, run_id, scope, kind, source, width=None, height=None):
        self.saved.append({"data": data, "kind": kind, "source": source, "width": width, "height": height})
        return SimpleNamespace(artifact_id="abc123", width=width, height=height, size=len(data))


def _registry(api=None, **kwargs) -> ToolRegistry:
    registry = ToolRegistry()
    register_bilibili_tools(registry, api or FakeAPI(), **kwargs)
    return registry


def test_registered_specs():
    registry = _registry()
    names = set(registry.names())
    assert {
        "bilibili_video_info",
        "bilibili_video_comments",
        "bilibili_video_subtitle",
        "bilibili_account_info",
        "bilibili_video_frames",
        "bilibili_comment_replies",
        "bilibili_user_search",
    } <= names
    assert "bilibili" in registry.toolsets()
    # 全部是 bilibili 工具集、SAFE 级
    for name in registry.names(toolsets={"bilibili"}):
        tool = registry.get(name)
        assert tool is not None and tool.risk.value == "safe"


def test_info_formatting():
    out = _registry().dispatch("bilibili_video_info", {"target": "BV1GJ411x7h7"})
    assert "测试视频" in out and "UP小张" in out and "1:05" in out and "简介内容" in out


def test_comments_and_subtitle_formatting():
    registry = _registry()
    comments = registry.dispatch("bilibili_video_comments", {"target": "BV1GJ411x7h7", "count": 5})
    assert "李四" in comments and "66 赞" in comments
    # 评论ID要露出来，否则没法接着读楼中楼
    assert "88001" in comments
    subtitle = registry.dispatch("bilibili_video_subtitle", {"target": "BV1GJ411x7h7"})
    assert "字幕第二句" in subtitle


def test_comments_support_latest_sort_and_pages():
    seen = {}

    class SortedAPI(FakeAPI):
        def video_comments(self, target, count=10, *, sort="hot", page=1):
            seen["sort"], seen["page"] = sort, page
            return []

    out = _registry(SortedAPI()).dispatch(
        "bilibili_video_comments", {"target": "BV1GJ411x7h7", "sort": "time", "page": 3}
    )
    assert seen == {"sort": "time", "page": 3}
    assert "最新" in out


def test_comment_replies_formatting():
    out = _registry().dispatch("bilibili_comment_replies", {"target": "BV1GJ411x7h7", "rpid": "88001"})
    assert "王五" in out and "同问" in out and "李四" in out


def test_comment_replies_requires_rpid():
    out = _registry().dispatch("bilibili_comment_replies", {"target": "BV1GJ411x7h7"})
    assert "评论ID" in out


def test_user_search_formatting():
    out = _registry().dispatch("bilibili_user_search", {"keyword": "老王"})
    assert "老王" in out and "5566" in out


def test_account_info_formatting():
    out = _registry().dispatch("bilibili_account_info", {})
    assert "小鱼本鱼" in out and "10086" in out and "Lv5" in out


def test_account_info_reports_logged_out():
    class LoggedOut(FakeAPI):
        def account(self):
            return {"logged_in": False, "uid": "", "name": "", "level": 0, "coins": 0, "vip": False, "face": ""}

    out = _registry(LoggedOut()).dispatch("bilibili_account_info", {})
    assert "没有登录" in out


class VideoAPI(FakeAPI):
    """下载到的是一段真视频（PyAV 现场编码），让抽帧链路真的跑起来。"""

    def download_binary(self, url, dest, max_bytes=None):
        import av
        from PIL import Image

        container = av.open(str(dest), mode="w")
        stream = container.add_stream("mpeg4", rate=5)
        stream.width, stream.height = 160, 120
        stream.pix_fmt = "yuv420p"
        for index in range(10):
            frame = av.VideoFrame.from_image(Image.new("RGB", (160, 120), (index * 20, 10, 90)))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        container.close()


def test_video_frames_tool_saves_one_artifact(tmp_path):
    """抽帧拼图：产物库里只登记**一张**长图，模型一次就能看完。"""
    artifacts = FakeArtifacts()
    out = _registry(VideoAPI(), artifacts=artifacts).dispatch(
        "bilibili_video_frames", {"target": "BV1GJ411x7h7", "count": 2, "width": 320}
    )
    assert len(artifacts.saved) == 1
    assert artifacts.saved[0]["kind"] == "video_frames"
    assert artifacts.saved[0]["data"][:8] == b"\x89PNG\r\n\x1a\n"
    assert "[artifact:abc123]" in out
    assert "帧" in out


def test_video_frames_tool_without_artifact_store_is_explicit():
    out = _registry(VideoAPI()).dispatch("bilibili_video_frames", {"target": "BV1GJ411x7h7", "count": 2, "width": 320})
    assert "产物库不可用" in out


def test_subtitle_empty_hint():
    registry = ToolRegistry()

    class NoSub(FakeAPI):
        def video_subtitle(self, target):
            return ""

    register_bilibili_tools(registry, NoSub())
    out = registry.dispatch("bilibili_video_subtitle", {"target": "BV1GJ411x7h7"})
    assert "没有可获取的字幕" in out


def test_bilibili_api_is_real_class_importable():
    # 冒烟：假 API 与真 API 接口一致（duck typing 已由 FakeAPI 结构保证）
    assert BiliAPI is not None


def test_transcript_tool_with_mocked_asr(tmp_path, monkeypatch):
    """转写工具链路：音频流→下载→ASR（模型用假实现，不真跑）。"""
    from omni.tools.bilibili import asr as asr_mod

    def fake_transcribe(path, *, model_name="small", device="cpu", compute_type="int8"):
        assert path.exists() and path.stat().st_size > 0
        assert model_name == "small"
        return "[00:00] 假转写第一句 [00:01] 第二句"

    monkeypatch.setattr(asr_mod, "transcribe", fake_transcribe)
    out = _registry().dispatch("bilibili_video_transcript", {"target": "BV1GJ411x7h7"})
    assert "假转写第一句" in out


def test_transcript_tool_surfaces_api_error(monkeypatch):
    class NoAudio(FakeAPI):
        def audio_stream(self, target):
            raise RuntimeError("该视频没有可用的 DASH 音频流（可能仅登录可见或受限）")

    registry = ToolRegistry()
    register_bilibili_tools(registry, NoAudio())
    out = registry.dispatch("bilibili_video_transcript", {"target": "BV1GJ411x7h7"})
    assert "DASH" in out  # 错误以字符串回传给 LLM，而不是抛异常
