<# Starts the isolated V2 workbench in this console; Ctrl+C drains the owned server. #>
[CmdletBinding()]
param(
    [Alias('Home')][string]$InstanceHome,
    [ValidateRange(1, 65535)][int]$Port = 8766,
    [string]$Python,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $Python = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'V2 Python environment is missing. Install the candidate or pass -Python with its interpreter.'
}
$launchArguments = @('-m', 'omni.composition.cli')
if ($InstanceHome) { $launchArguments += @('--home', $InstanceHome) }
$launchArguments += @('serve', '--port', [string]$Port)
if (-not $NoBrowser) { $launchArguments += '--open-browser' }
& $Python @launchArguments
exit $LASTEXITCODE
