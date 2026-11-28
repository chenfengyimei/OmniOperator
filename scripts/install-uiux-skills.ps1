# 安装/更新多 Agent 共享 UI/UX 技能（幂等）
#
# 目标目录：$env:USERPROFILE\.agents\skills（Codex 与 DSH 共同扫描的共享 skill 根）
# 来源目录：本仓库 sheji/ 下的离线克隆；缺失时按提示先克隆，脚本不自行联网。
#
# 用法：
#   pwsh -File scripts/install-uiux-skills.ps1            # 安装缺失的技能，已存在则跳过
#   pwsh -File scripts/install-uiux-skills.ps1 -Force     # 覆盖重装（会覆盖本地补丁，谨慎）
#   pwsh -File scripts/install-uiux-skills.ps1 -DryRun    # 只打印将要执行的动作
#
# 设计原则：不覆盖用户已有自定义技能；不创建符号链接；不执行第三方安装脚本；
# 只做目录复制 + 两处本机路径补丁 + 结构自检。

[CmdletBinding()]
param(
  [switch]$Force,
  [switch]$DryRun,
  [string]$Dest
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SharedRoot = if ($Dest) { $Dest } else { Join-Path $env:USERPROFILE '.agents\skills' }
$Sources = Join-Path $RepoRoot 'sheji'

$skills = @(
  @{ Name = 'agent-product-ui-ux'; Source = Join-Path $Sources 'agent-product-ui-ux\skills\agent-product-ui-ux' },
  @{ Name = 'ui-ux-pro-max';       Source = Join-Path $Sources 'ui-ux-pro-max-skill\.claude\skills\ui-ux-pro-max' },
  @{ Name = 'impeccable';          Source = Join-Path $Sources 'impeccable\.agents\skills\impeccable' },
  @{ Name = 'playwright';          Source = Join-Path $Sources 'playwright' }
)

function Write-Step([string]$Message) {
  if ($DryRun) { Write-Host "[dry-run] $Message" } else { Write-Host $Message }
}

# 只在本机安装位置与上游模板不同时打补丁；重复执行是幂等的。
function Set-LocalPatch([string]$SkillDir, [string]$SkillName) {
  $skillMd = Join-Path $SkillDir 'SKILL.md'
  if (-not (Test-Path $skillMd)) { throw "缺少 SKILL.md：$skillMd" }
  $text = Get-Content -LiteralPath $skillMd -Raw
  $sharedPosix = ($SharedRoot -replace '\\', '/')
  switch ($SkillName) {
    'playwright' {
      $patched = @"
## Skill path (set once)

> 本地补丁（共享安装）：本 Skill 安装在 ``$SharedRoot\playwright\``，不是用户级 ``\$CODEX_HOME/skills``。
> 原版路径已按本机共享安装位置改写；Windows 上优先用下面的 PowerShell 等价命令。

``````bash
# bash / Git Bash
export PWCLI="$sharedPosix/playwright/scripts/playwright_cli.sh"
``````

``````powershell
# Windows PowerShell：不依赖 bash 包装脚本
`$PWCLI = { npx --yes --package @playwright/cli playwright-cli @args }
``````
"@
      $pattern = '(?s)## Skill path \(set once\).*?(?=\r?\n## )'
      if ($text -notmatch $pattern) { throw 'playwright SKILL.md：未找到 Skill path 段落，无法打补丁' }
      $text = [regex]::Replace($text, $pattern, [System.Text.RegularExpressions.MatchEvaluator] { param($m) $patched + "`r`n" }, 1)
    }
    'ui-ux-pro-max' {
      $note = @"
# UI/UX Pro Max - Design Intelligence

> 本地补丁（共享安装）：本 Skill 根目录为 ``$SharedRoot\ui-ux-pro-max\``。下文 ``python scripts/search.py ...``
> 等相对路径均以该根目录为基准；直接调用时请加上该目录前缀，例如
> ``python $SharedRoot\ui-ux-pro-max\scripts\search.py "..." --design-system``。需要本机 Python 3（仅标准库）。
"@
      if ($text -match '本地补丁（共享安装）：本 Skill 根目录为') {
        $text = [regex]::Replace($text, '(?s)# UI/UX Pro Max - Design Intelligence\r?\n\r?\n> 本地补丁（共享安装）：.*?需要本机 Python 3（仅标准库）。', $note, 1)
      } elseif ($text -match '(?s)^---\r?\n.*?\r?\n---\r?\n') {
        $text = [regex]::Replace($text, '(?s)^(---\r?\n.*?\r?\n---\r?\n)\r?\n# UI/UX Pro Max - Design Intelligence', "`$1`r`n$note", 1)
      } else { throw 'ui-ux-pro-max SKILL.md：结构不符合预期，无法打补丁' }
    }
    default { return }
  }
  if ($DryRun) { Write-Step "[dry-run] 打补丁：$skillMd" }
  else { Set-Content -LiteralPath $skillMd -Value $text -Encoding utf8NoBOM }
}

if (-not (Test-Path $SharedRoot)) {
  Write-Step "创建共享目录：$SharedRoot"
  if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $SharedRoot | Out-Null }
}

