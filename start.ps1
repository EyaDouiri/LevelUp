param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
    Push-Location frontend
    try { npm.cmd ci; npm.cmd run build } finally { Pop-Location }
}
& '.\.venv\Scripts\python.exe' api.py --port $Port
