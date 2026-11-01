"""视频来源：把"一个目标"变成"一个本地可解码的视频文件 + 可选的带时间戳台词"。

**刻意不 import B站那个域**：架构守卫禁止 tools 域之间互相引用
（tests/test_arch.py 的「能力域互相引用」规则：tools/<域>/x.py 不能 import
omni.tools.<别的域>.*）。所以取流能力用 Protocol 注入，
由 bootstrap.py 这个唯一的组装根把具体平台接进来。

这样分镜引擎与平台解耦——以后接 YouTube、接本地文件、接公司内网视频，
都不用改 detect/segments/decode/render 这四层。
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class VideoSource(Protocol):
    """视频来源适配器（平台相关，由平台域实现）。"""

    def fetch(self, target: str, destination: Path) -> Path:
        """把 target 变成一个本地视频文件，返回该文件路径。

        实现方负责下载与落盘；引擎只管拿到一个能解码的文件。
        """

    def transcript(self, target: str) -> list[tuple[float, str]]:
        """带时间戳的台词 [(起始秒, 文本)]；拿不到就返回空列表。

        时间戳要能落到秒级——格子与台词的对齐全靠它。
        """


class LocalVideoSource:
    """本地文件源：target 就是本机视频路径。给离线调试与自建素材用。"""

    def fetch(self, target: str, destination: Path) -> Path:
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError(f"找不到视频文件：{target}")
        return path

    def transcript(self, target: str) -> list[tuple[float, str]]:
        return []