$results = @()
foreach ($skill in $skills) {
  $target = Join-Path $SharedRoot $skill.Name
  $exists = Test-Path $target
  if (-not (Test-Path $skill.Source)) {
    $results += [pscustomobject]@{ Skill = $skill.Name; Status = 'source-missing'; Detail = $skill.Source }
    continue
  }
  if ($exists -and -not $Force) {
    $results += [pscustomobject]@{ Skill = $skill.Name; Status = 'skipped-existing'; Detail = $target }
    continue
  }
  if ($exists -and $Force) {
    Write-Step "覆盖已有技能：$target"
    if (-not $DryRun) { Remove-Item -LiteralPath $target -Recurse -Force }
  }
  Write-Step "安装 $($skill.Name) -> $target"
  if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -Path (Join-Path $skill.Source '*') -Destination $target -Recurse -Force
    Set-LocalPatch -SkillDir $target -SkillName $skill.Name
    $files = Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction Stop
    if (-not (Test-Path (Join-Path $target 'SKILL.md'))) { throw "安装后缺少 SKILL.md：$target" }
    $results += [pscustomobject]@{ Skill = $skill.Name; Status = 'installed'; Detail = "$($files.Count) files" }
  } else {
    $results += [pscustomobject]@{ Skill = $skill.Name; Status = 'would-install'; Detail = $target }
  }
}

$results | Format-Table -AutoSize | Out-String | Write-Host

if (-not $DryRun) {
  Write-Host '结构自检：'
  foreach ($skill in $skills) {
    $skillMd = Join-Path (Join-Path $SharedRoot $skill.Name) 'SKILL.md'
    if (Test-Path $skillMd) {
      $head = Get-Content -LiteralPath $skillMd -TotalCount 10
      $nameLine = $head | Where-Object { $_ -match '^name:\s*' } | Select-Object -First 1
      $parsedName = if ($nameLine) { ($nameLine -replace '^name:\s*', '').Trim().Trim('"').Trim("'") } else { '' }
      $nameOk = ($parsedName -eq $skill.Name)
      Write-Host ("  {0,-22} SKILL.md=ok frontmatter-name={1}{2}" -f $skill.Name, $(if ($nameOk) { 'ok' } else { 'MISMATCH' }), $(if ($nameOk) { '' } else { " (found: '$parsedName')" }))
    } else {
      Write-Host ("  {0,-22} SKILL.md=MISSING" -f $skill.Name)
    }
  }
  Write-Host '提示：Codex 侧可用 `codex debug prompt-input "ping"` 检查 Available skills；DSH 侧重开新会话即可看到技能目录更新。'
}
