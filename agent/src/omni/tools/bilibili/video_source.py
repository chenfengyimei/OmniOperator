"""B站取流适配器：实现分镜域的 VideoSource 协议。

**为什么放在 B站这个域里**：架构守卫禁止 tools 域之间互相引用
（tests/test_arch.py 的「能力域互相引用」规则）——分镜域不能 import B站域，
反过来可以。所以取流能力放在平台这一侧，由 bootstrap.py 组装时接进分镜域。

这个类不需要 import 分镜域：它只实现协议要求的形状（结构化类型），
所以两边谁也不依赖谁。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ...config import Asr
from . import asr
from .api import BiliAPI


class BiliVideoSource:
    """把 B站视频标识变成一个本地可解码的视频文件（以及可选的带时间戳台词）。"""

    def __init__(
        self,
        api: BiliAPI,
        asr_config: Asr | None = None,
        *,
        cache_root: Path | None = None,
    ) -> None:
        self._api = api
        self._asr = asr_config or Asr()
        self._cache_root = cache_root

    def fetch(self, target: str, destination: Path) -> Path:
        """下载视频流到 destination。

        走我们自己的 WBI 直链（带 Cookie），不引 ffmpeg + yt-dlp + cookies.txt
        那一套——参考项目就是这么做的，我们不需要。
        """
        url = self._api.video_stream(target)
        self._api.download_binary(url, destination)
        return destination

    def transcript(self, target: str) -> list[tuple[float, str]]:
        """ASR 出带秒级时间戳的台词。

        官方 B站字幕**没有时间戳**（`api.video_subtitle` 只给全文），
        而"格子挂台词"要的就是时间戳，所以这里走本地 ASR。
        拿不到就返回空列表——画面本身不受影响。
        """
        url = self._api.audio_stream(target)
        root = self._cache_root / "audio" if self._cache_root is not None else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="omni-asr-", dir=root) as tmp:
            audio = Path(tmp) / "audio.m4s"
            self._api.download_binary(url, audio)
            return asr.transcribe_segments(
                audio,
                model_name=self._asr.model,
                device=self._asr.device,
                compute_type=self._asr.compute_type,
                **({"download_root": self._cache_root / "models"}
                   if self._cache_root is not None else {}),
            )
