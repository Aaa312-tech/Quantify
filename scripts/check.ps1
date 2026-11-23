param([string]$Python = "python", [string]$Output = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Output) {
    $Output = Join-Path $ProjectRoot ("quality-results\" + [guid]::NewGuid().ToString('N'))
}
$Output = [System.IO.Path]::GetFullPath($Output)
if (Test-Path -LiteralPath $Output) { throw "Choose an unused check output directory: $Output" }
New-Item -ItemType Directory -Path $Output | Out-Null
$PreviousPythonPath = $env:PYTHONPATH
$PreviousBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
$env:PYTHONDONTWRITEBYTECODE = "1"
Push-Location -LiteralPath $ProjectRoot
try {
    & $Python -B -m ruff format --no-cache --check src tests scripts examples
    if ($LASTEXITCODE -ne 0) { throw "Formatting failed" }
    & $Python -B -m ruff check --no-cache src tests scripts examples
    if ($LASTEXITCODE -ne 0) { throw "Lint failed" }
    & $Python -B -m mypy --junit-xml "$Output\types.xml"
    if ($LASTEXITCODE -ne 0) { throw "Type checks failed" }
    & $Python -B -m coverage run --data-file="$Output\.coverage.unit" -m pytest -q -m 'not integration' --junitxml="$Output\unit.xml"
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed" }
    & $Python -B -m coverage run --data-file="$Output\.coverage.integration" -m pytest -q -m integration --junitxml="$Output\integration.xml"
    if ($LASTEXITCODE -ne 0) { throw "Integration tests failed" }
    & $Python -m coverage combine --data-file="$Output\.coverage" "$Output\.coverage.unit" "$Output\.coverage.integration"
    if ($LASTEXITCODE -ne 0) { throw "Coverage merge failed" }
    & $Python -m coverage json --data-file="$Output\.coverage" -o "$Output\coverage.json"
    if ($LASTEXITCODE -ne 0) { throw "Coverage export failed" }
    & $Python -B scripts/check_coverage.py "$Output\coverage.json"
    if ($LASTEXITCODE -ne 0) { throw "Coverage decreased" }
    & $Python -B scripts/check_faults.py --output "$Output\faults"
    if ($LASTEXITCODE -ne 0) { throw "Fault detection failed" }
    git rev-parse HEAD | Set-Content -LiteralPath "$Output\commit.txt" -Encoding utf8
    Write-Output "Check results: $Output"
}
finally {
    Pop-Location
    $env:PYTHONPATH = $PreviousPythonPath
    $env:PYTHONDONTWRITEBYTECODE = $PreviousBytecode
}
