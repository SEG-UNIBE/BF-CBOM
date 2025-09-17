param(
    [string]$Template,
    [string]$Target
)

if (-not $Template -and -not $Target) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = Resolve-Path -Path (Join-Path $scriptDir "..")
    $envDir = Join-Path $repoRoot "docker/env"
    $templates = Get-ChildItem -Path $envDir -Filter "*.env.template" -File -ErrorAction SilentlyContinue
    if (-not $templates) {
        Write-Host "No environment templates found in $envDir"
        exit 0
    }
    foreach ($tmpl in $templates) {
        $targetPath = $tmpl.FullName -replace '\.template$',''
        & $MyInvocation.MyCommand.Path -Template $tmpl.FullName -Target $targetPath
    }
    exit $LASTEXITCODE
}

if (-not $Template -or -not $Target) {
    Write-Error "Usage: ensure_env.ps1 -Template <template> -Target <target>"
    exit 1
}

if (-not (Test-Path -LiteralPath $Template)) {
    Write-Error "Template file '$Template' not found."
    exit 1
}

$targetDirectory = Split-Path -LiteralPath $Target -Parent
if ($targetDirectory -and -not (Test-Path -LiteralPath $targetDirectory)) {
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
}

if (Test-Path -LiteralPath $Target) {
    Write-Host "$Target already exists; leaving unchanged"
    exit 0
}

Write-Host "Creating $Target from template"
Copy-Item -LiteralPath $Template -Destination $Target -Force
