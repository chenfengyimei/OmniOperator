<# Offline candidate installation into new directories; Python 3.12+ and local pinned wheels are required. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Wheel,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$Sha256,
    [Parameter(Mandatory)][string]$DependencyBundle,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$DependencySha256,
    [Parameter(Mandatory)][string]$Manager,
    [Parameter(Mandatory)][Alias('Home')][string]$InstanceHome,
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'install_v2.py'
& $Python '-I' $installer '--wheel' $Wheel '--sha256' $Sha256 `
    '--dependency-bundle' $DependencyBundle '--dependency-sha256' $DependencySha256 `
    '--manager' $Manager '--home' $InstanceHome
exit $LASTEXITCODE
