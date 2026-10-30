# Agent 测试

全部测试从仓库根 `uv run pytest` 或产品目录执行。fixtures 只使用临时目录，不依赖根目录的兼容链接，不访问真实模型或设备。架构规则由 test_arch.py 守卫；仓库/产品路径兼容由 test_repository_layout.py 守卫。
