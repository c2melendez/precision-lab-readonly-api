[CmdletBinding()]
param(
    [string]$RemoteUrl = 'https://github.com/c2melendez/precision-lab-readonly-api.git'
)

$ErrorActionPreference = 'Stop'
$Owner = 'c2melendez'
$RepoName = 'precision-lab-readonly-api'
$FullName = "$Owner/$RepoName"
$Description = 'Read-only certification status API for Precision Lab Lite and Plus GPT Actions'

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Normalize-Remote {
    param([Parameter(Mandatory = $true)][string]$Url)
    $value = $Url.Trim().TrimEnd('/')
    if ($value.EndsWith('.git', [System.StringComparison]::OrdinalIgnoreCase)) {
        $value = $value.Substring(0, $value.Length - 4)
    }
    return $value.ToLowerInvariant()
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required. Install Git for Windows and reopen PowerShell.'
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required. Install it from https://cli.github.com/ and reopen PowerShell.'
}
if ((Normalize-Remote $RemoteUrl) -ne 'https://github.com/c2melendez/precision-lab-readonly-api') {
    throw 'RemoteUrl must point to https://github.com/c2melendez/precision-lab-readonly-api(.git).'
}

$root = (Get-Location).Path
if (-not (Test-Path (Join-Path $root 'app/main.py')) -or -not (Test-Path (Join-Path $root 'render.yaml'))) {
    throw 'Run this script from the extracted precision-lab-readonly-api project root.'
}

# Refuse to publish common local credential files, even if ignore rules were altered.
$forbidden = @('.env', '.env.local', '.env.production', 'id_rsa', 'id_ed25519', 'credentials.json')
foreach ($name in $forbidden) {
    if (Test-Path (Join-Path $root $name)) {
        throw "Refusing to publish local secret-bearing file: $name"
    }
}

# Require a working GitHub CLI login before any remote creation attempt. Suppress
# output to avoid accidentally echoing account/configuration details in terminal logs.
& gh auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'GitHub CLI is not authenticated. Run: gh auth login --hostname github.com --git-protocol https --web'
}

# List the owner's repositories first. Do not interpret an API/auth failure as
# proof that the target repository is absent.
$repoJson = & gh repo list $Owner --limit 1000 --json name,isPrivate,url 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not list repositories for the configured GitHub owner. Check gh authentication and repo scope.'
}
try {
    $repoList = @($repoJson | ConvertFrom-Json)
} catch {
    throw 'GitHub CLI returned an unreadable repository list; refusing to continue.'
}
$existing = $repoList | Where-Object { $_.name -eq $RepoName } | Select-Object -First 1
$repoExists = $null -ne $existing
if ($repoExists -and $existing.isPrivate -ne $true) {
    throw "Repository $FullName exists but is not private. Refusing to publish into a public repository."
}

# Initialize locally if needed, but never replace an existing origin.
if (-not (Test-Path (Join-Path $root '.git'))) {
    Invoke-Git -GitArgs @('init', '-b', 'main')
}
$origin = $null
$origin = (& git remote get-url origin 2>$null)
if ($LASTEXITCODE -eq 0 -and $origin) {
    if ((Normalize-Remote $origin) -ne (Normalize-Remote $RemoteUrl)) {
        throw "origin already points to '$origin'; refusing to replace it automatically."
    }
}

Invoke-Git -GitArgs @('add', '-A')
$staged = @(& git diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect staged file list.' }
if ($staged.Count -eq 0) { throw 'No files are staged; refusing to create an empty repository commit.' }
if ($staged | Where-Object { $_ -match '(^|/)\.env($|\.)' -and $_ -ne '.env.example' }) {
    throw 'A staged environment file appears to contain secrets; unstage and remove it before publishing.'
}
if ($staged | Where-Object { $_ -match '(^|/)(\.git|\.venv|__pycache__|\.pytest_cache)(/|$)' -or $_ -match '\.pyc$' }) {
    throw 'Staged files include Git internals, caches, bytecode, or a virtual environment; remove them before publishing.'
}

Write-Host 'Files staged for the dedicated read-only API repository:' -ForegroundColor Cyan
$staged | ForEach-Object { Write-Host "  $_" }
Write-Host ''
if ($repoExists) {
    Write-Host "Existing repository $FullName is private; the script will push without force." -ForegroundColor Yellow
} else {
    Write-Host "The repository $FullName will be created as PRIVATE if this operation is approved." -ForegroundColor Yellow
}
Write-Host 'This operation publishes source code to GitHub. It does not deploy to Render.' -ForegroundColor Yellow
$answer = Read-Host 'Type PUBLISH to commit/push these files'
if ($answer -cne 'PUBLISH') { throw 'Cancelled; no commit, push, or remote repository creation performed.' }

$hasHead = $true
& git rev-parse --verify HEAD *> $null
if ($LASTEXITCODE -ne 0) { $hasHead = $false }
$hasChanges = $false
& git diff --cached --quiet
if ($LASTEXITCODE -eq 1) { $hasChanges = $true }
elseif ($LASTEXITCODE -gt 1) { throw 'Could not inspect staged diff.' }

if (-not $hasHead -or $hasChanges) {
    Invoke-Git -GitArgs @('commit', '-m', 'chore: publish Precision Lab read-only certification API')
}

if (-not $repoExists) {
    & gh repo create $FullName --private --description $Description
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI could not create $FullName as private. Local commit is preserved; no push was attempted."
    }
    Write-Host "Created private repository $FullName." -ForegroundColor Green
}

if (-not $origin) {
    Invoke-Git -GitArgs @('remote', 'add', 'origin', $RemoteUrl)
}

# A preinitialized nonempty remote will reject this push. Never force-push or reset it.
Invoke-Git -GitArgs @('push', '-u', 'origin', 'main')
Write-Host 'Published. Next: create a Render Web Service from this repository with auto-deploy disabled, then add secrets in Render Dashboard.' -ForegroundColor Green
