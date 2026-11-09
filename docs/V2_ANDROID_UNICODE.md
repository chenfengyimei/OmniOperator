# V2 安卓受控 Unicode 输入

本批接入 `append_unicode`、独立 Android 输入辅助应用、输入法恢复义务与工作台。它属于 D5 选定的安卓输入范围；设备验收尚未完成，默认入口仍为 V1，完整 V2 尚未交付。证据以 [本批记录](evidence/v2-android-unicode.json) 为准。

## 使用与能力边界

辅助应用包名为 `org.omnioperator.inputhelper`，组件为 `org.omnioperator.inputhelper/.HelperIme`，协议版本 1，最低 Android 11/API 30，构建目标 API 35。它没有网络权限、输入广播接收器或剪贴板输入路径。安装和启用输入法由所有者在设备上完成，宿主不会执行安装、自动启用或配对。

1. 使用明确的本地 SDK/JDK 构建并签名，审阅 `build.json` 中的 APK 和签名证书 SHA-256。
2. 在指定测试设备上手动安装、启用辅助输入法；不把“已启用”当作本次授权。
3. 在设备应用中显式生成/更换配对密钥，通过工作台「实例凭据」保存；配置页仅填写凭据引用与 APK/证书指纹。
4. 在设备应用中允许本次使用，最长 30 分钟。暂停、更换密钥、应用进程重启和超期都会阻止输入。
5. 重新观察已授权设备，选择当前聚焦的文本框，提交 Unicode 追加动作，再在统一审批区核对目标和正文。模型仍需先审批本次运行的应用/动作范围，再审批具体输入。

只支持当前应用内具有可核验资源 ID、非密码的普通文本编辑器。光标必须折叠且在完整文本末尾，文本上限 4096 个 UTF-16 单元；观察正文截断或编辑器不提供完整提取时拒绝。每次追加最多 256 个 Unicode 码点，不接收控制字符、孤立代理项或隐式换行。它不执行提交、回车或任意编辑器 action。

应用 UID、Android 用户、包名、资源、输入连接、输入会话代次、原文指纹、选区和授权共同校验。准备令牌只使用一次，3 秒失效。提交后重新读取完整文本和选区；不能确认时为 `unknown`。

当前编辑器的文本、选区、输入会话、可观察按键或辅助界面触摸变化会撤销令牌。**没有系统全局输入监听能力**，不能描述为全设备键鼠接管；自绘、工作资料及无法证明身份的编辑器拒绝。设备厂商和实际 Android 行为需另行验收。

## 宿主、辅助进程与密钥

主进程不调用 Android API。受管 Python 进程通过已有本机 ADB smart socket 选择固定 transport，再访问 `localabstract:org.omnioperator.inputhelper.v1.u<user>`；不执行 `adb forward` 或启动 ADB 服务。原生 IME 的本地 socket 核验内核 peer UID 必须为 shell/2000，root adbd 不放行。

输入前通过固定 `pm path --user` 与 `sha256sum` 核验已安装 APK。配对协议采用长度帧和方向分离的 HMAC-SHA256，绑定新 nonce、本次动作及运行上下文；响应核验设备用户和签名证书。帧上限 16 KiB、单连接至多 4 帧、socket 等待 5 秒；一次原生编辑器调用最多等待 2.5 秒。迟到提交仍按未知处理，排队取消不能稍后开始写入。

密钥由设备生成，使用 AndroidKeyStore AES-GCM 加密后存于应用私有配置。宿主只持久保存凭据引用，通过现有凭据端口读取；实际值仅经子进程 stdin 和认证 socket 使用，不进入参数列表、环境变量、业务配置、日志、指标或 APK 构建记录。凭据代次在每个派发前重查；观察不返回凭据引用。

## 输入法切换与持久恢复

切换输入法属于外部写入，由同一次明确批准覆盖：

1. 重新观察原目标；核验 APK、已启用输入法集合、原默认 IME 及 subtype。
2. 在数据库原子登记恢复义务及元数据审计，成功后才允许 `ime set` 临时选择已启用的辅助组件。
3. 核验辅助编辑器，宿主再次核验审批、范围、配置、凭据和目标后提交一次追加。
4. 回收连接并恢复原 IME/subtype，读取实际配置核验；恢复结论持久写入成功后才能返回完整成功。

正常结束由辅助应用尝试公开 API 恢复原 subtype；宿主仅在当前仍是辅助 IME、启用集合未变且原 IME 仍启用时，允许固定的原 IME 选择。无法精确恢复 subtype 时保持待处理，不修改安全设置伪造验证。

