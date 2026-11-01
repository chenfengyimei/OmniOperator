# Agent 开发工具

源码契约 `check_ui_contract.py`，项目索引 `project_index.py`，离线评测 `eval_core.py`，CI `ci.ps1`，升级演练/验收/采样及临时进程验证均在此维护。参数中的数据与证据目录继续显式注入。

测试入口为 `uv run --no-sync pytest -q`（默认最多 4 进程、按文件分发）；开发中对相关测试追加 `-n 0`。CI 支持 `ci.ps1 -Workers 4`，串行诊断使用 `-Workers 0`。规则与耗时对比见 [TESTING.md](../../docs/TESTING.md)。
