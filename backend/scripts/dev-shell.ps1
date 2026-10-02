$ErrorActionPreference = 'Stop'
$backendProjectRoot = Split-Path -Parent $PSScriptRoot
$backendVirtualEnvironment = Join-Path $backendProjectRoot '.venv'
$env:UV_PROJECT_ENVIRONMENT = $backendVirtualEnvironment
Set-Location -LiteralPath $backendProjectRoot
$backendActivationScript = Join-Path $backendVirtualEnvironment 'Scripts\Activate.ps1'
if (Test-Path -LiteralPath $backendActivationScript) {
    . $backendActivationScript
    Write-Host 'Backend project selected; project Python environment activated.'
} else {
    Write-Host 'Backend project selected. Run uv sync --locked to create its Python environment.'
}
