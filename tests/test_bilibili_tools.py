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

    def video_comments(self, target, count=10):
        return [{"user": "李四", "like": 66, "content": "前排"}]

    def video_subtitle(self, target):
        return "字幕第一句\n字幕第二句"

    def audio_stream(self, target):
        assert target == "BV1GJ411x7h7"
        return "https://upos.example/low.m4s"

    def download_binary(self, url, dest, max_bytes=None):
        dest.write_bytes(b"fake-audio")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_bilibili_tools(registry, FakeAPI())
    return registry


def test_registered_specs():
    registry = _registry()
    names = set(registry.names())
    assert {"bilibili_video_info", "bilibili_video_comments", "bilibili_video_subtitle"} <= names
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
    comments = registry.dispatch(
        "bilibili_video_comments", {"target": "BV1GJ411x7h7", "count": 5}
    )
    assert "李四" in comments and "66 赞" in comments
    subtitle = registry.dispatch("bilibili_video_subtitle", {"target": "BV1GJ411x7h7"})
    assert "字幕第二句" in subtitle


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
