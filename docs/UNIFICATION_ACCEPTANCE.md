# 统一版本验收与恢复手册

本轮实现和离线证据见[任务清单](tasks/unification.json)与[验证记录](evidence/unification-offline.json)。
历史会话中的真机结果属于各自版本，不能作为本轮真实验收证据。

## 1. 固定候选版本

主工作区仍保留集成前的未提交成果；恢复副本在`temp/private/unification-baseline-20261101T154256Z/`，
包含原始文件ZIP、暂存/工作差异和逐文件SHA-256。不要批量暂存或丢弃不明修改。
本轮运行资源和代码以验证记录的源码指纹识别；正式72小时验收前需冻结审核后的主程序提交及配置。
官网、市场的独立提交记录在`docs/tasks/asset-repositories.json`。本地建立仓库不代表已部署。

候选wheel生成并核验：

```powershell
uv run --no-sync python scripts/check_package.py
```

输出在`temp/build/packaging/`：直接wheel、sdist→wheel、资源指纹和隔离导入证据。
源码ZIP发行包仍遵循`docs/UPDATES.md`的干净提交门槛；不从混合在途工作区制作正式发行版。

## 2. 工作台操作

启动使用`start.bat`，成功必须同时匹配健康响应、启动身份、PID和本次配对链接。
工具按钮和设置侧栏使用同一分区注册表；旧独立URL转到对应分区。未知分区会提示错误。
终端、安卓和存储的内部`?embed=1`地址保留。切换分区保留草稿，首次加载完成后才允许保存。
关闭后台任务面板停止界面轮询；关闭终端分区释放观察连接，后台任务本身不随面板关闭而重放。

人设「导入」保存预设并填入编辑草稿，「应用预设」只填草稿，「保存人设」写配置；
运行中的人设、桌面与视觉配置需要重启，界面会提示。旧浏览器预设首次读取时迁入服务端，
同ID以服务端版本为准；失败保留原浏览器来源，成功后才清理。多页面同时编辑时旧revision会被拒绝。

截图事件区分登记、附加到模型请求、未附加及原因；附加成功也不代表模型已经理解图片。
默认关闭云端附图与前台接管，可在「浏览器→截图与桌面」设置。只配置目标应用完整exe路径。
前台接管逐次人工确认，绑定run与窗口，用户输入/焦点漂移时暂停，unknown禁止自动重试。

## 3. 可恢复归档

```powershell
uv run --no-sync python scripts/archive_project.py plan <明确选择的相对路径> --category private --reason "归档依据"
uv run --no-sync python scripts/archive_project.py apply <上一步输出的manifest.json>
uv run --no-sync python scripts/archive_project.py restore <同一个manifest.json>
```

`archive`放退役源码/文档，`private`放私人记录/快照，`build`放可再生成产物。
apply前复核引用、路径、指纹和文件占用；中断后可以再次apply，restore不覆盖不同内容。
SQLite仅作一致快照并保留原库；恢复只补回已不存在且无WAL/SHM的原位置，活跃库走独立恢复流程。
manifest必须随归档一起保留。不要移动单独的WAL/SHM，也不要把用户无关文件作为清理目标。

已归档三张调试截图、两个已迁移的空cron JSON和两个干净的旧验收Git工作树。
工作树恢复按`temp/private/legacy-worktrees-20261102/manifest.json`的git worktree move命令执行。
`data/MEMORY.md`的两条旧条目只有一条与人物系统当前内容完全一致，因此继续保留作迁移来源。
两个活跃数据库、config/.env/skills、兼容联接和仍被引用的原型继续保留。

## 4. 真实验收清单（均待执行）

以下目标需所有者确认后执行；不会由默认pytest调用。

| 项目 | 具体步骤与放行证据 |
|---|---|
| 视觉与档位 | 指定供应商/模型及总费用上限；临时中文截图一张，验证附图、拒绝原因、压缩后引用；同一合成任务比较各档费用与完成结果 |
| QQ | 所有者扫码登录，指定一个许可私聊接收者；仅发「OmniOperator 统一验收：单条回执」；验证冷却、掉线重连、未知投递不重发与定时链单次执行 |
| B站 | 指定本人测试视频/私信目标、账号UID、完整正文和mention UID；复核页面用户组件选择器、候选UID与最终组件UID；逐次人工批准，新增发布记录才算成功 |
| 安卓 | 仅指定测试设备和允许应用，在合成页面观察→锁帧→单次动作→回读；检查旧观察、切设备和断连拒绝 |
| 混合检索 | 仅合成事实；授权embedding后验证语义命中、后台不计hits、遗忘后不可再召回及来源指针 |
| 远程MCP | 指定受信HTTPS端点与认证来源，工作台手动授权；核验PKCE、目录确认、只读许可、漂移撤权、超时未知及凭据重载 |
| Windows | 只在新建记事本临时文件及文件资源管理器临时目录操作；确认run/窗口预览，主动输入验证暂停；核验回读、重复观察拒绝及第二目标应用 |
| 升级与恢复 | 先用真实数据的一致副本演练；确认维护窗口后再执行真实服务重启/固定提交升级；核验中断恢复、账本/额度/未知投递及新界面完整性 |

B站未配置真实mention组件时拒绝带UID名单的草稿，不把纯`@昵称`文本当成真实通知。
已批准的真实写入出现unknown时先到目标应用核查，不能为了拿到成功结果再次发送。

## 5. 72小时常驻

先确认固定源码提交、三仓版本、真实配置指纹、服务范围和资源/费用上限。
`docs/evidence/unification-soak-limits.example.json`仅为待审核样例；采样器只记录，不替代模型费用硬预算。
确认后用独立PowerShell运行：

```powershell
uv run --no-sync python scripts/soak_monitor.py run --hours 72 --interval 60 --limits docs/evidence/unification-soak-limits.example.json --output temp/private/unification-soak
uv run --no-sync python scripts/soak_monitor.py report --output temp/private/unification-soak
```

记录资源趋势、重连/恢复、任务链和人工介入。源码或配置变化后重新固定样本，不能拼接不同版本的
时长。短跑报告不满足72小时；缺账号、设备、授权或完整时长时保持「待真实验收」。
公开部署、发行包发布和线上更新分别确认目标、版本及恢复方案后执行。
