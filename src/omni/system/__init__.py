"""本地系统域：服务管理（NapCat / 常驻通道启停与状态）+ 配置读写。

只管本机进程，供 Web 工作台（人工操作）与 agent 的 system 工具（自查）共用。
"""
from .services import ServiceManager, ServiceError  # noqa: F401
