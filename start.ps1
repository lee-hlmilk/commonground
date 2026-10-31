$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    throw 'Install the Python environment first. See README.md.'
}
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) {
    throw 'Build the dashboard first: npm --prefix frontend ci; npm --prefix frontend run build'
}
Write-Host 'Starting Common Ground at http://localhost:8000'
& '.venv/Scripts/python.exe' -m backend.main
