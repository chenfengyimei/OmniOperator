<#
.SYNOPSIS
  Recover an interrupted OmniOperator update using its frozen base-Python runner.
#>
param([switch]$Recover, [switch]$ConfirmProcessesStopped)
$ErrorActionPreference = 'Stop'
if (-not $Recover) { throw 'Use -Recover to restore an interrupted update.' }
$installRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$updateRoot = Join-Path $installRoot 'data\control\updates'
$maintenanceFile = Join-Path $updateRoot 'maintenance.json'
if (-not (Test-Path -LiteralPath $maintenanceFile)) { exit 0 }
$recovery = Get-Content -LiteralPath (Join-Path $updateRoot 'recovery.json') -Raw | ConvertFrom-Json
$operation = [IO.Path]::GetFullPath($recovery.operation)
$maintenance = Get-Content -LiteralPath $maintenanceFile -Raw | ConvertFrom-Json
if ([IO.Path]::GetFileName($operation) -ne $maintenance.operation) { throw 'Recovery plan does not match the active update.' }
$runner = [IO.Path]::GetFullPath($recovery.runner)
$allowedOperations = [IO.Path]::GetFullPath((Join-Path $updateRoot 'operations')) + [IO.Path]::DirectorySeparatorChar
if (-not $operation.StartsWith($allowedOperations, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid recovery operation path.' }
if (-not $runner.StartsWith(($operation + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid frozen runner path.' }
if (-not (Test-Path -LiteralPath $runner) -or -not (Test-Path -LiteralPath $recovery.python)) { throw 'Recovery runner is missing. Preserve the update directory for inspection.' }
if ($ConfirmProcessesStopped) {
    & $recovery.python $runner $operation --recover --confirm-processes-stopped
} else {
    & $recovery.python $runner $operation --recover
}
exit $LASTEXITCODE
