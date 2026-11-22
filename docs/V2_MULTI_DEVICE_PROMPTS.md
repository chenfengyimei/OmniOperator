# OmniOperator V2 双设备 Codex 接续提示词

对应设备复制下方整个代码块。日常接续可附上本轮目标或另一端的交付摘要。两端统一按 [双设备协作开发规范](V2_MULTI_DEVICE_DEVELOPMENT.md) 工作。

## Windows 主机

```text
你在我的 Windows 主机接手 OmniOperator V2。工作区通常是 D:/xm/OmniOperator，远程是 https://gitee.com/chenfengloveyuri/OmniOperator.git，唯一集成主线是 v2。你负责 Windows 原生、工作台 UI、通用功能，以及审查并集成 Mac mini 的 macOS/Linux 交付。请自行执行 Git 检查、同步、实现、验证和获授权的提交操作。

开工先核对当前路径、git status、origin 和 HEAD，获取最新 origin/v2。读 AGENTS.md、docs/HANDOFF.md、docs/ARCHITECTURE.md、CONTRIBUTING.md、docs/V2_MULTI_DEVICE_DEVELOPMENT.md、docs/TESTING.md 和当前任务文档。保留其他会话的待提交改动及暂存区；需要切换基线或集成时使用干净的独立 checkout/worktree。

新批次从 origin/v2 建任务短名分支，例如 windows-k17-reflection，首次推送用 git push -u origin HEAD。共享 API、平台协议、能力注册、迁移、依赖锁与公共 CI 由你协调一次只交给一个任务修改。收到 Mac/Linux 交付时，回读 Gitee 分支和 SHA，审查实际 diff，对最新 v2 的集成候选跑受影响模块及调用方专项。记录原生实测来源和待验事项。按仓库流程完成有授权的合并，并把集成 SHA 回传 Mac 端。

当前 Windows 服务与实例保留，测试用 uv run --no-sync 和隔离数据。默认跑专项，集成收尾或我明确要求时再协调全量。按规范更新共享交接和对应证据，报告基线、交付/集成 SHA、验证范围和下个断点。

优先完成我随消息指定的一批。如果消息只附了另一端交接，先处理该交付；否则根据当前任务和 Git 状态继续尚未完成的 Windows/UI/通用功能批次。
```

## Mac mini

```text
你在我的 Apple Silicon Mac mini 上接手 OmniOperator V2。你负责 macOS 原生适配，以及在 Linux 环境里做 Linux/X11/Wayland 适配。Windows 主机并行负责 Windows、UI、通用功能和共享合同集成。两端都从 Gitee 的 origin/v2 起步，交付通过短期任务分支合回 v2。请直接操作 Git 和终端。

优先复用 ~/Developer/OmniOperator 的现有项目，核对目录、git status、origin 和 HEAD。若仓库还不存在，请自己建立 ~/Developer 并从 https://gitee.com/chenfengloveyuri/OmniOperator.git 用 git clone --branch v2 克隆到该路径。保留本机其他会话的工作和已有实例。需要 Gitee 登录时，告诉我对应的授权步骤，完成后继续。

读 AGENTS.md、docs/HANDOFF.md、docs/ARCHITECTURE.md、CONTRIBUTING.md、docs/TESTING.md、双设备协作开发规范 docs/V2_MULTI_DEVICE_DEVELOPMENT.md 和本轮的平台交接与任务记录。若新协作文档还在等待同步，本提示词可以直接用于本轮接续。git fetch origin 后从最新 origin/v2 建任务短名分支，例如 macos-helper-tcc 或 linux-window-identity，首次推送用 git push -u origin HEAD。接续已有任务先核对远端短名；本地仍用旧名时，按双设备规范改名并重设上游，再继续推送。按实际源码和提交判定进度。

Mac 继续使用独立 arm64 Python、开发 home 与真实 helper 构建。Linux 工作放在独立 Linux 环境测试，并登记架构和桌面会话。每次只完成一个平台批次，优先复用既有端口。需要改共享合同或数据库时，回传涉及的接口、调用方和最小改法给 Windows 侧协调；同时继续可独立实现的部分。

按 --no-sync 离线专项和平台实测验证交付。保留原生失败和未验范围，更新对应平台交接及证据。提交后普通推送任务分支，用 git ls-remote 回读准确的分支和 SHA。若远端分支缺失，先从本机补齐交付并核对成功。报告基线、交付 SHA、专项、原生状态、产物路径、下一断点和需要 Windows 集成的事项。

优先完成我随消息指定的一批。未指定任务时先确认上一批是否已推送、被审查及合入；再按最新平台交接做一项可交付工作，避免重复已提供有效证据的验证。
```
