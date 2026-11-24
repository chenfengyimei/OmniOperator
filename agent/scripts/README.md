# Agent 开发工具

源码契约 `check_ui_contract.py`，项目索引 `project_index.py`，离线评测 `eval_core.py`，CI `ci.ps1`，升级演练/验收/采样及临时进程验证均在此维护。参数中的数据与证据目录继续显式注入。

Linux原生只读诊断使用`accept_linux_desktop.py --output <新目录>`；读取所有者打开的临时样例须同时提供`--fixture-pid`与`--consent-read-ui`。默认AT-SPI语义观察，显式增加`--native-backend x11 --capture-window`才读取该X11样例的像素，证据只记录摘要而不包含图像或节点正文，不执行输入。操作与限制见[Linux交接](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/platforms/linux/V2_LINUX_HANDOFF.md)，该工具的一次样例不代表完整平台支持。

测试入口为 `uv run --no-sync pytest -q`（默认最多 4 进程、按文件分发）；开发中对相关测试追加 `-n 0`。CI 支持 `ci.ps1 -Workers 4`，串行诊断使用 `-Workers 0`。规则与耗时对比见 [TESTING.md](../../docs/v/reference/TESTING.md)。


Wayland授权会话样例为`accept_linux_portal.py --consent-window-share --hold-seconds 1 --output <新私有目录>`。只在Linux隔离账号由验收者显式运行，会弹系统窗口选择器；仅检查授权持有、撤回和进程回收，不打开PipeWire FD、不读像素、不输入，不代表完整Portal或业务审计验收。ScreenCast版本、环境及证据要求见Linux交接；本机未执行原生样例。

Wayland样例增加独立`--capture-window`：读取一个显式选择的窗口单帧，经宿主验证后只保留回执，不把像素写入证据。默认授权样例仍不读图；需要desktop-wayland可选依赖和系统GStreamer/PipeWire组件。该脚本不替代工作台、业务审计或Linux平台验收。

原生系统凭据统一验收入口为`accept_native_credentials.py --write-owned-item --output <新私有目录>`。该命令会真实操作随机测试项，须在对应平台隔离账号由验收者显式运行；本轮未执行。清理身份先落盘，未知写入不重试或自动删除；后续通过`--cleanup <cleanup.json> --output <另一个新私有目录>`显式清理。它复用生产受管进程，结果不包含凭据值，不代表完整平台验收。

macOS 用户服务只读证据使用 `accept_macos_services.py --bundle <真实不可变安装的部署包> --expect inspection|absent|registered|loaded|ready --output <新的私密JSON>`。它不安装、不激活、不卸载、不申请权限，注册不等于加载或就绪。Swift/C、默认 ACL 证明与真实安装仍待 Mac 验证，步骤见[Mac交接](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/platforms/macos/V2_MACOS_HANDOFF.md)。

macOS 系统部署也可使用同一只读证据入口，支持 inspection/absent/loaded/ready，拒绝 registered 和交换计划；不代表管理员后端。当前 helper 查询协议2绑定域，签名前新增8项本地文件/ACL测试，Windows未执行原生测试。详见[系统前置](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/platforms/macos/V2_MACOS_SYSTEM_SERVICES.md)。

安卓受控输入源码使用`build_android_helper.py --sdk <API35 SDK目录> --java-home <JDK17目录> --output <新目录>`构建；默认未签名，可显式指定外部PKCS12/密码文件。`check_android_helper.py --java-home <JDK目录>`只在本地JVM检查APK中的纯编辑器核心，不使用ADB。构建、设备手动启用、凭据与恢复规则见[Unicode合同](https://gitee.com/chenfengloveyuri/OmniOperator/blob/3f6594ebd307dfd3d7f64be7b21c83a36b938fb5/docs/v2/contracts/V2_ANDROID_UNICODE.md)；编译或开发签名不等于设备验收。
