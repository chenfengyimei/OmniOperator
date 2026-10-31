<#
.SYNOPSIS
  OmniOperator 命令行安装（零依赖引导；Windows PowerShell 5.1 兼容）。

.DESCRIPTION
  本脚本是命令行 / 远程场景的安装辅助（共享 scripts/installer_lib.ps1）；
  图形安装器已废弃（temp/retired-installer-2026-11-01/），源码安装步骤见 README。
  在全新 Windows 机器上把 OmniOperator 装到"工作台能打开"为止：
  预检 → uv → Python 3.12 → uv sync → Playwright Chromium → NapCat Shell
  → ADB → .env → 只读 doctor 体检。幂等：重复运行跳过已完成步骤。
  可选组件（Chromium / NapCat / ADB）失败只降级为警告，不阻塞核心安装；
  预检发现服务运行中（blocked）或核心步骤失败则中止并给指引。
  安装完成后：双击 start.bat 启动工作台；模型密钥 / QQ 白名单 / B站 Cookie
  等全部在工作台「设置中心」配置。

.PARAMETER Proxy
  GitHub 资产代理前缀（如 https://gh-proxy.com/ ）。留空 = 仅直连。

.PARAMETER PypiMirror
  PyPI 镜像（如 https://pypi.tuna.tsinghua.edu.cn/simple ）。留空 = 官方源。

.PARAMETER NapCatVersion
  NapCat release 标签。默认 v4.18.41（与项目验收部署版本一致；zip 哈希已钉死校验）。

.PARAMETER ToolsDir
  NapCat / platform-tools 的安装目录，默认 <仓库>\tools。

.PARAMETER SkipNapCat / SkipAdb / SkipChromium
  跳过对应可选组件。

.EXAMPLE
  scripts\install.ps1
  scripts\install.ps1 -Proxy https://gh-proxy.com/
  scripts\install.ps1 -SkipNapCat -SkipAdb
#>
param(
    [string]$Proxy = '',
    [string]$PypiMirror = '',
    [string]$NapCatVersion = 'v4.18.41',
    [string]$ToolsDir = '',
    [switch]$SkipNapCat,
    [switch]$SkipAdb,
    [switch]$SkipChromium
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest 的进度条会让下载慢一个数量级

. "$PSScriptRoot\installer_lib.ps1"

$sink = {
    param([string]$Text, [string]$Level)
    switch ($Level) {
        'step' { Write-Host ''; Write-Host ("==> " + $Text) -ForegroundColor Cyan }
        'ok'   { Write-Host ("    [OK] " + $Text) -ForegroundColor Green }
        'note' { Write-Host ("    [提示] " + $Text) -ForegroundColor Yellow }
        'bad'  { Write-Host ("    [失败] " + $Text) -ForegroundColor Red }
        default { Write-Host $Text }
    }
}

$options = @{
    Proxy          = $Proxy
    PypiMirror     = $PypiMirror
    NapCatVersion  = $NapCatVersion
    ToolsDir       = $ToolsDir
    SkipNapCat     = [bool]$SkipNapCat
    SkipAdb        = [bool]$SkipAdb
    SkipChromium   = [bool]$SkipChromium
}
$progress = @{}

Write-Host '===================================================='
Write-Host ' OmniOperator 命令行安装辅助（源码安装步骤见 README「环境要求与安装」）'
Write-Host (' 仓库目录：' + $script:InstallerRoot)
if ($Proxy) { Write-Host (' GitHub 代理：' + $Proxy) }
Write-Host '===================================================='

$result = Invoke-Installer -Sink $sink -Progress $progress -Options $options

if ($result -eq 'ok') {
    Write-Host ''
    Write-Host '===================================================='
    Write-Host ' 安装完成。下一步：'
    Write-Host '   1) 双击 start.bat（或 scripts\start.ps1）启动工作台'
    Write-Host '   2) 浏览器会自动打开配对链接，进入工作台'
    Write-Host '   3) 到「设置中心」填模型密钥、QQ 白名单、B站 Cookie 等配置'
    Write-Host '   4) 需要 QQ/安卓时，在服务面板与设备页完成扫码/连接'
    Write-Host ' 详见 docs/INSTALLER.md。'
    Write-Host '===================================================='
    exit 0
}
if ($result -eq 'blocked') { exit 2 }
exit 1
