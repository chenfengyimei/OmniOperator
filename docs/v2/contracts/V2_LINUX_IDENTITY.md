# V2 Linux 身份证据合同（G：AT-SPI 地址 guid 与 display-manager Xorg）

状态：**已实现并专项通过（2026-11-23）**。本文件裁定 G3 集成中精确定位的两个身份阻塞点，并把证据链写成可回归的验收用例；不改共享接口、不新增迁移、不扩大授权面。

## 1. 范围与背景

G 分支在 Lima/Ubuntu 26.04（LightDM + openbox + Xorg/VKMS）实测时，V2 在两个位置失败关闭（`desktop_native_unavailable` 的根因之一）：

1. `org.a11y.Bus.GetAddress` 返回的地址里 `guid` 与连接该总线后 `GetId` 的返回值**不一致**；重启 at-spi 总线后仍不一致（`cb55a6bf…` vs `5ad915ce…`、`833ae62b…` vs `db808a07…`）。旧实现要求两者相等，故报 `desktop_bus_peer_unverified`。
2. LightDM 把 Xorg 放在 `system.slice/lightdm.service`，**没有 logind 会话**；旧 `server_session` 要求 `GetSessionByPID(Xorg)` 等于当前活动会话，故报 `desktop_x11_peer_unverified`。

两条都不是 VM 特例：真实 Ubuntu/GNOME/KDE 桌面同样由 display-manager 启动 Xorg，at-spi2-core 的地址 guid 也不是 `GetId` 的别名。

## 2. 决策 A：AT-SPI 地址 guid 只作不透明元数据

- `Endpoint.accessibility` 继续校验地址形态（合法 `unix:path=`/`unix:abstract=`，`guid` 若存在必须是 32 位小写十六进制），非法地址仍 `desktop_bus_address_denied`。
- **不再要求**「地址 guid == 该总线 `GetId`」。`GetId` 仍必须存在且为 32 位十六进制，并记入 `SessionInspector.guids` 供审计/scope 证据；地址 guid 仅作为收到的字段保留，不充当身份令牌。
- 身份由下面这条链证明（任一不满足即 `desktop_bus_peer_unverified`，不读正文）：
  1. **路径身份**：socket 及其祖先目录的属主/权限（`/run/user/<uid>` 私有、系统父目录 root 且不可写组/他人、socket `nlink==1`），连接前后 `(st_dev, st_ino)` 相同；
  2. **内核对端**：`SO_PEERCRED` 的 pid/uid 与期望一致，且 `psutil` 读到的 uid、`create_time`、可执行文件与连接前一致（`LinuxOps.process`）；
  3. **唯一名归属**：`GetNameOwner` 得到的连接 `GetConnectionUnixProcessID`/`GetConnectionUnixUser` 与期望 uid 一致，且再次 `GetNameOwner` 不变（防重启/换主）。
- 理由：D-Bus 规范没有「地址 guid 等于 GetId」的约束；at-spi2-core 的实现也不保证。第 1/2/3 条全部来自内核凭据与已固定的 socket inode，比可伪造的字符串更强，且不因总线重启而放行错误进程。

## 3. 决策 B：display-manager 启动的 Xorg 以「显示归属」而非「进程会话」证明

`linux_x11_socket.Connection.open` 的既有证据链保持不变（活动 logind x11 会话、`Display` 推导出的规范 socket、root 所有的真实 `Xorg` 可执行文件、socket 属主、Xauthority 授权本 uid）。`server_session` 的规则改为：

| `GetSessionByPID(Xorg pid)` | 处置 |
|---|---|
| 返回会话路径 | 必须等于当前活动会话路径，否则 `desktop_x11_peer_unverified`（不变） |
| `org.freedesktop.login1.NoSessionForPID` | 仅当当前会话已通过 `read_session`、`kind == "x11"`、存在 seat，且 Xorg `/proc/<pid>/cmdline` 的**显式 `-seat` 参数等于活动 seat** 时**接受**；即 display-manager 在系统单元里启动的 Xorg |
| 其他 `RemoteError` | `desktop_x11_peer_unverified`（不变，不猜测） |

