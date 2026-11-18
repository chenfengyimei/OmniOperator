# Windows V2 专项实机验收记录（2026-11-18）

> 后续复核：下述点击缺陷诊断已收窄为验收样例问题。STATIC 顶层窗口导致 UIA 跨进程命中，原生产拒绝正确；换标准窗口类后，点击点及真实遮挡拒绝专项通过。人工聚焦后的完整前台回归也已通过，文本与按钮计数符合预期，但四次生产动作回执均保留 unknown。生产 C# 未改，矩形中心候选未保留。见[后续证据](../../../evidence/v2-windows-click-fixture.json)。以下保留原固定候选记录及失败，不将后续结果倒写为旧候选通过。

> 结论先行：**本轮在固定源码 `1e62fe08` 上完成 Windows 专项实机验收**。原生读取、终端 ConPTY、
> 安装/备份恢复/续跑卸载、用户业务链（含**真实离线 ASR** 与**真实 PyAV 抽帧**）、前台 `type_text`/`press_key`
> 均**原生实机验证通过**；前台 `click` 发现**真实缺陷**（原生 Win32 按钮被永久拒绝），已精确诊断，
> 按所有者决定**交开发会话作为新候选修复**，本轮保持当前候选证据完整。
> 72 小时长跑与真正干净机器**准备就绪、待执行**。本文件**不构成「整个 V2 完成」或生产发布就绪**结论。

## 0. 验收身份与基线

| 项 | 值 |
| --- | --- |
| 验收源码 commit | `1e62fe08055492e819ee7cfaeabdbc6b77604dd3`（分支 `v2`，冻结工作树 `C:\v2-acceptance\source`） |
| 源码指纹（测试修复后） | `0cca300342ed5ac07a4b583c15a7db17b903184a3c268b6b212aa1284ab97a81`（1467 文件） |
| 源码指纹（测试修复前） | `e0f31c762dba0563f1179c9117449e03f1feb432360178753fe0ceecae49ef4c` |
| 本轮唯一源码改动 | `agent/tests/test_v2_installation_launchers.py`：`shell()` 沿 git 祖先目录查找 `bin/sh.exe`（修复 Git 布局 `D:\Git\mingw64\bin\git.EXE` 导致 sh 定位错误）。**仅测试文件，wheel 字节不变** |
| wheel SHA256 | `a43eadd3652f1cd347cac864f63c01a15d7d86cb3abdbff8f2a5cae74eeae3d0` |
| 依赖 bundle manifest SHA256 | `ddc242906492a815e1893505f0453f922f2c218dda441ad0088effb9f5e1225f`（110 wheel，extras terminal/asr/vectors；与当前 lock 导出逐项一致，复用已验证字节） |
| 测试解释器 | `D:\xm\OmniOperator\.venv\Scripts\python.exe` + `PYTHONPATH=C:\v2-acceptance\source\agent\src`（omni 从固定源码导入，优先于 editable） |
| 运行环境 | Windows 11 10.0.26300 x64，Python 3.12.10，uv 0.12.7 |
| 当前主仓 HEAD | `a9fa9ef2…`（其他会话已推进；本验收固定于 `1e62fe08`，**不复用旧全量**） |
| 大产物位置 | `C:\v2-acceptance\win-accept-20261118`（D 盘已满，大产物置 C 盘；仓库内仅汇总 [证据](../../../evidence/v2-windows-acceptance-2026-11-18.json)） |

历史 5998 全量仅覆盖固定提交 `7e64d348`，近期 311 专项仅覆盖媒体上传批；**均不代表当前源码全量**。本轮按所有者要求**未自行启动全仓 pytest**。

## 1. 验收矩阵（四态区分）

| 项 | 功能实现 | 离线验证 | 原生实机 | 仍待验收 |
| --- | --- | --- | --- | --- |
| 原生读取（能力/目标/身份/UIA/截图/对象 + 拒绝路径） | ✅ | ✅ 106 | ✅ 自有窗口 12 | 真实他人应用/多会话长期 |
| 显式前台 `type_text`/`press_key` | ✅ | ✅ 36 | ✅ 真实输入验证 | — |
| 显式前台 `click` | ✅ | ✅ | ❌ **缺陷** | 开发会话修复（新候选） |
| 终端 ConPTY 与生命周期 | ✅ | ✅ 33 | ✅ 4 真实 ConPTY | 完整 UI 矩阵/三平台常驻 |
| 安装/启动/更新/回滚/备份恢复/保留数据卸载 | ✅ | ✅ 77 | ✅ 新目录演练 | 真正干净机器 |
| 用户业务链（上传音视频→ASR/抽帧→产物 + 备份恢复 + 重启） | ✅ | ✅ 39 | ✅ 真实 MediaProcessor | 真实 ASR 质量评估 |
| 72 小时长跑 | ✅ 准备 | ✅ 9 | 采样器冒烟 | 实际 72h（开发冻结后） |
| 真实账号/市场/MCP 供应商/模型质量/设备 | — | 注入 | ❌ | 另行正式验收 |
| macOS/Linux/容器 | — | — | — | 其他会话/未验 |

