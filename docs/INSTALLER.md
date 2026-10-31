# 一键安装器（install.bat → 图形安装器）

面向"不一定会配置"的 Windows 用户：**装好 + 跑起来**为止，其余配置
（模型密钥、QQ 白名单、B站 Cookie、心跳任务……）全部在工作台「设置中心」完成，
安装器不代填、不碰任何密钥。

> 定位：受控内测的装机工具。项目本身尚未通过验收门禁（见 docs/ACCEPTANCE.md），
> 对外分发前请先走发布闸门。

## 用法

| 动作 | 操作 |
|---|---|
| 安装 | 双击 `install.bat` → 打开**图形安装器** → 点「开始安装」 |
| 启动 | 图形安装器点「启动工作台」（或双击 `start.bat`）；浏览器自动打开配对链接 |
| 重装/补装 | 重复安装即可，已完成步骤自动跳过（幂等） |

启动后会弹出一个**工作台服务窗口**（标题栏标注"OmniOperator 工作台服务（保持开启）"，
窗口顶部有醒目提示）：它是工作台的宿主进程，**保持开启，关闭它 = 停止工作台**。
误关了不用担心，再点一次「启动工作台」即可恢复（数据不丢）。

图形安装器（`scripts/install_gui.ps1`）是 Windows 自带的 PowerShell + WinForms，
**零依赖、无需安装任何东西**。窗口提供三个选项（不勾也能装）：

- GitHub 走 gh-proxy.com 代理（直连不畅时勾选）
- 依赖同步走清华 PyPI 镜像（国内加速）
- 仅装核心（跳过 Chromium / NapCat / ADB，最小可跑工作台）

命令行等价入口（远程 / 调试场景用，逻辑与图形界面共享 `scripts/installer_lib.ps1`）：

```powershell
scripts\install.ps1                                             # 全量安装（返回码 0=成功 1=失败 2=被预检拦下）
scripts\install.ps1 -Proxy https://gh-proxy.com/                # GitHub 资产走代理
scripts\install.ps1 -PypiMirror https://pypi.tuna.tsinghua.edu.cn/simple
scripts\install.ps1 -SkipNapCat -SkipAdb -SkipChromium         # 只装核心依赖
scripts\install.ps1 -NapCatVersion v4.18.42                     # 换 NapCat 版本（需同步更新 lib 内 sha256）
scripts\start.ps1                                               # 启动工作台并自动打开配对链接
```

## 安装步骤与网络策略

| 步骤 | 内容 | 下载源（按尝试顺序） | 失败后果 |
|---|---|---|---|
| 0 | 预检（服务未占用） | 本地：独占探测 `.venv\Scripts\omni*.exe` | **拦下**（blocked）：提示停止服务后重试 |
| 1 | uv 包管理器 | GitHub 直连（代理选项勾选后代理优先） | **中止**（核心步骤） |
| 2 | Python 3.12（uv 托管） | python-build-standalone（代理走 `UV_PYTHON_INSTALL_MIRROR`） | **中止**（核心步骤） |
| 3 | 项目依赖 `uv sync --frozen` | PyPI（镜像选项勾选后走清华源） | **中止**（核心步骤） |
| 4 | Playwright Chromium | 官方 → 自动改用 npmmirror 镜像重试 | 警告：浏览器能力不可用 |
| 5 | NapCat Shell（QQ 通道） | GitHub（代理优先），**sha256 钉死校验** | 警告：QQ 通道不可用 |
| 6 | ADB platform-tools（安卓） | 腾讯云镜像 → dl.google.com 兜底（实测镜像可达且同尺寸） | 警告：安卓能力不可用 |
| 7 | `.env` | 从 `.env.example` 复制（**已存在则保留不动**） | — |
| 8 | 只读 doctor 体检 | 本地 | error 级问题 → 中止并提示反馈 |

设计原则：

- **预检先拦**：OmniOperator 服务运行中会锁住 `.venv` 里的入口 exe，`uv sync`
  会中途炸掉。预检用独占打开探测 `omni*.exe`，被占用就停在"blocked"并给指引
  （工作台服务面板停止或关闭对应窗口），绝不装到一半失败。
