# Agent 测试

从仓库根执行 `uv run --no-sync pytest`，或进入产品目录执行。开发中选择受影响文件并加 `-q -n 0`；整个 V2 开发冻结后统一全量。

- `v2/core`、`http`、`ui`、`platforms/{common,windows,macos,linux}`、`operations`、`integrations`：按用途归组的 V2 测试，文件名和原测试节点保留。
- `omni_test_support/v2`：可复用样例、seed、模型和共享场景；测试不直接导入其他测试模块。
- `omni_test_support/paths.py`：仓库、产品和测试根的统一锚点。
- 根 `test_*.py`：V1 及共用守卫；`conftest.py`、离线守卫和 pytest 插件留在根，保持原隔离及 BrowserContext 生命周期。

fixtures 只使用临时目录，不依赖根兼容联接，不访问真实模型或设备。架构由 V1/V2 各自守卫；目录和发现由 test_repository_layout.py、v2/core/test_v2_layout.py 覆盖。路径映射见 docs/v/layout.json。
