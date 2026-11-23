# macOS 候选交付状态（2026-11-23）

基线 `b29409d0`；任务分支 `macos-release-closeout`；本机 macOS 27.2.1（26B131）arm64、Swift 6.4.2、SDK 27.2、Python 3.12.16。

## 产物

- Wheel：`temp/build/wheel/omnioperator-0.1.0-py3-none-any.whl`，SHA256 `d478d0e6ff365ba8d62fb9fdbc698c62cc18acc4290d9dae03e4b29e20c6e136`
- Helper：`temp/build/mac-native-release/OmniOperator Desktop Helper.app`，可执行 SHA256 `1ab02d9c3e327882a59591384561383959b761d8be58de49c3150bf690fdd99f`（ad hoc）
- 安装说明：`V2_MACOS_RELEASE_INSTALL.md`；证据：`docs/evidence/v2-macos-release-candidate-2026-11-23.json`

## 真机已通过

- 隔离安装、启动、工作台 200、owner 认证（401/200）、创建 Agent、重启恢复（同 instance）
- 桌面：读（9 节点 + SCK）、语义 value_matches、前台 text_appended（旧 helper `9360128b`，TCC 已授权）
- Keychain：写入/读回/删除通过
- 用户域服务：install succeeded → registered → bootstrap → loaded → ready → bootout → uninstall succeeded → absent，无残留
- 升级/回滚：旧 build `d9da1ec1` ⇄ 候选 `de6ebae2`，数据/实例保留，备份与清理完成
- 专项：macOS 11 文件 242/242（27.54s）、终端 10/10（0.30s）
- 72h 采样器：冒烟通过；正式长跑运行中（60s 间隔，当前        7 个采样，全 ok）

## 阻塞/缺口（需所有者或 A）

1. **候选 helper TCC**：替换后四项探针全 denied（TCC 绑定签名/路径），需在系统设置允许辅助功能/输入监控/屏幕；旧 helper 已恢复，四项重新 granted。
2. **系统域（LaunchDaemon）**：bundle valid、只读 absent 通过；安装被 `service_registration_target_unimplemented` 失败关闭，未写 /Library；是否实现由 A 定。
3. **Developer ID/公证**：ad hoc，spctl 拒绝；需要证书/Apple ID 与公证凭据。
4. **模型任务**：隔离 home 无 provider key。
5. **锁屏/睡眠/快速用户切换**：需所有者配合的桌面状态切换。
6. **第二台 Mac**、72h 完整窗口：未完成。

## 下一步

- 所有者允许 helper TCC 后：替换候选 helper、复跑桌面四项、记录权限撤回/恢复。
- A 决定系统域是否实现；否则按“用户域闭环 + 系统域失败关闭”交付。
- 72h 结束后运行 `v2_soak.py report` 并归档；按最终交付形态补签名/公证。