切换或提交结果未知时先回收原工作进程，后续新进程只读环境，**不自动重放输入或不确定的切换**。输入法恢复与输入结果独立：即使环境已恢复，原输入的 `unknown` 仍要求独立核查。恢复义务未结清时，所有 Agent 对相同设备的后续输入均被阻止。

调用者取消等待、运行撤权或关闭不会丢弃实际清理归属。原批准捕获的恢复义务允许完成有限清理；不再允许原正文输入。进程完全回收前保留工作槽和资源租约。重启保留待恢复环境及未知动作。

所有者可针对环境 ID、revision 和归属会话准备 `android_restore_ime`，仍需统一审批。恢复使用原记录的 ADB 地址、boot/transport/用户身份及原 IME，即使根设备授权已撤销，也不需要重新授予文本输入。用户改选第三个输入法或启用集合变化时不覆盖。历史恢复动作未核查时阻止再次派发；恢复记录变化要求重新准备。也可在设备手动恢复后提交相同核查流程，只读成立时不再切换。

## 存储与接口

`v2_0031` 后共 108 张业务表：

- `android_unicode_profiles`：按设备授权追加配置修订；唯一 revision、单步递增、不可改写或删除，包含凭据引用与指纹。
- `android_unicode_environments`：每个输入动作最多一条恢复义务，记录原 IME/subtype、启用集合指纹、配置修订、设备身份来源和恢复观察。组合外键、触发器约束同 Agent、授权和正在执行的批准动作，原始捕获不可修改。

输入正文、审批、动作结果、取消及未知核查仍属于共享 `owner_commands → actions` 或模型动作账本；恢复表不是第二套输入结果。迁移重建动作范围及 owner 命令约束时恢复原不可变和来源触发器，不改写旧迁移文件。

接口均需所有者认证：

| 接口 | 合同 |
|---|---|
| `GET /api/v1/agents/{agent}/android/grants/{grant}/unicode` | 当前配置或 revision 0 的未配置状态 |
| `PUT .../grants/{grant}/unicode` | 精确 `revision, profile`；profile 只允许 enabled/credential_ref/apk_sha256/certificate_sha256 |
| `GET .../android/environments` | UUID 游标，limit 1–100，所有者恢复记录 |
| `POST .../android/environments/{id}/restore` | consent=true、session_id、request_id、revision；仅排队与请求审批 |
| 原 `POST .../android/actions` | append_unicode 的 params 必须为 helper_revision、restore_ime=true |

工作台按 Agent/设备保留配置草稿；保存不宣称可用。观察报告 `configured_not_probed` 及限制，输入前才实际核验。离开清除短期观察与截图，后端恢复义务继续保留。所有设备文本使用 textContent 渲染。

## 构建与验证

显式使用 SDK API 35、build-tools 35.0.0 和 JDK 17+，不依赖 Gradle、全局环境变量或联网下载：

```powershell
uv run --no-sync python agent/scripts/check_android_helper.py --java-home <JDK目录>
uv run --no-sync python agent/scripts/build_android_helper.py --sdk <SDK目录> --output <新产物目录> --java-home <JDK目录>
```

默认构建未签名、不可直接安装。使用具有相同库/私钥密码的 PKCS12 时，显式增加 `--keystore <文件> --password-file <外部密码文件> --alias <别名>`；脚本校验签名并写入 APK/证书指纹。密码不放在 argv 中，已有产物不覆盖。源码 Java/XML 纳入 V2 运行及部署指纹。

本批开发签名产物为 `temp/build/v2-android-unicode-development-apk/omni-input-helper.apk`，配套 `build.json`；开发私钥留在 Git 忽略且访问受限的 `temp/private` 中，**源码恢复包不包含它**。开发签名不等于发布签名或设备验收。

离线验证覆盖实际受管 Python、SQLite、假 ADB/IME、双重模型审批、指纹/密钥变化、编辑器变化、丢失回执、恢复失败、撤权、等待者取消、重启、持久化失败、恢复不覆盖用户设置、HTTP 和四视口双主题。JVM 另检查与 APK 相同的纯编辑器核心。不能由这些模拟结果推断真实设备已支持。

实机仍需核验 shell socket 身份、包可见性、启用与本次允许、KeyStore、系统生命周期、普通/密码/自绘编辑器、中文/emoji、IME/subtype、锁屏/切换用户、连接中断和服务回收。真实设备安装和动作另行明确安排。

原生合同参考 [InputMethodService](https://developer.android.com/reference/android/inputmethodservice/InputMethodService)、[InputConnection](https://developer.android.com/reference/android/view/inputmethod/InputConnection)、[LocalSocket](https://developer.android.com/reference/android/net/LocalSocket) 和 [Android Keystore](https://developer.android.com/privacy-and-security/keystore)。
