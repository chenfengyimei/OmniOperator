# V2 扩展链（D6.extensions）

> 文档范围：本页维护该能力合同与所属批次记录。当前默认入口为 V2；[当前交接](../../HANDOFF.md)区分已实现、专项通过和原生验收，历史测试数字不覆盖当前源码。

本文是 V2 扩展（可信 Python 插件）子系统的设计权威：安装与捕获、进程外宿主、显式授权与漂移撤权、产物协议、版本与升级、仍未完成的宿主回调与市场。实现进度与原始报告见 `docs/evidence/v2-extension-artifacts.json`。

## 1. 生产路径与真值

```
所有者安装（目录来源）→ ExtensionPackages.capture 封存为对象（package_sha256 / catalog_sha256）
  → extension_revisions 不可变版本 + extension_tools 工具目录（spec_sha256 含 artifacts 能力）
  → 所有者显式 authorize（信任 + 当前 catalog_sha256）→ extensions 槽位 enabled/current revision
  → 每回合 extension_capture_run 封存 run_extension_tools（未授权/漂移不可见）
  → 模型调用 ext__<id>__<tool> → 动作行 + 审批（写操作）→ 宿主子进程 invoke
  → 结果文本/对象产物；终态与审计共同提交；撤权取消在途调用
```

- 真值：`<home>/data/omni.db`；包正文与产物正文都只在对象存储（`<home>/objects`），行表只存 `object_id` 与度量。
- 插件进程是**故障隔离**，不是操作系统沙箱；插件仍以本机用户身份运行，只能安装所有者审核过的可信包。
- 外部来源不可信：插件描述与返回值不授予权限、不自动打开链接；宿主不信任子进程上报的字节与哈希。

## 2. 数据模型（既有，v2_0019 起）

- `extensions`：每 Agent 槽位（`name` 唯一、`revision`、`authority_epoch`、`enabled/state`、`current_revision_id`）。
- `extension_revisions`：不可变版本（`package_sha256`、`catalog_sha256`、`manifest`、`files`、`entry`、对象引用）。
- `extension_tools`：工具目录行（`name/local_name/description/parameters/timeout_s/writes/spec_sha256`）；`spec_sha256` 现在包含 `artifacts` 能力，能力变化会改变目录指纹。
- `grants`：逐工具授权（`authority_epoch`、`history_epoch`、`review_audit_id`、`revoked_at`）。
- `run_extension_tools` / `action_extension_bindings`：每 run 封存与动作绑定，均不可变。
- `extension_install_requests`：安装幂等键与请求指纹。

产物协议**不新增表**：产物与动作通过既有 `action_objects`（`settle_action` 按 `ActionResult.artifacts` 建立）关联，对象 GC 按引用/固定保护。

## 3. 进程外宿主与失败分类

- 每个扩展一个 `WorkerClient`（`OwnedProcess` + Windows Job/进程组），握手只校验静态元数据，**授权调用到达前不导入插件代码**；同一宿主串行，最多 4 个宿主、64 个待处理调用。
- 子进程 stderr 持续排空并只记字节数元数据；异常正文不回传。
- 失败分类：派发前失败（策略/授权/参数/宿主不可用）→ `DENIED/ERROR` 且无副作用；已派发后的异常、崩溃、畸形结果 → **UNKNOWN**（可能已执行，不重放）；插件 `status=ok` 且有产物但产物不可验证 → 仍归 UNKNOWN。
- 撤权/停用：数据库先撤销授权并递增代际，随后取消在途调用、关闭宿主；实际未回收时 `health()` 暴露 `cleanup_pending`。

### 3.1 受控宿主回调

- 仅当 `invoke` 显式声明第 4 个参数时注入 `HostBridge`；三参数插件协议不变，不自动获得宿主能力。
- 能力白名单固定为两项：`context`（返回 agent/run/extension/tool 标识，不含凭据）与 `log`（≤1000 字符文本）；未知能力回 `callback_denied`，由插件自行处理，宿主不代其执行。
- 每次调用最多 16 次回调、日志合计 ≤8192 字节；超预算、超长日志、畸形帧或通道关闭一律在已派发后归 **UNKNOWN**，回收宿主且不重放。
- 回调日志以 `data.host_callbacks = {calls, logs}` 随动作结果有界保存；模型文本仍只含插件自己的返回文本。