- **核心步骤失败才中止**（uv / Python / 依赖同步），可选组件失败一律降级为
  警告并给手动方案——"工作台能打开"就是安装成功的底线。
- **NapCat 版本与哈希一起钉死**（默认 v4.18.41，与验收部署一致）。走第三方
  代理下载也具备篡改证据能力；换版本时从 GitHub release 的 digest 字段更新
  `installer_lib.ps1` 里的 `$script:NapCatZipSha256`。
- **安装器不写任何密钥、不改 QQ 白名单**——这些属于用户在设置中心的职责，
  也符合项目"花钱/发布/加好友人工确认、无人值守 fail-closed"的红线设计。
- NapCat Shell 依赖本机已安装 **QQ NT 桌面版**（安装器尽力检测并提醒，不拦截）。

## 修改过的机器状态（卸载须知）

- `%USERPROFILE%\.local\bin`：uv.exe（并加入用户 PATH）
- `<仓库>\tools\napcat`、`<仓库>\tools\platform-tools`：解压组件（platform-tools 加入用户 PATH）
- `<仓库>\config\config.yaml` 的 `system.napcat_dir` 定向改写为解包目录（保留注释；config.yaml 不入库，首次安装前先复制 config.example.yaml）
- `<仓库>\.env`（仅首次，从模板复制）

## 平台坑（脚本内已处理，改动前先读）

1. **PowerShell 5.1 没有 `&&` / `||`**——脚本全程用 `;` 与 if/else；
   `tests/test_installer_scripts.py` 有静态守卫。
2. **PS 5.1 按 ANSI 解析无 BOM 的 .ps1**——四个脚本必须以 UTF-8 **带 BOM** 保存，
   否则中文提示直接把脚本打花；守卫测试同样钉死。
3. **cmd 代码页随机器变化**——`install.bat` / `start.bat` 只允许 ASCII。
4. **Invoke-WebRequest 进度条**会把下载拖慢一个数量级：`$ProgressPreference='SilentlyContinue'`。
5. **TLS 1.2**：老 Win10 的 PS 5.1 默认不带，下载前显式开启。
6. **config.yaml 无 BOM 写回**：用 `[System.IO.File]::WriteAllText(..., UTF8Encoding($false))`，
   PS 5.1 的 `-Encoding UTF8` 会带 BOM，而 config.yaml 的消费方按无 BOM 对待。
7. **配对码文件会滞后**：`data/run/pair_url.json` 只在换码那一刻刷新，
  启动脚本必须校验 `expires_at`，拿不到就以工作台窗口打印的链接为准。
8. **常驻服务占用 .venv**：预检必须先拦（见上）；直接 uv sync 会在 Windows 上
  因文件锁（os error 32）失败。
9. **GUI 跨线程**：安装步骤在后台 Runspace 执行，日志经同步队列由 UI Timer 消费
  （PS 5.1 的 scriptblock 不能跨 Runspace 调用），窗口全程不卡顿。

## 手动方案（对应各步失败时）

- uv：浏览器下载 `https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip`，
  解压到 `%USERPROFILE%\.local\bin`，重跑安装器。
- NapCat：从 `https://github.com/NapNeko/NapCatQQ/releases` 下载 `NapCat.Shell.zip`，
  解压到任意目录（含 napcat.bat），把 `config/config.yaml` 的 `system.napcat_dir`
  指向该目录（config.yaml 首次使用前先从 config.example.yaml 复制）；需本机先安装 QQ NT 桌面版。
- ADB：从 `https://developer.android.com/tools/releases/platform-tools` 下载，
  解压后把目录加入 PATH，或直接勾选"仅装核心"跳过。
- Chromium：`uv run --no-sync python -m playwright install chromium`
  （国内可先 `$env:PLAYWRIGHT_DOWNLOAD_HOST='https://npmmirror.com/mirrors/playwright/'`）。
