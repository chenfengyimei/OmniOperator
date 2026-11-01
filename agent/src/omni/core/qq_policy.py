"""主动提醒策略：显式所有者、安静时段和独立每日额度；不做网络发送。

判定逻辑已下沉到 `safety/proactive.py`（叶子层），原因见那里的说明：
tools 层不能 import core，而「模型自己调 qq_send_message 主动发消息」
这条路径原先完全绕过了主动策略——同一个动作两条路，只有一条有闸门。

这里保留一层薄转发，`core/cron.py` 与 `channels/web.py` 的既有调用点不用改。
"""
from __future__ import annotations

from ..safety.proactive import ProactiveDecision, proactive_decision

__all__ = ["ProactiveDecision", "proactive_decision"]