## 4. 产物协议（本批新增）

### 4.1 声明

工具条目可声明可产出的媒体类型（不声明则不能产出产物）：

```json
{
  "name": "render",
  "description": "Render the approved content into an image",
  "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": false},
  "writes": true,
  "timeout_s": 30,
  "artifacts": ["image/png"]
}
```

- `artifacts`：0..4 个媒体类型，小写 `type/subtype`（如 `image/png`、`application/pdf`），不可重复。
- 该能力进入 `catalog` 行与 `spec_sha256`；能力变化即目录指纹变化 → 旧授权撤销、需要重新审阅授权。
- 工具仍为写操作（`writes=true`），产物能力不改变审批链。

### 4.2 子进程侧

- 宿主为每个扩展准备唯一可写目录 `<home>/control/extension-artifacts/<agent>/<extension>`，启动子进程时通过 `OMNI_EXTENSION_ARTIFACTS` 传入；其余环境仍是白名单。
- 工具返回 `data.artifacts = [{"name": "report.png", "media_type": "image/png"}]`（只允许这两个键）；子进程只读取该目录内的普通文件（拒绝符号链接、路径越界、缺失文件），计算 `bytes` 与 `sha256`，并按声明媒体类型校验后把完整描述（name/media_type/bytes/sha256）回传。
- 越界/未声明类型/超限（单文件 8 MiB、总计 16 MiB、最多 4 个、文件名 `[A-Za-z0-9][A-Za-z0-9._-]{0,119}`）→ 子进程返回 UNKNOWN，不伪造成功。

### 4.3 宿主与服务侧

- 服务在每次调用前清空并重建该扩展的产物目录，调用后再次独立校验：路径必须解析到目录内、非符号链接、普通文件、字节数与 SHA-256 与子进程上报一致、媒体类型在**捕获的 manifest** 中声明。
- 校验通过后逐份 `ObjectService.publish` 为不可变对象；`ActionResult.artifacts` 携带对象 id，`settle_action` 建立 `action_objects` 关联，GC/备份/恢复沿用既有对象合同。
- 模型文本只追加有界摘要（媒体类型 + 字节数），**不含文件名或正文**；`ActionResult.data.artifacts` 只含 `{id, media_type, bytes}`。
- 任一步骤失败在派发之后 → UNKNOWN（`extension_artifact_invalid`），不发布、不重放、不建立动作引用。
- 调用结束后删除产物目录（成功、失败、取消一致）；宿主关闭时清理根目录。

## 5. 版本、升级与授权

- 升级安装带 `extension_id + revision`：不改变包身份，追加不可变版本，槽位置为 disabled 并**撤销旧授权/递增代际**；必须对新版本重新审阅授权。
- 同一 `catalog_sha256` 重放安装返回原结果（幂等键 + 请求指纹）；原始来源改动不影响已捕获版本。
- 目录/参数/产物能力变化 → 授权漂移，旧 run 封存与动作绑定不再可派发。
- 安装上限：每 Agent 16 个槽位、128 个不可变版本、包 ≤8 MiB、64 个文件、单文件 ≤1 MiB。

## 6. HTTP 与工作台

