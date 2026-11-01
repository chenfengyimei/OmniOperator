param([switch]$SkipSync, [string]$Python = '3.12', [string]$EvidenceDir = '.pytest_cache/ci', [ValidateRange(0, 64)][int]$Workers = 4)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $projectDirectory
$env:PYTHONUTF8 = '1'
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Command (exit=$LASTEXITCODE)" }
}
if (-not $SkipSync) { Checked { uv sync --frozen --python $Python } }
Checked { uv run --no-sync python -m playwright install chromium }
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
Checked { uv run --no-sync python -m compileall -q agent/src }
Checked { uv run --no-sync python scripts/project_index.py --check }
Checked { uv run --no-sync pytest -q -n $Workers --dist=loadfile --strict-markers --junitxml "$EvidenceDir/pytest.xml" }
$evaluationDirectory = Join-Path $EvidenceDir ('core-' + [guid]::NewGuid().ToString('N'))
Checked { uv run --no-sync python scripts/eval_core.py --output $evaluationDirectory }
Write-Output "Windows verification completed. Evidence: $EvidenceDir"
