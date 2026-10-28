# 插件系统与插件社区（设计文档）

状态：插件系统核心已交付（2026-10-29）；插件社区（客户端 + 参考服务器）见 §5，
服务器部署待项目所有者提供机器后进行。
本文是插件子系统的**唯一设计权威**；分层与模块职责仍以 [ARCHITECTURE.md](ARCHITECTURE.md) 为准。

## 1. 目标与定位

让**用户**和**项目内的 agent** 都能自己开发新功能、扩展更多功能：

- agent 写一个插件（工作区里的一个目录：`manifest.json` + `plugin.py`），
  就能给主对话注册新工具——不需要改内核代码、不需要重新部署；
- 用户从插件社区下载别人写好的插件包，审阅后一键激活；
- 所有扩展都经过同一条纪律线：**数据化校验零执行 → 人工确认激活 → 既有审批链照常生效**。

明确不做的（v1）：
- 进程内沙箱。Python 没有可靠的进程内隔离，本系统**不假装有**（见 §3 信任模型）；
- 插件注册通道/UI/记忆类型——v1 插件只能注册**工具**；这些扩展点留给社区反馈后再开；
- 通用插件市场（多租户、计费、评分）——社区服务是单 owner 部署的共享索引，不是商店。

## 2. 插件包格式

一个插件 = 一个目录（zip 或工作区目录），**平铺或包一层顶层目录皆可**：

```
demo-tools/
  manifest.json      # 必需：身份与能力声明（见下）
  plugin.py          # 必需：入口，定义 register(ctx)
  README.md          # 可选
  references/        # 可选：纯文本参考资料（.md/.txt/.json/.yaml 等，≤64KiB，≤20 个）
```

`manifest.json`（全部必填项校验失败即拒收）：

```json
{
  "id": "demo-tools",
  "name": "演示插件",
  "version": "1.0.0",
  "description": "一句话描述（≤80 字）",
  "entry": "plugin.py",
  "author": "someone",
  "homepage": "https://…",
  "permissions": ["tools"]
}
```

- `id`：3-40 位小写字母/数字/连字符（与技能名同规则），也是包目录名、工具前缀、
  卸载/启停的操作键；
- `permissions`：v1 **只允许 `["tools"]`**——后续开新能力口（通道/记忆/UI）时
  在这里扩展，而不是让插件隐式获得；
- `version`：`x.y[.z[.w]]` 数字版本，升级与社区索引都以它排序。

入口契约（`plugin.py`）：

```python
def register(ctx):
    """ctx 是宿主给的受限注册面（omni.plugins.host.PluginContext）。"""

    def summarize(args):
        return "…"

    ctx.add_tool(
        name="demo-tools-summarize",       # 必须以 "<id>-" 开头
        description="给一段文本做摘要",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        func=summarize,
        risk="safe",                        # safe / confirm / dangerous
        requires_human=False,
    )
```

`ctx` 上还有 `ctx.id`（插件 id）与 `ctx.data_dir`（插件私有数据目录，
随卸载一起删除）。刻意**只暴露这两个能力**：插件拿不到 ToolRegistry 本体、
拿不到运行时内部件、不能反注册别人的工具。

## 3. 信任模型（安全边界，如实声明）

红线不变：花钱/发布/群发/加好友人工确认。插件系统的边界按四层收口：

1. **校验零执行**。安装（`plugin_install`，SAFE）只做数据化检查，绝不 import/运行
   包内任何代码：类型白名单（manifest + 唯一顶层入口 .py + README + references
   纯文本）、zip-slip/盘符/ADS/Windows 保留名全防、条目重名拒绝、体积与数量上限
   （zip ≤4MiB、解包 ≤8MiB、条目 ≤50、入口 ≤128KiB）。校验通过也**不等于安全**——
   它只是让"激活前审阅"有清单可看（`plugin_list` 展示逐文件列表）。