- 既有 `/agents/{id}/extensions`（列表/详情/安装/授权/停用）与工作台「可信插件」分区。
- 新增 `GET /agents/{id}/extensions/{extension_id}/actions?after=&limit=`：按扩展返回最近动作证据，只含 `id/tool/state/effect/ended_at` 与产物 `{id,media_type,bytes}`，**不回传参数、结果正文或异常**；游标与 limit（1..50）有界，跨 Agent 404。
- 工作台新增“最近调用/产物”：逐条显示时间、工具、终态与产物类型/大小；`text/*` 与 `application/json` 可显式预览前 4000 字符，其他类型只提示已保存为对象（可通过既有对象接口读取）。切换插件/刷新/换 Agent 会清空旧预览，避免迟到响应覆盖新目标。
- 新增 `POST /agents/{id}/extensions/market`（body 只接受 `{source}`）：所有者给定**本机绝对目录**，只读扫描其直接子目录的 `extension.json`（有界读取、拒绝符号链接/junction、最多 64 项/32 个有效候选、损坏候选跳过），返回 `name/version/entry/source/catalog_sha256` 并与已安装槽位比对给出 `installed/installed_version/extension_id/revision/is_current_version/update_available`；扫描不捕获、不执行、不写库。
- 目录通过可关闭的原生迭代器枚举，最多保留64个直接目录项（普通文件也计数）；观察到第65项即以`extension_invalid`（422）拒绝整次扫描，避免先枚举并排序任意大目录。范围内保持原排序和32个有效候选上限。清单复用描述符读取：读取前拒绝超过64 KiB的声明大小，实际最多读取64 KiB加1字节，并复核文件身份/大小/修改时间；非普通文件、硬链接、变动或坏清单跳过，所有已打开流与目录迭代器显式关闭。调用等待取消后实际线程仍按既有任务归属排空，不执行插件或自动登记候选；不据此声明整个进程内存峰值已验证。
- 安装仍走既有 `POST /extensions`：显式确认后由 `ExtensionPackages.capture` 封存、新版本撤销旧授权并要求重新授权；失败不自动重试。真实网络市场源与自动下载不在本轮范围。
- 工作台「可信插件」新增本地市场目录检查：候选逐条显示名称/版本/当前版本/可更新状态，已是当前版本禁用登记；登记按钮先弹确认（名称、版本、目录、更新会撤销旧授权），确认后才走既有 `POST /extensions` 捕获固定包，成功后刷新详情并重新扫描；失败只提示、不自动重试。
- 尚未做：进程外宿主回调、真实网络市场源与真实第三方插件验收。

## 7. 上限与错误码（择要）

`extension_invalid`、`extension_integrity_failed`、`extension_snapshot_drift`、`extension_not_authorized`、`extension_artifact_protocol`（子进程回传对象 id 等旧式产物）、`extension_artifact_invalid`（发布前校验失败）、`extension_outcome_unknown`（已派发后异常/畸形结果）、`extension_hosts_closing`、`extension_busy`、`extension_cleanup_pending`。

## 8. 验证与未完成

- 本节点覆盖：manifest/目录指纹（含 artifacts 能力变化）、描述校验、真实子进程产物写入与宿主二次校验、对象发布与动作引用、文本无正文、越界/未声明/缺失/超限均归 UNKNOWN 且不发布对象、产物目录清理；调用方（HTTP/对象/worker/架构）回归。
- 工作台节点覆盖：按扩展的动作证据接口（白名单字段、游标/上限、跨 Agent 拒绝）、390/1024 安装→授权→模型调用→审批→产物列表→文本预览、资源版本 v50。
- 市场扫描节点覆盖：本地目录 v1/v2 对比、损坏候选跳过、相对/不存在/链接源拒绝、扫描后版本不变；HTTP 返回字段、非法 body 422、未授权 401；62 项调用方/服务/HTTP 全过（`docs/evidence/v2-extension-market.json`）。
- 市场工作台节点覆盖：390/1024 安装 v1→授权→检查更新→v2 可更新→确认登记→详情 v2 且 disabled、旧授权撤销；资源版本 v51；25 项 UI/服务/HTTP 回归全过。
- 宿主回调节点覆盖：context/denied/log 正常路径、日志不入模型文本、超预算与超长日志两类 UNKNOWN、三参数兼容；93 项扩展/包/宿主/worker/架构回归全过（`docs/evidence/v2-extension-callbacks.json`）。
- 未完成/待真实验收：真实市场源、真实第三方插件验收（含使用回调的插件）、三平台原生 ABI；宿主回调白名单目前仅 context/log。

## 历史批次状态（非当前结论）

以下原记录中的默认入口与完成度仅属于当时源码；当前默认入口为 V2，实际进度以当前交接和任务为准。

> 默认正式入口仍是 V1；扩展链未完成，真实第三方插件与市场源另记“待真实验收”。
