# V2 macOS 辅助应用独立 TCC 身份设计合同（草案）

## 状态

**设计合同（design_contract_recorded，2026-11-22）。** 事实来自本机实测（[TCC 归属](../evidence/v2-macos-tcc-attribution-2026-11-22.json)、[权限与语义](../evidence/v2-macos-permissions-2026-11-22.json)）。所有者已于 2026-11-22 批准按 A1 执行（“全部批准、不必逐项询问”）。**阶段 1（helper `--connect` 校验 + UDS 传输）已实现并实测**（[证据](../evidence/v2-macos-helper-connect-stage1-2026-11-22.json)）；阶段 2（宿主 LaunchServices 启动/对端身份/回收）待实现，生产仍走原有 POSIX 启动。

`whole_core_parity_complete`/`whole_reference_feature_audit_complete`/`whole_v2_complete` 保持 false。

## 勘定事实（本机实测）

1. **POSIX 子进程启动时权限随宿主。** 后端用 `OwnedProcess.spawn`（asyncio 子进程、`start_new_session`）启动 helper 时，tccd 的 `AUTHREQ_ATTRIBUTION` 显示 `responsible=com.deepseek.dsh`（启动 agent 的宿主应用）、`requesting=org.omnioperator.desktophelper`。把已安装应用原样复制到新路径（从未授权）后用相同环境 probe，四个能力仍全部 `granted`；`tccutil reset ScreenCapture/Accessibility org.omnioperator.desktophelper` 成功返回但新进程仍 `granted`。
2. **LaunchServices 启动时 helper 成为自己的 responsible process。** 用 `open -n` 启动（临时只读 `--self-test` 入口，已回退）时 `ppid=1`、`bundle_id` 为自身、`__CFBundleIdentifier` 为自身；tccd 的 `AUTHREQ_ATTRIBUTION` **没有 `responsible` 项**，只有 `requesting=org.omnioperator.desktophelper`；四个能力按 helper 自身（已被重置）条目全部 `denied`。
3. **UDS 传输原型已通过。** 宿主创建 0700 目录下的 Unix socket，`open -n ... --args --connect <path>` 启动 helper；helper 连接后 `dup2` 到 fd 0/1，**现有 IPC 6 帧原样可用**：probe 往返 0.266 秒，回包 2112 字节，`ok=true`；宿主可用 `LOCAL_PEERPID` 取得对端 pid；该进程的 tccd 归属仍无 `responsible` 项。原型代码只在临时构建中存在，已回退。
4. **当前生产链**：`adapters/platform/desktop/backend.py::_spawn` 在 darwin 用固定路径 helper + 管道；`OwnedProcess` 以子进程句柄、进程组和 `create_time` 身份承担回收；helper 自带 20 秒失联 fuse。IPC 版本为 `omni-desktop-ipc-6`。
5. **ad-hoc 授权可能绑定 cdhash**：重建会改变可执行文件哈希；因此 helper 自身权限必须在最终交付二进制上授予，不能先授权再重建。
6. **生产形态**：OmniOperator 目标是常驻后台服务；若由 launchd 启动 agent，宿主自身通常没有 TCC 授权，POSIX 子进程模型会使 helper 失去屏幕/AX/输入权限——这是选择方案 A 的主要理由。

## 方案 A（推荐）：LaunchServices/XPC 启动 + 宿主 UDS 传输

**A1（本合同推荐，原型已验证）**

- 宿主在 0700 私有目录创建一次性 Unix socket（路径绝对、非符号链接、属主为当前 uid），用 LaunchServices（`NSWorkspace.openApplication` 或 `open -n`）启动固定 Bundle，参数 `--connect <socket>`。
- helper 校验：参数仅此一项；socket 路径绝对、长度有界；父目录属主为 uid 且 mode 0700、非链接；`getpeuid()` 等于自身 uid；连接成功后 `dup2(fd,0/1)`，其余仍走现有 IPC 6 循环；连接失败/校验失败直接退出，不回退 stdio。
- 宿主校验：启动前仍用现有 `macos_helper.resolve()` 验证固定路径、属主、清单与签名；accept 有界超时（建议 5 秒）；用 `LOCAL_PEERPID` 取对端 pid，并以 `create_time` 记录身份。
- 生命周期：由于 helper 的父进程是 launchd，不能 `waitpid`；回收改为"按已验证身份终止 + 等待消失 + helper 20 秒 fuse"，沿用现有 `reclaim_posix` 身份校验思路；每次请求一个新实例（`.newInstance`/`open -n`），避免复用持旧权限的进程。
- 协议：**IPC 6 帧不变**，只是传输从匿名管道换成 socket；请求/响应、上限、超时、错误码、unknown 语义全部保持。
- 权限：所有者对 **OmniOperator Desktop Helper** 自身授予辅助功能/屏幕/输入/投递；因为归属为 helper，`tccutil reset`/系统设置撤回才真正生效，撤回/恢复验收可以按合同执行。
- 验收：probe 经 socket 直接可跑，无需额外文件入口；仍建议保留一个仅所有者可用的只读取证入口（可选）。

**成本与风险**：需要改 `backend._spawn` 的 darwin 分支、accept/对端身份、回收路径与相关离线测试（注入启动器与 socket）；LaunchServices 有冷启动延迟与被 Gatekeeper/quarantine/translocation 影响的风险；`open` 的返回码不证明启动成功，必须由 accept 超时与身份核验兜底；连续快速请求要确保 `-n` 新实例语义。

## 方案 B：接受"权限随宿主"

保留 POSIX 子进程，明确合同为"helper 的 TCC 取决于启动它的宿主应用"：

- 生产必须由稳定的、可被 TCC 识别的宿主（签名 app/LaunchAgent app）启动 agent，而不是任意终端；权限授予与撤回都针对宿主。
- 撤回/恢复验收改为针对宿主，helper 自身条目无意义。
- 需要修改 `V2_DESKTOP`/macOS 交接中的权限归属表述与威胁模型；launchd 服务形态下宿主自身的授权问题仍需单独解决。

**成本低，但与"权限属于辅助应用自身"的产品目标冲突，且后台服务形态下权限稳定性差。**

## 建议

采用 **A1**：原型已证明现有 IPC 6 可直接复用，改动集中在 darwin 启动/回收与一个 helper 参数模式；它同时解决后台服务形态下的权限稳定性与可撤回性。实施前需所有者/A 批准本合同与 `--connect` 入口的安全边界。

## 实施顺序（批准后）

1. helper `--connect`（含路径/属主/对端校验、dup2、失败不回退）+ 失败路径测试。
2. 宿主 darwin 启动改为 LaunchServices + accept + 对端身份 + 有界超时；回收改为身份终止；注入启动器的离线测试覆盖超时、重复实例、迟到连接与清理。
3. 真实验收：对 helper 授予四项权限 → probe/read/semantic/foreground → `tccutil reset` 撤回 → 拒绝路径 → 重新授予恢复；全部在最终二进制上执行。
4. 合同与交接同步；证据记录 helper 哈希、cdhash、归属日志与撤回结果。

## 未决与未验

- 由 launchd 服务启动 helper 时的 TCC 归属未实测。
- ad-hoc 授权是否严格绑定 cdhash、以及重建后的重新授权行为未逐一验证。
- LaunchServices 冷启动/translocation/quarantine 对固定路径应用的影响未实测。
- 本机没有隔离账号，真实授权仍只能由所有者在当前图形会话完成。
