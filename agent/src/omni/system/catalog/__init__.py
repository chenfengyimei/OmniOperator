"""项目索引：只解析源码与显式分类，不导入被索引模块、不接触运行数据。"""

from .scan import build_index

__all__ = ["build_index"]