2. **激活即同意执行**。`plugin_activate` 是 CONFIRM + `requires_human`：
   审批流里那个人工确认就是"用户激活"这一步。导入入口模块会执行其顶层代码
   ——这与安装 npm 包同理；Python 进程内没有可靠沙箱，这里不假装有。
   不可信来源的扩展请走 **MCP**（进程外、白名单与确认链），不要做进程内插件。
3. **注册面受限**。工具名强制 `<id>-` 前缀（结构上不可能影子覆盖内置工具）、
   `toolset=plugin:<id>`（可归因；群聊作用域下因许可名单机制自动禁用）、
   风险等级由作者声明并**照常走既有审批链**（SAFE 直通 / CONFIRM 问人 /
   DANGEROUS 一律拒绝；无人值守 DenyApprover 拒绝一切非 SAFE——包括插件声明的）。
4. **内容指纹钉死**。激活时记整包 sha256；启用前复核（防"激活后被改"）；
   激活前对隔离区做逐文件防篡改比对；`__pycache__` 等生成物不计入指纹。
   改过内容的包必须重走「卸载 → 安装 → 人工确认激活」链，不能靠 enable 洗白。

如实的已知边界：disable/remove 反注册工具入口，但**已导入的模块代码无法从
进程里卸载**（Python 无安全卸载），彻底清除需重启进程——所有返回消息都写明。

## 4. 本地生命周期

```
data/plugins/
  .inbox/        # 用户/社区客户端放 zip 的收件箱
  .quarantine/   # 校验通过的待激活包 + <id>.package.json 导入记录
  <id>/          # 激活后的插件包（entry 代码就位）
  <id>.plugin.json  # 激活状态（版本/来源/内容指纹/启停/装载错误）
  .data/<id>/    # 插件私有数据目录（ctx.data_dir）
  .locks/        # 跨进程文件锁（激活/卸载与其它进程的写入互斥）
```

工具面（agent 可用，全部进主注册表）：

| 工具 | 风险 | 语义 |
| --- | --- | --- |
| `plugin_list` | SAFE（read_only） | inbox / 隔离区 / 已安装三段清单，含文件列表与装载错误 |
| `plugin_install` | SAFE | `package=`（inbox zip）或 `path=`（工作区目录，agent 自开发）→ 隔离区 |
| `plugin_activate` | **CONFIRM+requires_human** | 隔离区 → 正式目录 + 装载注册；激活即同意执行 |
| `plugin_enable` / `plugin_disable` | SAFE | 指纹复核后装载 / 反注册工具 |
| `plugin_remove` | **CONFIRM+requires_human** | 卸载（删包+状态+数据）；也可丢弃隔离区包 |

启动恢复：bootstrap 构造 `PluginHost` 后 `load_installed()` 把所有已启用插件
重新装载注册；装载失败只报 stderr、不阻断启动（错误可在 `plugin_list` 里看到）。

### agent 自开发闭环

agent 用工作区文件工具（写 `.py`/`.json` 本身就要过确认）开发一个插件目录，
然后 `plugin_install(path="my-plugin")` → `plugin_list` 把文件清单给用户看 →
用户确认 `plugin_activate`。全程没有"agent 绕过人给自己装代码"的路径。

## 5. 插件社区（已交付客户端与参考服务器，待所有者提供机器部署）

三个部件（2026-10-29 交付）：

- **参考服务器** `omni/plugins/server.py`（FastAPI + 文件存储，单 owner 部署）；
- **客户端** `omni/plugins/community.py`（`CommunityClient` + agent 工具）；
- **本地配置** `data/plugins/community.json`（`server_url` + `token`）——刻意不进
  config.yaml：社区接入点属于用户级偏好，且 config.py 由并行前端批持有。

### 5.1 部署（所有者提供服务器后）

```bash
# 服务器上（clone 仓库后）：
uv sync
uv run python -m omni.plugins.server --root /srv/omni-community \
    --token "$COMMUNITY_TOKEN" --host 0.0.0.0 --port 8080
```

