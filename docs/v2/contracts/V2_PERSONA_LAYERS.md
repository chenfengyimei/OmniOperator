# V2 分层人格与跨渠道统一卡设计合同（K15 可离线缺口，实现前）

**状态：`design_contract_recorded`，未实现。** 本文件记录分层人格组合、渠道 sanitize、角色包 epoch 切换的边界与所有者决策；本批无源码、测试、迁移、路由改动。

## 1. 范围

把 K15 审计中的「分层人格 + 跨渠道统一卡」收敛为**纯逻辑组合层**，再接入既有 `settings["persona"]`/系统前缀与渠道适配：

- 层：`identity-base`（身份底座）+ `core`（核心价值观/语气）+ `tone`（当前语气）+ `scene`（渠道场景）+ `daily`（当日状态/日程）；
- 场景：`qq-group`/`qq-private`/`bili`/`pet`/`desktop`；
- 输出：一段有界、可冻结的人格文本，供既有 K01 系统前缀使用；
- 额外：B 站注入前 `sanitize`（剥离 QQ 号/昵称段）、统一卡 banner、角色包 epoch 原子切换。

不做：动机/情绪引擎（K15 另列）、记忆衰减（已完成）、自动外联、跨 Agent 人格共享、运行时任意 prompt 拼接。

## 2. 参考语义（固定提交）

参考 `xuexi/surfcore` 固定提交 `59ec3066f7d42a0452fae01f170c9a8484d5dbe5`：

- `composePersonaLayers`：identity-base + core + tone + scene + daily；scene 按渠道；预算裁剪 daily→scene→保护 core；
- `roles/<name>.md` 角色卡：persona vs simulation 两层，`selectRoleText` 一代/二代模式过滤；
- `sanitizePersonaForBili`：剥离 QQ 号/昵称段，注入 banner「QQ 和 B 站是同一个人」；pet 同卡；
- `character-persona.js`：角色包 persona + 衰减/全量记忆 + B 站账本，`epoch` 原子隔离切换。

V2 只继承组合/裁剪/sanitize/epoch 语义，不复制参考文件格式/角色卡解析/供应商行为。

## 3. 待所有者决策（D0–D5）

| ID | 决策 | 选项 | 不决策时 |
|---|---|---|---|
| D0 | 层存储 | 扩展 `settings` JSON 字段（需 Agent 设置 schema/UI）／角色卡文件包（owner 目录）／新表 | 不实现，保持单 persona 串 |
| D1 | scene 来源 | 仅 QQ（既有 qq_run scene）／QQ+B站+pet／按会话显式标签 | 仅 QQ，其他渠道用 core |
| D2 | 预算裁剪 | 固定字符上限（如 identity 8k/core 8k/tone 2k/scene 4k/daily 4k）／比例 | 未定不裁剪，只做组合 |
| D3 | 注入点 | `run_settings` 组合成 `settings["persona"]`（零 metering 改动）／`memory_system` 组合（触及冻结前缀） | 不实现 |
| D4 | B 站 sanitize | 剥离 QQ 号/昵称/群号+固定 banner／仅剥离凭据类 | 不注入 B 站，仅 QQ |
| D5 | 角色包 epoch | 新 `persona_epoch` 表/列 + 原子切换（需迁移）／仅配置 revision CAS | 不做角色包切换 |

**D0/D3 未决不实现**；D5 未决不新增表/迁移；D4 未决不做 B 站人格注入。

## 4. 纯逻辑合同

### 4.1 层与优先级

- 层文本均为 owner 提供的**不可信数据**：不执行、不解析命令/模板、不拼接任意 URL/凭据；控制字节/孤立代理项拒绝。
- 组合顺序固定：`identity-base` → `core` → `tone` → `scene` → `daily`；空层跳过；层间分隔符固定，避免同义拼接歧义。
- `core` 永不因预算被裁；裁剪顺序 **daily → scene → tone**，最后才裁 identity-base 尾部；每层输出带固定截断标记，不静默丢核心。
- 组合结果 ≤ `PERSONA_MAX_CHARS`（建议 24,000，低于 settings persona 50,000 上限）；超限按上述顺序截断并记录 `persona_trimmed` 审计元数据（层名/长度，不记正文）。

