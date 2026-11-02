"""旧导入兼容层；实现位于 tools/scheduling/。"""

from .scheduling import register_cron_tools

__all__ = ["register_cron_tools"]
