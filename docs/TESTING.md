# 测试运行与提速

## 日常开发

开发中运行受改动影响的模块与调用方测试，完整任务收尾、提交前再跑一次全量。专项通过只证明该范围；不要在每个小步骤、状态汇报或仅修改说明文字后反复全量。

```powershell
# 小范围验证：串行避免 worker 启动、重复导入/收集的开销
uv run --no-sync pytest -q -n 0 agent/tests/test_memory.py agent/tests/test_owner_memory.py

# 重跑失败：没有失败记录时直接结束，不退回全量
uv run --no-sync pytest -q -n 0 --lf --last-failed-no-failures=none

# 收尾全量：默认最多 4 个进程，按文件调度，输出最慢 10 项
uv run --no-sync pytest -q

# 并发问题诊断 / 调整并行度
uv run --no-sync pytest -q -n 0
uv run --no-sync pytest -q -n 8
```

同一份源码、测试、依赖、测试配置已全量通过时复用该次结果；其中任一变化或其他会话改了代码，都要重新判断受影响范围与最终验证。不要并发启动多个完整套件。只使用规范路径 `agent/tests/`，避免本机兼容联接 `tests/` 带来不同 nodeid 和失败缓存。

## 并行与浏览器隔离

根目录与 `agent/` 的 pytest 配置一致：`-n auto --dist=loadfile --durations=10`，`conftest.py` 将自动 worker 数限制为 `min(4, CPU 数)`。`pytest-xdist` 属于开发依赖并锁定在 `uv.lock`。`-n 0` 关闭并行，显式 `-n 8` 或 `PYTEST_XDIST_AUTO_NUM_WORKERS` 可覆盖自动 worker 数。

`loadfile` 保证同一文件在同一个 worker 中执行，复用模块级 fixture。`ui_browser` 在一个 UI 测试文件内仅启动一次 Chromium；每个用例仍创建独立 BrowserContext、页面、localStorage、cookie 和临时数据库。该 fixture 不跨模块存活，防止同步 Playwright 的运行中事件循环干扰其他 asyncio 测试。需要特别启动参数或浏览器生命周期断言的测试保留自己的浏览器。

Python 网络/模型闸门仍在每个 worker 启用；子进程继承离线环境。并行不改变测试数量、断言、业务超时或真实设备验收边界。

## 环境与 CI

全新开发环境使用 `uv sync --frozen`，安装浏览器用 `uv run --no-sync python -m playwright install chromium`。常驻服务正在使用 `.venv` 时不要重建环境或停止服务；测试使用 `uv run --no-sync`。

CI执行Ruff、格式检查、已收口领域Pyright、固定版本Prettier、ES模块语法、UI接口合同、重复定义、
任务摘要、索引检查、直接wheel/sdist→wheel及隔离导入校验，然后运行全部测试和离线评测。
前端开发环境需要Node 24和`npm ci --prefix web`。可用`scripts/ci.ps1 -Workers 0`排查串行行为，
默认`-Workers 4`。`-SkipSync`的含义不变，不能在用户服务运行中重建`.venv`。

## 耗时证据

`--durations` 区分 setup、call、teardown。需要保留报告时使用：

```powershell
uv run --no-sync pytest -q --junitxml=.pytest_cache/test-speed/full.xml
```

前后对比必须选择相同用例范围、机器与环境，并标明串行或 worker 数；历史报告的结果不能冒充当前源码的新实测。专项提速也不能按比例推算整个套件。

2026-10-31 同机实测：相同的 55 项 UI/上下文测试，优化前串行 **139.39 秒**，优化后 4 worker + 浏览器复用 **55.31 秒**，本次观察到约 **2.52 倍**提速、耗时减少 **60.32%**。这只是单次测量。

本轮并行全量共 1411 项，**1408 通过、3 失败，207.06 秒**。运行期间发生其他 UI 源码修改，不能作为固定源码快照全绿证据。3 个失败串行复核后，2 个资源版本检查已恢复，1 个 UI 契约检查仍因 `memory:resident-add` 未注册失败；串行同样复现。详细范围与原始报告摘要见 [提速验证记录](evidence/test-speed-2026-10-31.json)。
