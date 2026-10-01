<#
.SYNOPSIS
  One-time Windows laptop setup for Optimus: Python environment, frontend build,
  .env with generated secrets, and a local Qdrant binary.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\local\setup.ps1
#>
param(
    [switch]$SkipFrontend,
    [switch]$SkipQdrant
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LocalDir = Join-Path $Root ".local"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$QdrantVersion = "v1.19.1"
New-Item -ItemType Directory -Force $LocalDir | Out-Null

Write-Host "== Python 3.12 environment"
if (-not (Test-Path $VenvPython)) {
    & py -3.12 -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 is required (py -3.12). Install it from python.org." }
}
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r (Join-Path $Root "backend\requirements-dev.txt")
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }

if (-not $SkipFrontend) {
    Write-Host "== Frontend build"
    Push-Location (Join-Path $Root "frontend")
    try {
        npm ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    } finally {
        Pop-Location
    }
}

Write-Host "== Configuration"
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    Write-Host ".env already exists; left unchanged."
} else {
    $template = Get-Content (Join-Path $Root ".env.local.example") -Raw
    $token = (& $VenvPython -c "import secrets; print(secrets.token_urlsafe(32))").Trim()
    $key = (& $VenvPython -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
    $template = $template -replace "(?m)^BOOTSTRAP_TOKEN=.*$", "BOOTSTRAP_TOKEN=$token"
    $template = $template -replace "(?m)^CREDENTIAL_ENCRYPTION_KEY=.*$", "CREDENTIAL_ENCRYPTION_KEY=$key"
    [IO.File]::WriteAllText($EnvFile, $template, (New-Object Text.UTF8Encoding $false))
    Write-Host "Created .env with a setup token and credential key."
    Write-Host "Back up CREDENTIAL_ENCRYPTION_KEY: saved connection passwords cannot be decrypted without it."
}

if (-not $SkipQdrant) {
    Write-Host "== Qdrant $QdrantVersion"
    $QdrantDir = Join-Path $LocalDir "qdrant"
    $QdrantExe = Join-Path $QdrantDir "qdrant.exe"
    if (Test-Path $QdrantExe) {
        Write-Host "Qdrant binary already present."
    } else {
        New-Item -ItemType Directory -Force $QdrantDir | Out-Null
        $zip = Join-Path $LocalDir "qdrant.zip"
        $url = "https://github.com/qdrant/qdrant/releases/download/$QdrantVersion/qdrant-x86_64-pc-windows-msvc.zip"
        Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
        Expand-Archive $zip -DestinationPath $QdrantDir -Force
        Remove-Item $zip
        if (-not (Test-Path $QdrantExe)) { throw "qdrant.exe was not found in the downloaded archive." }
        Write-Host "Installed $QdrantExe"
    }
}

Write-Host ""
Write-Host "Setup complete. Before the first start, edit .env and set:"
Write-Host "  DATABASE_URL        Neon direct connection string (postgresql+psycopg://...?sslmode=require)"
Write-Host "  OPENROUTER_API_KEY  your OpenRouter key"
Write-Host "Then run: powershell -ExecutionPolicy Bypass -File scripts\local\start.ps1"
