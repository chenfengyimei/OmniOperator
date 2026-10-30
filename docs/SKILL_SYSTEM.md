# 技能系统与设置中心「技能」

> 技能是**程序性记忆**：事实回答“知道什么”，技能回答“知道怎么做”。
> 存储、linter、CAS、修订历史与外来包隔离由 `memory/skills.py`、`memory/skill_inbox.py` 唯一持有；
> 工作台与 agent 工具都只是它们的前端。设置中心「技能」页是用户视角的统一入口。

## 1. 数据模型与边界

技能 = `skills/<name>/SKILL.md` + 可选 `references/` 文本：

```
---
name: bilibili-watch
description: 看B站视频的标准流程：信息→字幕→热评→结构化看后感
version: 3
platforms: [windows, macos]
---

## When to Use
## Procedure
## Pitfalls
## Verification
```

- 名字：3–40 位小写字母/数字/连字符，目录名是权威；frontmatter 的 `name` 被外部改坏时，
  下一次写入会自动修回目录名，统计页也会提示“名字与目录不一致”。
- 描述：非空、≤60 字；正文 50–24000 字（常量在 `memory/skills.py`）。
- 四节是稳定结构：`patch` 只替换其中一节；格式坏掉的技能仍会出现在统计页（`valid=false`），
  可归档或交给用户/agent 重写，不会被静默隐藏。
- `references/` 只允许 `.md/.txt/.rst/.json/.yaml/.toml/.csv` 等文本，单文件 ≤64 KiB、
  最多 60 个；绝对路径、`..`、盘符、ADS、Windows 保留名、符号链接/junction 一律拒绝。
- 技能内容**不改变权限**：它不会提升风险等级、不会绕过审批；模型读到的仍是“数据”，
  不是可执行指令。

## 2. 生命周期

| 动作 | 入口 | 语义 |
|---|---|---|
| 列表 / 查看 | `skills_list` / `skill_view` | 渐进披露：列表只给名字+描述，查看才展开全文或 `references/` |
| 创建 | `skill_create` / 设置中心「新建技能」 | 过 linter；用户创建 `author=user, source=workbench` |
| 按节修订 | `skill_patch` / 编辑器全文保存 | 只改一节；全文保存走 `save_raw`，仍重新解析并过 linter |
| 全文重写 | `skill_rewrite` | 结构级变化；版本号 +1 |
| 历史 / 差异 / 回滚 | `skill_history` / `skill_rollback` / 设置中心 | 回滚产生**新修订**，版本号继续加，旧版仍可取回 |
| 归档 | `skill_archive` / 设置中心「归档」 | 移出技能库与 `skills_list`，内容移到 `.archive/<name>/`，历史保留；可恢复 |
| 恢复 | `skill_restore` / 设置中心「恢复」 | 移回技能库；同名技能已存在时拒绝覆盖 |
| 待审阅 | `/api/skills/pending` + 设置中心 / 技能页横幅 | agent/review 自动写的修订；可看差异、回滚、标记已读 |

写入纪律（D08）：

- 整段读改写持跨进程 `FileLock`（`skills/.locks/`），落盘走唯一临时文件 + 原子替换；
- `expected_hash` 是内容指纹 CAS（≥8 位前缀即可）；读过之后别人改过就拒绝，不会静默覆盖；
- 每次真实写入前把磁盘现状纳入修订历史（外部编辑也留痕）；历史只保留最近
  `skills.history_keep` 条，是“能回滚”而不是永久归档；
- 附件清单（路径→sha256）随修订保存，`reference_drift` 能说清“附件是否跟着这一版”。

## 3. 待审阅队列（可审阅，不是门禁）

- 判定：`author` 不是 `user`（工作台编辑）也不是 `external`（外部改动快照），且修订号大于
  该技能的已读标记；
- 已读标记存 `state.db` 的 `skill_acks`，只前进不后退；**设置中心归档路径**会清掉同名技能的
  旧标记，避免以后同名重建被旧标记吞掉；
- 这是**可审阅清单**：技能改动立即生效，不会改变工具风险等级或审批链。界面提供
  差异、回滚、全部已读；回滚走同一 CAS/历史路径。

## 4. 外来技能包

流程：上传/放入收件箱 → 导入隔离区 → 用户看清单 → 人工确认激活。

- 收件箱：`skills/.inbox/`，工作台可上传裸 `.zip`（≤4 MiB），也可直接放文件；
- 导入：`SkillInbox.import_package` 读中央目录，先校验条目名（zip-slip/盘符/ADS/保留名/
  重名/数量/体积），再按类型白名单**数据化**落成 SKILL.md 与 references 文本；
  **绝不执行包内任何脚本**；
- 隔离区：`skills/.quarantine/<name>/`，对 `skills_list` 不可见；清单记录每个文件
  大小与 sha256；
- 激活：`CONFIRM + requires_human`（agent 工具层）或设置中心里用户显式确认；激活前
  重新校验结构、逐文件比对清单 sha256、与技能编辑共用同一把锁、锁内复核同名冲突，
  来源写入修订历史（`source=skill_import`）；
- 丢弃：只删隔离区，不碰正式技能库；收件箱移除只删未导入的 zip。

## 5. 设置中心「技能」页