## 2. 原生读取（item2，实机）

- 真实固定 MTA PowerShell/.NET UIA 辅助进程对**本次自有、非激活 Win32 窗口**：`observe`（真实 UIA 树，密码控件及子树不采内容，128 节点/8 层/16KiB 截断）、`capture`（真实 PrintWindow 640×480 PNG，纯色拒绝）、`act`（set_text/select/invoke）均通过。
- 拒绝路径实机验证：`desktop_target_changed`（`process_created+1` 旧目标拒绝）；窗口销毁后相同 HWND/元数据不能恢复旧身份；`desktop_user_intervened`（执行期间真实键鼠输入→未派发拒绝）。
- **首次失败留档**：`…observation_native.py::…[act]` 首跑 `denied/desktop_user_intervened/not_dispatched`，因交互期间真实用户输入触发 `GetLastInputInfo` 守卫；输入静止重跑 `1 passed in 8.56s`。分类为**环境性输入敏感、fail-closed 正确**，非缺陷。
- 离线注入 106 通过（能力 supported/installed/permission/available 分离、Session0/非活动会话拒绝、视图/快照容量与代次、跨 Agent 隔离、撤权/取消/迟到结果不发布、unknown 不重放、审计无原生身份/正文）。

## 3. 显式前台（item3，实机交互 + 缺陷）

- 离线 36 通过：同意绑定 run/action/boot 级 grant、focus 等待中撤销 view 阻止原生 permit、worker 分帧/暂停/部分派发/清理（none/before/after/partial/hook_lost/cleanup）、UI takeover 同意 + 独立审批（390/1440 视口）。
- 实机交互 `agent/scripts/accept_foreground.py`（仅本次自有隔离窗口）：
  - `type_text ONLY-OWNED-TEXT` → **applied**（TextPattern 真实验证）；
  - `press_key Home` → **unknown**（按契约保留，不自动重放）；
  - `type_text PREFIX-` → **applied**（state=`PREFIX-ONLY-OWNED-TEXT`）；
  - `click Button` → **denied `desktop_permission_denied`**（真实缺陷，见 §8）。
- attempt1 的 `desktop_user_intervened` 为用户点击离派发太近，守卫正确 fail-closed，已归档为介入拒绝正面证据。

## 4. 终端 ConPTY 与生命周期（item4，实机）

- 真实 Windows ConPTY 同步管道 + cmd 壳 4 通过：Unicode 输入/resize/退出/Job 归属 verified cleanup；取消启动保留归属至实际门工作完成；授权拒绝 never 执行用户 shell；POSIX 注入辅助退出不证明会话回收。
- 离线 33 通过：单写者 15s 租约续期/过期/撤权/异 boot 拒绝；部分输入保持 unknown、回执查询绝不重发；重启恢复保留 PID 证据与 unknown 回执，不重开/不重放/不按历史 PID 回收（quarantine）；未知操作独立核查需原生静止门；v21/v25→v26 迁移中断回滚后从验证备份重试。

## 5. 安装/备份恢复/卸载（item5，实机新目录演练）

- `agent/scripts/check_v2_installation.py`：基础 Python（确认无 omni/sqlalchemy）→ 真实安装 6 阶段（base/install/manager/candidate/launcher/current）verified；安装链 artifact→stage→prepare→smoke→backup→rollback-check→migrate→switch（同 schema `v2_0039`）；真实 cmd 启动 `omni doctor` integrity ok，私有 sitecustomize 探针未执行，Unicode/空格/emoji/百分号/感叹号/shell 字符 home；维护 venv 与候选 venv 分离；`services_registered=false`。
- 备份/回滚 DB 均 148 表、`alembic_version=v2_0039`、`integrity_check=ok`（真实 restore 到新目录，含一致 WAL，非裸复制活动 DB）。
- 卸载：首次 `cleanup_pending=true`（Windows 文件短暂占用，与历史已知环境行为一致，非新缺陷）；外部维护解释器**同 SHA 续跑** `removed=true/data_kept=true/backup_kept`，RC=0；卸载后 `omni.db` SHA `272b98df…` 与备份 manifest SHA `ba40189d…` 均与卸载前一致；候选树/`omni.cmd`/`runtime/current.json` 移除。`first_attempt_clean_uninstall=false` 如实记录。
- 离线 77 通过（首跑 5 失败为 §0 的 launcher 测试定位缺陷，修复后 9 文件全过）。**真正干净机器待验**。

## 6. 用户业务链（item6，实机真实管线）

真实 `MediaProcessor`（PyAV stdio worker）+ 真实 SQLite（148 表，`v2_0039`），非 Fake：