- 存储布局：`<root>/index.json`（索引）+ `<root>/packages/<id>/<version>.zip` +
  `<root>/packages/<id>/meta.json`（版本记录）。无数据库，备份 = 复制目录。
- `--token` 为空 = 允许匿名发布（不推荐）；发布 token 只用于发布，
  查询与下载无需 token。
- 建议前置反代做 TLS 与限流；服务本身只做索引与分发。

客户端配置（对 agent 说"配置插件社区"，或直接编辑文件）：

```json
{"server_url": "https://community.example.com", "token": "发布 token（可空）"}
```

### 5.2 API 契约（v1）

| 方法与路径 | 语义 |
| --- | --- |
| `GET /api/health` | 存活与索引数量 |
| `GET /api/plugins?query=&limit=&offset=` | 搜索（id/名称/描述/作者子串匹配），按 id 排序 |
| `GET /api/plugins/{id}` | 详情：latest_version、versions 列表、latest_sha256、下载量 |
| `GET /api/download/{id}?version=` | zip 字节；`X-Plugin-Sha256` 响应头；存储指纹不符时 503 拒发 |
| `POST /api/publish` | multipart：`file`=zip + `token`；401 拒 token、400 拒坏包、409 拒重复版本 |

服务端纪律：发布只做**纯数据校验**（zip 内必须有合法 manifest.json 且入口
在包内）——深度校验（类型白名单/上限/防篡改）仍在每台机器安装时做；
**版本不可变**（同 id+version 重复发布 409，升级只能加版本号）。

### 5.3 客户端行为

- `community_search` / `community_info`（SAFE，read_only）：查询展示；
- `community_install`（SAFE）：详情 → 下载（≤4 MiB）→ **sha256 与响应头
  校验**（不符即丢弃、不落收件箱）→ 落 inbox → 走既有隔离安装链
  （激活仍需人工确认——**社区不背书安全性**）；成功入隔离区后清理收件箱 zip；
- `community_publish`（**CONFIRM + requires_human**——"发布"是项目红线）：
  `path=` 工作区插件目录自动打包（跳 `__pycache__`，≤4MiB/50 条目）上传，
  或 `package=` inbox 里的 zip 原样上传；服务器响应带 sha256。

### 5.4 未做（如实）

- 无评分/评论/统计页面、无多 owner 鉴权（单 token 即全权发布）；
- 无"社区有新版本"的主动提醒（本地 list 不拉索引比对）；
- 客户端只信任**https + sha256**，不校验作者签名——激活前的人工审阅
  仍是最终防线。

## 6. 平台坑与实现记录

1. `__pycache__`：导入插件时解释器会在包目录写字节码缓存，把内容指纹打花
   ——读包、指纹与防篡改一律跳过 `__pycache__`（工作区开发目录里的同样跳过）；
2. 常见 zip 会包一层顶层目录（"压缩文件夹"），读取时剥掉公共顶层再校验形状；
3. 插件装载用 `importlib.util.spec_from_file_location`（文件加载，非 omni 模块
   系统）；模块名带 uuid 后缀，disable 后重新 enable 拿到的是新模块对象；
4. 装载失败（含 register 中途抛异常）必须整组回滚已注册工具——测试覆盖了
   "注册到一半炸了"的场景；
5. 架构守卫：`plugins/` 是顶层包（与 channels/bootstrap 同级不设限），守卫的
   "组装根唯一构造"规则约束宿主不在插件包内构造 Gateway/MemoryStore 等；
   插件代码本身是外部文件，不属于 omni 模块图，不受分层规则约束——这正是
   它必须经人工确认才能进进程的原因。
6. **httpx ≥0.28 的 `ASGITransport` 只实现 `handle_async_request`**（只能配
   AsyncClient）：同步 `httpx.Client` 的测试需要自建同步桥接
   （`tests/test_plugin_community.py::SyncASGITransport`——专用事件循环线程 +
   `run_coroutine_threadsafe`）。生产客户端走真实 URL，不受影响。