导航：设置中心 → 技能。页面分五块：

1. **总览指标**：技能数、待审阅数、修订快照数、参考文件数、技能库体积（历史另计）、
   隔离区/收件箱数量、需要修复的格式坏技能；
2. **最近 14 天修订**：按修订 `created_at` 的本地日期聚合（含零值），附来源/作者分布；
3. **待审阅变更**：最近 20 条摘要，完整差异与回滚在既有审阅弹窗；
4. **技能库**：搜索、编辑（同一编辑器/CAS）、归档；已归档区可恢复；
5. **外来技能包**：上传、收件箱导入/移除、隔离区文件清单与激活/丢弃；
6. **技能设置**：`skills.history_keep`（5–200）与 `skills.max_write_chars`（200–24000，约束
   agent/ review 的学习写入；工作台手动编辑仍过 linter/历史但不受此限额），
   保存到 `config.yaml`，显示运行值/保存值，**重启相应入口后生效**。

统计接口只读：不执行技能、不调用模型、不写配置；页面刷新不会触发写入。
若后端仍是旧版本（`/api/skills/overview` 404、被当成技能名），页面会明确提示
“关闭工作台服务窗口后重新运行 start.bat”，而不是只显示“技能不存在”。
工作台右侧「技能」页仍保留快速列表、待审阅横幅与编辑器，并新增“技能中心”入口，
两处共用同一个存储与审阅 API，不产生两套数据。

## 6. HTTP 与工具入口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/skills` | 列表（含 `meta_name` / `name_matches`） |
| GET | `/api/skills/overview` | 只读总览：技能/归档/修订/附件/体积/活动/待审阅/收件箱/隔离区 |
| GET/PUT | `/api/skills/settings` | 技能设置快照；PUT 带 revision CAS |
| POST | `/api/skills` | 工作台新建技能（同一 linter/历史） |
| POST | `/api/skills/{name}/archive` | 归档（可带 `expected_hash`） |
| POST | `/api/skills/archive/{name}/restore` | 从归档恢复 |
| GET | `/api/skills/inbox` | 收件箱 + 隔离区清单 |
| POST | `/api/skills/inbox/upload?filename=` | 裸 zip 字节上传到收件箱（≤4 MiB） |
| POST | `/api/skills/inbox/{package}/import` | 导入隔离区 |
| DELETE | `/api/skills/inbox/{package}` | 移除未导入 zip |
| POST | `/api/skills/quarantine/{name}/activate` | 激活（校验/防篡改/同名冲突） |
| POST | `/api/skills/quarantine/{name}/discard` | 丢弃隔离区内容 |
| GET | `/api/skills/pending`、`/pending/{name}/diff` | 待审阅与单条差异 |
| POST | `/api/skills/{name}/ack`、`/ack-all` | 已读标记（只前进） |
| GET | `/api/skills/{name}` / `/history` / `/revision/{rev}` / `/diff` | 编辑器、历史、差异 |
| POST | `/api/skills/{name}/save` / `/rollback` | 全文保存/回滚（CAS） |

agent 工具：`skills_list`、`skill_view`、`skill_create`、`skill_patch`、`skill_rewrite`、
`skill_history`、`skill_rollback`、`skill_archive`（CONFIRM+人工确认）、`skill_restore`；
外来包工具：`skill_import`、`skill_pending`、`skill_activate`（CONFIRM+人工确认）、
`skill_discard`。review 注册表刻意不注册归档/外来包工具——自动整理可以沉淀/修订技能，
但不能替用户归档或激活外来包。

## 7. 安全与如实边界

- 技能是数据，不是代码；附件永不执行；外来包只落文本。
- 归档不是硬删除：`.archive/` 与历史仍可能包含敏感流程文本；备份会按普通文件处理。
- 统计中的体积是文件字节数，不是计费或压缩后大小；历史受 `history_keep` 限制。
- 单个技能正文 ≤24 KiB、附件 ≤64 KiB；这些是稳定契约，不在设置页开放。
- 工作台编辑是用户显式操作；agent 的创建/改写/回滚仍共用 `MemoryGuard` 的每日写入额度，
  `skills.max_write_chars` 只约束这条学习写入路径。
- 真实外来包来源、跨机器包格式兼容性与用户审阅体验仍需实际使用验证；本页不声称
  “任何技能包都安全”。

## 8. 开发入口与验证

- 存储：`memory/skills.py`（`statistics`、`archive`、`restore`、`save_raw`、`rollback`）；
- 隔离安装：`memory/skill_inbox.py`（`upload`、`import_package`、`pending`、`activate`、`discard`）；
- tool 面：`tools/skills/tools.py`；组装：`bootstrap.py`（`Runtime.skills` / `Runtime.skill_inbox`）；
- HTTP：`channels/web_api/skills.py`；配置白名单：`system/config_io.py`；
- 前端：`web_static/ui/app/hub-skills.js`（设置中心域）、`ui/app/skills.js`（右栏快速页）；
- 测试：`tests/test_skill_center.py`（统计只读、设置 CAS、归档恢复、上传/导入/激活/丢弃、
  鉴权与 390/760/1024/1440px 真实 Chromium），既有 `tests/test_skills*.py`、
  `tests/test_skill_inbox.py`。