- **真实抽帧**：合成 MP4 → 真实 PyAV worker → 4 帧拼图 PNG 640×520，产物对象读取一致。
- **真实离线 ASR**：Windows SAPI（Huihui zh-CN）合成语音 → 缓存 `Systran/faster-whisper-small`（`local_files_only=True`，不下载）→ 转写 `[00:00-00:03] 你好世界,这是一个离线语音测试。`（zh）。
- **备份恢复**：上传源/抽帧产物/转写文本与对象引用在 verified backup/restore 后完整存活，FK=0。
- **重启恢复**：原 home 重开后已完成作业保持 `done`，无自动重放，产物仍在，FK=0。
- **工作台 HTTP**：401 拒绝 / 201+`no-store` / 真实产物读取 / cancel→`cancelled` / 跨 agent 404 / processor probe 组件（av/numpy/PIL/faster_whisper）全 true。
- 离线 39 通过（上传作业图/迁移 18 + 上传 UI/worker 21，390/1024 视口）。真实 ASR **质量评估**仍待。

## 7. 72 小时长跑准备（item7，未启动实际长跑）

- 用 `agent/scripts/v2_soak.py` 机制（**非 V1 `soak_monitor.py`**）：原生采样器 `native_v2_hmac_http` + 时钟 `native_monotonic_wall`，实测基线 RSS 146MB/线程 18/句柄 417/子进程 0/cleanup 0。
- 校准 8 字段所有者上限 `soak-prep/limits.json`（223B≤4096）：main_rss +512MB、descendant_rss +1GB、threads +64、descriptors +512、children +16、cleanup_pending≤8、`new_failed_runs=0`、`new_unknown_model_calls=0`（unknown 绝不自动关闭）。
- 3 次采样冒烟仅 `duration_below_72h` 阻断（正确未通过），owner token 未泄漏入证据，记录 `collector_sha256` 供 resume。
- 采样方案：72h、interval 60s、约 4320 样本；resume 需同 origin+sampler+collector，间隙/倒钟/失败/计数回退均为阻断。离线 `test_v2_soak.py` 9 通过。**实际 72h 待整个 V2 开发冻结后执行**。

## 8. 发现的真实缺陷（交开发会话修复）

**前台 click 对原生 Win32 按钮永久拒绝**（`foreground-click-defect.json`）：

- 观察 [windows_uia.cs:52](../../../../agent/src/omni/adapters/workers/windows_uia.cs#L52) 对任何启用、非离屏、非密码控件**无条件** advertise `click`；
- 派发 [windows_foreground.cs:89](../../../../agent/src/omni/adapters/workers/windows_foreground.cs#L89) `ClickPoint` 要求 `TryGetClickablePoint==true`，否则 `Denied()`→`desktop_permission_denied`；
- 独立 UIA 探针证实标准 Win32 BUTTON `TryGetClickablePoint=False`（MSAA→UIA 桥接不暴露可点击点），矩形/虚拟屏幕边界均正常。于是整类原生按钮「看得到 click 却永远派发被拒」。
- **安全修复方案**：`TryGetClickablePoint` 失败/NaN 时回退包围矩形中心（宽高≥2 且有限），**保留** `FromPoint` 命中同节点 + `WindowFromPoint` 祖先同窗口核验（中心被遮挡仍 `Changed()` 拒绝）；不放宽身份/焦点/介入/边界闸门。
- **回归需求**：新增原生回归——legacy 按钮 click 成功 + 中心被遮挡仍拒绝。
- **候选影响**：改动 `windows_foreground.cs` 产生新候选，需重建 wheel、重跑安装/原生相关项、重新生成指纹。所有者决定**本轮不修**，保持当前候选 `1e62fe08`/wheel `a43eadd3` 证据完整，由开发会话作为新候选修复。

## 9. 本轮退出标准与剩余项

**Windows 专项本轮退出标准达成**：原生读取/终端 ConPTY/安装备份恢复续跑卸载/用户业务链（含真实 ASR 与抽帧）/前台 type_text 与 press_key 均实机通过；前台 click 真实缺陷已诊断并按所有者决定交开发会话；72h 与干净机器准备就绪待执行。

**整个 V2 剩余项**（不构成 Windows 本轮阻断）：前台 click 缺陷修复（新候选）、真正干净机器完整 install→update→rollback→uninstall、真实账号/市场/MCP 供应商、真实模型质量/设备、72 小时长跑（开发冻结后）、macOS/Linux/容器、当前源码全量 pytest（开发冻结后）。

## 10. 证据与复现

- 汇总机器可读证据：[evidence/v2-windows-acceptance-2026-11-18.json](../../../evidence/v2-windows-acceptance-2026-11-18.json)
- 本机全量产物：`C:\v2-acceptance\win-accept-20261118`（`baseline.json`、`source.json`、`item2…item6`、`soak-prep/`、`foreground-click-defect.json`、`fresh-install/report.json`、`foreground-acceptance-attempt1-intervened/`、`foreground-acceptance-attempt2-click-defect/`、`business/`）
- 专项命令统一 `uv run --no-sync pytest -q -n 0 agent/tests/test_<模块>.py`（本轮以固定工作树 + PYTHONPATH 等价执行）；保留首次失败与精确 nodeid 重跑，未跳过测试/缩短被测超时/放宽断言。
- 并行会话未提交修改（`PPT/`、`docs/competition/`、Linux/UI/MCP 在途）**未纳入**本候选或本轮专项。