- 接受后的「显示归属」证据 = 活动会话的 `Type=x11`、`Class=user`、`State=active`、`Active=true`、`Remote=false`、`LockedHint=false`，seat 的 `ActiveSession` 指回该会话，`Display=:N` 与规范 socket `/tmp/.X11-unix/XN` 一致（由 `display_number` 推导），Xorg 对端为 root 所有的真实二进制，**Xorg 命令行 `-seat` 与活动 seat 相同**，且 `.Xauthority`/GDM cookie 授权当前 uid。**不**使用 `_NET_WM_PID`、环境变量 `DISPLAY` 或同名进程推断。
- `-seat` 证据由 G 分支交付的本机实测补充（LightDM 的 Xorg 以 `-seat seat0` 启动）；缺失该参数或值不符一律失败关闭。
- 若 Xorg 属于**另一个**会话，或当前会话不是活动 x11 会话，仍失败关闭（`desktop_x11_session_unavailable` / `desktop_x11_peer_unverified`）。
- 未证明前不读取 cookie、不读窗口内容；失败一律不重试。

## 4. 验收用例（离线，已通过）

| 用例 | 断言 |
|---|---|
| `test_accessibility_address_guid_may_differ_from_bus_id_while_peer_identity_still_rules` | 地址 guid ≠ GetId 时 `targets` 成功（仍走 `GetNameOwner`/`GetConnectionUnixProcessID` 校验）；同一错配下把总线对端标记为已替换 → `desktop_bus_peer_unverified`，连接全部关闭 |
| `test_wrong_seat_service_uid_or_remote_address_is_rejected` | seat 错配 / a11y 服务 uid 错误 / TCP 地址仍被拒（原三条保留，移除「guid 必须相等」一条） |
| `test_pre_auth_peer_server_executable_and_session_proofs_are_required` | socket/binary/会话不属于活动会话、pid 复用、链接、共享目录、可写二进制仍全部拒 |
| `test_display_manager_xorg_without_a_logind_session_is_accepted` | `sessions` 中无 Xorg pid、且 `-seat seat0` 等于活动 seat 时，`Connection.open` 成功、cookie 读一次、`verify()` 通过、连接与 inspector 关闭 |
| `test_display_manager_xorg_with_another_seat_is_rejected` | `-seat seat1` 与活动 seat 不符时失败关闭且不读 cookie |
| `test_accessibility_address_guid_format_is_enforced`（既有） | 非法地址/guid 仍 `desktop_bus_address_denied` |

运行：`uv run --no-sync pytest -q -n 0 agent/tests/v2/platforms/linux/test_v2_linux_bus.py agent/tests/v2/platforms/linux/test_v2_linux_desktop.py agent/tests/v2/platforms/linux/test_v2_x11_socket.py` → **79 passed, 1 skipped**（2026-11-23，Windows 注入）。

## 5. 未验证与不做

- **真实桌面未复验**：本批只在 Windows 注入下证明证据链；LightDM/GDM/SDDM 实机上仍需重跑 `accept_linux_desktop.py` 确认 `session.kind=interactive` 与 `desktop_bus_peer_unverified`/`desktop_x11_peer_unverified` 消失。VM 里已定位的会话/总线事实保留在 [Linux 交接](../platforms/linux/V2_LINUX_HANDOFF.md) 与 [证据](../../evidence/v2-linux-x11-probe-2026-11-23.json)。
- 不做：`_NET_WM_PID` 或几何映射、跨 uid 总线、TCP/远程 X、Wayland 复用本证明；不改 `V2_LINUX_FOREGROUND.md` 的输入授权语义（`input_authorized` 仍为 false）。
