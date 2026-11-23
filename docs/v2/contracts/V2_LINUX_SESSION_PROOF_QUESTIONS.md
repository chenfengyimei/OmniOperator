# Linux 会话证明：两个待 A 决策的合同问题

日期：2026-11-23（Linux 子任务，分支 `linux-container-g3`）
环境：Mac mini 上的 Lima `vz` VM，Ubuntu 26.04.1 LTS、aarch64、kernel 7.0.0-38-generic；`at-spi2-core 2.60.4-0ubuntu0.1`、`libdbus-1-3 1.16.2-2ubuntu4`；LightDM + openbox + vkms 虚拟 DRM，隔离账号 `omni`（uid 10001），logind 会话 Type=x11、Seat=seat0、Display=:0、Active=yes。

这两处都让 `desktop_*_peer_unverified` 成为必然结果；当前代码与测试在离线注入下通过，真机无法放行。是否调整属于安全检查/合同变更，按分工请 A 决定。以下是现象、复现与可选方案，供 A 选择。

> **2026-11-23 已定案并实现**：A 采纳两处建议方案（a11y guid 仅作格式/不透明元数据、身份用 `SO_PEERCRED`/进程校验；DM 启动的 Xorg 以 socket/display/可执行文件/`-seat` 与活动 seat 一致作为回退证明），裁定与验收用例见 [V2_LINUX_IDENTITY](V2_LINUX_IDENTITY.md)；本页保留为问题与复现记录，不再作为待决项。

---

## 问题 1：`org.a11y.Bus.GetAddress` 的 guid 不等于该总线 `GetId`

**现象**

```
$ busctl --user call org.a11y.Bus /org/a11y/bus org.a11y.Bus GetAddress
s "unix:path=/run/user/10001/at-spi/bus_0,guid=833ae62b2abe4761314151b56b03bb37"

$ busctl --address="unix:path=/run/user/10001/at-spi/bus_0,guid=833ae62b2abe4761314151b56b03bb37" \
    call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus GetId
s "db808a07f0fb53c27d572d086b03bb37"
```

- 地址内 guid 与 `GetId` 不一致；末尾部分相同、前缀不同。
- 杀掉 `at-spi-bus-launcher`/`at-spi2-registryd` 让总线重新激活后复测，仍不一致（排除地址过期）。
- 代码位置：`agent/src/omni/adapters/platform/desktop/linux/linux_session.py::open_bus`（`endpoint.guid is not None and endpoint.guid != guid` → `desktop_bus_peer_unverified`）。`Endpoint.accessibility` 会从 GetAddress 解析 `guid`。

**影响**：`open_accessibility` 必然失败，`accessibility`/`window_identity`/`foreground_control` 全部不可用。

**可选方案**

1. **保持严格**（guid 必须等于 GetId）：需要说明 at-spi2-core 2.60 下该前提不成立，Linux 桌面语义在本环境明确不支持；等上游/环境修复。
2. **放宽为不透明值**：地址 guid 仅作为传输元数据，不参与对端身份判断；对端身份继续依赖已实现的 `SO_PEERCRED`（uid/pid）+ `GetConnectionUnixUser/ProcessID` + 进程 uid/创建时间校验。安全边界不变，改动最小。
3. **其他证明**：例如校验地址所属总线 socket 的属主/路径（已有 `Endpoint.accessibility` 的路径前缀与属主检查）后再连接，忽略 guid。

**建议**：方案 2。理由：guid 来自同一 D-Bus 地址字符串，攻击者若能篡改地址同样能篡改 guid；真正的身份证明是内核 `SO_PEERCRED` 与连接进程校验（现有实现已覆盖）。但这是安全相关合同，请 A 决定。

---

## 问题 2：DisplayManager 启动的 Xorg 没有 logind 会话

**现象**

```
$ pgrep -a Xorg
232363 /usr/lib/xorg/Xorg -core :0 -seat seat0 -auth /var/run/lightdm/root/:0 -nolisten tcp vt7 -novtswitch

$ cat /proc/232363/cgroup
0::/system.slice/lightdm.service
```

- Xorg 在 `system.slice/lightdm.service`，**没有 logind 会话**；用户会话（Type=x11、seat0、:0）不拥有 X 服务器。
- 代码位置：`agent/src/omni/adapters/platform/desktop/linux/linux_x11_socket.py`：socket 路径/属主校验后调用 `server_session(inspector, process)`，要求 Xorg 的 logind 会话与当前活动会话一致；不满足即 `desktop_x11_peer_unverified`。
- 这是 LightDM/GDM 等 DM 的正常行为（X 由 DM 启动，不属于用户会话）；`startx` 式可让 Xorg 进入用户会话，但 logind 会把该会话记为 `tty`，与现有“x11 会话”要求冲突。

**影响**：`screen_capture`/X11 `window_identity`/`foreground_control` 不可用。

**可选方案**

1. **接受 DM 启动的 Xorg**：保留 socket 身份、属主、模式、Xorg 可执行名、`-seat`/VT、`DISPLAY` 与活动会话 Display 一致性等校验；把“Xorg 必须在 logind 会话内”改为“Xorg 由 seat 所属的 DM 启动、seat 活动会话的 Display 与该 X 服务器一致”。需要定义具体字段与比较方式（`-seat seat0` 参数、`/proc/<pid>/cmdline`、socket 属主、`logind Seat.ActiveSession`、会话 `Display`）。
2. **要求 startx 式**：Xorg 在用户会话内，但 logind Session.Type=tty；需要同时放宽“x11 类型”要求，或引入“display owner”概念，改动面更大。
3. **仅在真实桌面验证**：在 GDM/LightDM 的完整桌面（有 greeter 会话与 seat 分配）上确认 Xorg 的会话归属后再定，当前环境不具备。

**建议**：方案 1。理由：DM 启动 X 是 Linux 桌面的常态；只要 socket/seat/display/可执行文件与启动参数可核验，安全性不弱于“Xorg 在用户会话内”。请 A 确认字段清单与失败关闭条件。

---

## 复现材料

- 证据：`docs/evidence/v2-linux-x11-probe-2026-11-23.json`
- 交接：`docs/v2/platforms/linux/V2_LINUX_HANDOFF.md`（2026-11-22/23 各节）
- 分支：`linux-container-g3`（基线 `0793f254`，当前 `8bb1f0a2`）
- 已修复并验证：`2f96badd`（dbus-next 0.2.3 枚举 `NO_AUTOSTART`；会话证明因此可达 `interactive`）

## 请 A 回复

1. 问题 1 选方案 1/2/3，以及（若选 2）是否保留“地址 guid 必须为合法 32 hex”的格式检查；
2. 问题 2 选方案 1/2/3，以及（若选 1）必须核验的字段清单与拒绝条件；
3. 两项决定后是否需要我同步更新 `V2_LINUX_HANDOFF.md` 的“当前限制”和对应测试（fake 注入与真机记录）。
