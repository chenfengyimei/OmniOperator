# 仓库与本地 Agent 产品结构

架构职责与分层仍以 [ARCHITECTURE.md](ARCHITECTURE.md) 为唯一权威。

## 目录归属

| 位置 | 内容与修改规则 |
|---|---|
| `agent/` | 本地 Agent 完整产品：源码、测试、配置模板、开发脚本、项目依赖 |
| `web/` | 单一原生ES模块界面源码、固定版本排版配置；开发依赖不随界面打包 |
| `website/` | 独立官网，本地素材与预览不混入 Agent 源码 |
| `market/` | 独立市场仓库：源码、内容与部署模板；主程序不依赖本地目录 |
| `docs/` | 全仓唯一交接入口、架构权威、开发记录和自动索引 |
| `scripts/` | 仓库安装/启动/审计及旧开发命令兼容转发 |
| `pyproject.toml` / `uv.lock` | 根 uv 工作区及统一依赖锁；产品依赖只在 agent/pyproject.toml 定义 |
| `config/` / `.env` | 已有用户配置控制区与凭据，不随源码移动 |
| `data/` | 默认记忆、档案、账本、日志与缓存；可由设置中心覆盖 |
| `skills/` | 默认运行技能库，已有内容与路径保持 |
| `xuexi/` / `prototype-html/` | 参考项目和原型，不是生产实现 |
| `temp/archive` / `temp/private` / `temp/build` | 历史素材 / 私人可恢复归档 / 可再生成产物；私人区域禁止模型文件工具读取 |

新增 Agent 能力进入 `agent/src/omni/` 既有分层，不在根建立第二套 tools/memory/terminal。
多个运行 Agent 是独立数据空间，共用这一份源码；不得给每个空间复制程序代码。
模块职责、内部调用和 API 仍由 `/project/` 与 [自动索引](index/MODULES.md) 查询。

## 路径契约

`APP_ROOT` 指本地 Agent 产品；`PROJECT_ROOT` 指仓库与默认运行态锚点。
配置、`.env`、data、skills 和默认工作目录保留原位置。索引与启动指纹识别
`agent/src/omni`，注入的旧结构合成仓库仍受支持；不会扫描 data 或凭据。

旧 `uv run omni*`、`uv run pytest` 和 `scripts/*.py` 命令保持可用。
已有开发环境迁移时可保留本机 `src`/`tests` 目录联接兼容旧编辑器和可编辑安装；
它们不入库、不用于新代码定位。干净检出直接用 uv 工作区，无须任何联接。

## 开发顺序

1. 读 HANDOFF 与 ARCHITECTURE，再按 PROJECT_INDEX 查所属能力域。
2. 新业务只在产品正式目录实现；跨层对象仍只由 bootstrap 构造。
3. 测试进入 agent/tests，静态模板进入 agent/config，开发工具进入 agent/scripts。
4. 运行全量 pytest 与项目索引检查；更新交接、证据和自动索引后提交。

界面资源按源码`web/`、sdist`_web/`、wheel`omni/web_assets/`定位；直接wheel、sdist→wheel和
隔离导入由`scripts/check_package.py`逐文件校验，升级恢复测试同时覆盖界面资源。
真实配置、活跃数据库和凭据保留原位置；历史归档用显式plan/apply/restore，不按文件夹名批量删除。
