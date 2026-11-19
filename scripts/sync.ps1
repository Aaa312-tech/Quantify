param(
    [Parameter(Mandatory = $true)]
    [string]$Message,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ExpectedRemote = "https://github.com/Aaa312-tech/Quantify.git"
$PreviousPythonPath = $env:PYTHONPATH
$PreviousPrompt = $env:GIT_TERMINAL_PROMPT
$PreviousBytecode = $env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GIT_TERMINAL_PROMPT = "0"

function Assert-GitSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed" }
}

Push-Location -LiteralPath $ProjectRoot
try {
    $ActualRoot = (& git rev-parse --show-toplevel).Trim()
    Assert-GitSuccess "Repository lookup"
    if ([System.IO.Path]::GetFullPath($ActualRoot) -ne [System.IO.Path]::GetFullPath($ProjectRoot)) {
        throw "Unexpected Git repository root"
    }
    $Remote = (& git remote get-url origin).Trim()
    Assert-GitSuccess "Remote lookup"
    if ($Remote -ne $ExpectedRemote) { throw "Unexpected origin: $Remote" }
    $Branch = (& git branch --show-current).Trim()
    Assert-GitSuccess "Branch lookup"
    if ($Branch -ne "main") { throw "Review the current branch before syncing: $Branch" }
    if (@(& git diff --name-only --diff-filter=U).Count -gt 0) { throw "Resolve merge conflicts first" }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI is required to verify CI after the push"
    }

    & git fetch origin main
    Assert-GitSuccess "Fetch"
    $Counts = ((& git rev-list --left-right --count HEAD...origin/main).Trim() -split '\s+')
    Assert-GitSuccess "Branch comparison"
    if ([int]$Counts[1] -gt 0) { throw "Remote changes must be reviewed and integrated before syncing" }

    & (Join-Path $PSScriptRoot "check.ps1") -Python $Python
    $env:PYTHONPATH = Join-Path $ProjectRoot "src"
    & $Python -B -c "from quantfolio.dataset import check_integrity; from quantfolio.settings import Paths, project_root; print(check_integrity(Paths(project_root())))"
    if ($LASTEXITCODE -ne 0) { throw "Frozen input integrity failed" }

    & git add --all
    Assert-GitSuccess "Stage project files"
    & git diff --cached --quiet
    $DiffStatus = $LASTEXITCODE
    if ($DiffStatus -eq 1) {
        & git commit -m $Message
        Assert-GitSuccess "Commit"
    }
    elseif ($DiffStatus -ne 0) { throw "Cannot inspect staged changes" }
    & git lfs fsck
    Assert-GitSuccess "LFS object check"
    & git push origin main
    Assert-GitSuccess "Push"
    $Commit = (& git rev-parse HEAD).Trim()
    Write-Output "Commit: https://github.com/Aaa312-tech/Quantify/commit/$Commit"
    $Runs = @()
    for ($Attempt = 0; $Attempt -lt 10 -and $Runs.Count -eq 0; $Attempt++) {
        $RunJson = & gh run list --repo Aaa312-tech/Quantify --commit $Commit --workflow ci.yml --event push --limit 5 --json databaseId
        Assert-GitSuccess "CI lookup"
        $Runs = @($RunJson | ConvertFrom-Json)
        if ($Runs.Count -eq 0) { Start-Sleep -Seconds 3 }
    }
    if ($Runs.Count -eq 0) { throw "Push completed, but no CI run is visible yet; check GitHub Actions" }
    & gh run watch $Runs[0].databaseId --repo Aaa312-tech/Quantify --exit-status
    Assert-GitSuccess "GitHub Actions"
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
    $env:GIT_TERMINAL_PROMPT = $PreviousPrompt
    $env:PYTHONDONTWRITEBYTECODE = $PreviousBytecode
    Pop-Location
}