### 4.2 scene 选择

- QQ：复用既有 `qq_run.scene`（`qq-private`/`qq-group`），未知 scene 回退 core；
- B 站：`bili`，仅 D4 批准后注入 sanitized 文本；
- pet/desktop：显式请求时使用对应 scene，否则 core；
- **run 内冻结**：`settings["persona"]` 在 run 起点确定后整轮不变（与 K01 一致）；跨 run scene 变化产生不同前缀，**必须**在 K01 缓存键中体现（见 D3/缓存交互），不得让同 session 两个 scene 误共享前缀缓存。

### 4.3 渠道 sanitize（B 站）

- 输入输出均为纯字符串；规则固定：
  - 删除 QQ 号数字段、QQ 昵称段、群号、`@` 账号段、`http://`/`https://` 外部链接；
  - 保留 owner 声明的人格/价值观/兴趣文本；
  - 追加固定 banner「QQ 和 B 站是同一个人」；
- sanitize 后再次执行预算裁剪与字符校验；无法安全 sanitize（如出现疑似凭据/密钥模式）→ 拒绝注入，回退 core，记录 `persona_sanitize_denied` 审计，不发送原文。

### 4.4 角色包 epoch（D5 批准后）

- 角色包 = persona 层 + 只读记忆快照引用 + B 站账本引用；不复制记忆正文。
- epoch 切换为**单事务**：新 epoch 行 + 当前指针 CAS；旧 epoch 只读归档，不参与注入；
- run 起点捕获 epoch；run 内不切换；切换后新 run 使用新 epoch；失败回滚，不半切换；
- 擦除/保留边界：epoch 只存引用，来源擦除仍按既有屏障。

## 5. 与 K01 缓存/冻结前缀的交互

- 若 D3 选择 `run_settings` 组合：`memory_system`/`history_request` 零改动，`settings["persona"]` 已是组合串；K01 D3(b) 的缓存键当前为 `(agent, session, content_epoch)`，**同一 session 跨 scene 会产生不同前缀**。实现前必须二选一：
  - scene 在 session 内固定（同一 session 只属于一个渠道 scene）；或
  - 缓存键扩展为 `(agent, session, content_epoch, scene_hash)`（K01 合同变更，需独立批准）。
- 若 D3 选择 `memory_system` 组合：直接触及冻结前缀，按 K01 D3(a)/(c) 走所有者批准，不与 D3(b) 混批。
- 无论哪种，**不得**让不同 scene 的前缀字节共享同一缓存桶而伪造命中。

## 6. 测试计划（实现时）

1. 纯组合：层序/空层/分隔符/控制字节/超长裁剪顺序（daily→scene→tone→identity/core 保留）。
2. scene：QQ private/group、B 站、pet、desktop、未知回退；run 内冻结。
3. sanitize：QQ 号/昵称/群号/链接剥离、banner、凭据模式拒绝、裁剪后校验。
4. epoch（D5）：CAS 切换、事务回滚、旧 epoch 只读、run 捕获、擦除引用。
5. 缓存交互：同 session 不同 scene 的键/前缀断言（按选定方案）。
6. 回退：摘组合/sanitize/epoch → 新增用例红；恢复后绿。
7. 调用方：QQ/B 站/people/settings/context/model_calls/strict/ruff。

## 7. 生产接线点与阻塞

- `composition/instance.py`、`application/engine.py`、`adapters/http.py`、`adapters/persistence/schema.py` 当前有他会话未提交修改；本批不触碰。
- 若 D0 选择新表/新设置字段：需冻结迁移 + head 同步 + 表数 pin；若仅纯组合，可先零迁移实现。
- 真实 QQ/B 站账号注入质量、跨平台身份对齐（`alignments.json`）另验，不在本设计批。

## 8. 不做与口径

- 不做动机/情绪引擎、记忆衰减、自动外联、双卡分离、身份对齐 self-anchor。
- 不宣称分层人格/跨渠道统一卡对等；`whole_core_parity_complete`/`whole_v2_complete` 保持 false。
