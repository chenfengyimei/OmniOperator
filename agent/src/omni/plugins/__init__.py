"""插件子系统（顶层包，与 channels/bootstrap 同级）。

三个模块各司其职：
- `manifest.py`：插件清单（manifest.json）的解析与校验，纯数据、零执行；
- `host.py`：插件宿主——收包、隔离校验、人工确认激活、装载注册、启停卸载；
- `tools.py`：面向 agent 的生命周期工具（plugin_list/install/activate/…）。

信任模型一句话：**校验绝不执行代码；激活（人工确认）即同意执行**。
进程内插件是受信代码，Python 没有可靠的进程内沙箱——这里不假装有。
不可信来源的外部能力请走 MCP（进程外、既有白名单与确认链），不是插件。
设计全文见 docs/PLUGIN_SYSTEM.md。
"""
from .host import PluginApiError, PluginContext, PluginHost
from .manifest import PluginManifest

__all__ = ["PluginApiError", "PluginContext", "PluginHost", "PluginManifest"]
