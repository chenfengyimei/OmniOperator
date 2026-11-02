"""专题工作台路由；只消费注入的 Runtime，不在通道构造核心对象。"""

from .routes import create_workbench_router

__all__ = ["create_workbench_router"]
