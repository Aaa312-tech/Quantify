param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = "1"
Push-Location -LiteralPath $ProjectRoot
try {
    & $Python -B -m ruff format --no-cache --check src tests
    if ($LASTEXITCODE -ne 0) { throw "Formatting failed" }
    & $Python -B -m ruff check --no-cache src tests
    if ($LASTEXITCODE -ne 0) { throw "Lint failed" }
    & $Python -B -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
}
finally {
    Pop-Location
}
