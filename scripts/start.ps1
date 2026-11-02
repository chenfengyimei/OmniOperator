<#
.SYNOPSIS
  启动 OmniOperator 工作台，并自动打开配对链接（Windows PowerShell 5.1 兼容）。

.DESCRIPTION
  共享逻辑在 scripts/installer_lib.ps1 的 Start-Workbench：
  1) 工作台未运行 → 新开一个 PowerShell 窗口执行 `uv run --no-sync omni-web`
     （HANDOFF 纪律：常驻运行必须 --no-sync，避免运行时 uv 重建 venv 失败）；
  2) 轮询 data/run/pair_url.json（服务启动时写入），校验 expires_at 未过期后
     自动用默认浏览器打开配对链接；
  3) 工作台已在运行 → 只负责打开（或提示去工作台窗口取）最新配对链接。
  配对码文件可能滞后于服务换码（HANDOFF 平台坑）：只认未过期的链接。
#>
param()

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\installer_lib.ps1"

$maintenanceFile = Join-Path $script:InstallerRoot 'data\control\updates\maintenance.json'
if (Test-Path -LiteralPath $maintenanceFile) {
    & "$PSScriptRoot\update.ps1" -Recover
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$sink = {
    param([string]$Text, [string]$Level)
    switch ($Level) {
        'step' { Write-Host ("==> " + $Text) -ForegroundColor Cyan }
        'ok'   { Write-Host ("[OK] " + $Text) -ForegroundColor Green }
        'note' { Write-Host ("[提示] " + $Text) -ForegroundColor Yellow }
        'bad'  { Write-Host ("[失败] " + $Text) -ForegroundColor Red }
        default { Write-Host $Text }
    }
}

$result = Start-Workbench -Sink $sink -Root $script:InstallerRoot
if ($result -eq 'ok') { exit 0 }
exit 1
