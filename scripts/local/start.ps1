<#
.SYNOPSIS
  Start Optimus on this laptop: local Qdrant (if configured), database migrations,
  background workers and the web app at http://localhost:<APP_PORT>.
  Press Ctrl+C to stop everything this script started.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\local\start.ps1
#>
param(
    [switch]$NoWorker,
    [switch]$SkipMigrations
)
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$LocalDir = Join-Path $Root ".local"
$LogDir = Join-Path $LocalDir "logs"
$PidFile = Join-Path $LocalDir "pids.txt"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"

if (-not (Test-Path $VenvPython)) { throw "Run scripts\local\setup.ps1 first." }
if (-not (Test-Path $EnvFile)) { throw "No .env found. Run scripts\local\setup.ps1 first." }
if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
    throw "The frontend is not built. Run scripts\local\setup.ps1 (or npm run build in frontend)."
}
New-Item -ItemType Directory -Force $LogDir | Out-Null

$Config = @{}
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $Config[$Matches[1]] = $Matches[2].Trim() }
}
if (-not $Config["DATABASE_URL"]) {
    throw "Set DATABASE_URL in .env (Neon direct connection string using postgresql+psycopg://)."
}
if (-not $Config["OPENROUTER_API_KEY"]) {
    Write-Warning "OPENROUTER_API_KEY is empty: chat, classification and embeddings will be unavailable."
}
$Port = $Config["APP_PORT"]
if (-not $Port) { $Port = "8088" }

function Test-Ready([string]$Url) {
    try {
        $response = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$Started = @()

function Stop-Started {
    foreach ($process in $Started) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
}

try {
    if ($Config["VECTOR_BACKEND"] -eq "qdrant") {
        $QdrantUrl = $Config["QDRANT_URL"]
        if (-not $QdrantUrl) { $QdrantUrl = "http://127.0.0.1:6333" }
        $QdrantUrl = $QdrantUrl.TrimEnd("/")
        if (Test-Ready "$QdrantUrl/readyz") {
            Write-Host "Qdrant already running at $QdrantUrl"
        } elseif ($QdrantUrl -match '^http://(127\.0\.0\.1|localhost):6333$') {
            $QdrantDir = Join-Path $LocalDir "qdrant"
            $QdrantExe = Join-Path $QdrantDir "qdrant.exe"
            if (-not (Test-Path $QdrantExe)) { throw "Qdrant is not installed. Run scripts\local\setup.ps1." }
            $env:QDRANT__SERVICE__HOST = "127.0.0.1"
            $env:QDRANT__STORAGE__STORAGE_PATH = Join-Path $QdrantDir "storage"
            $env:QDRANT__STORAGE__SNAPSHOTS_PATH = Join-Path $QdrantDir "snapshots"
            $env:QDRANT__TELEMETRY_DISABLED = "true"
            $Started += Start-Process $QdrantExe -WorkingDirectory $QdrantDir -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $LogDir "qdrant.log") `
                -RedirectStandardError (Join-Path $LogDir "qdrant.err.log")
            $deadline = (Get-Date).AddSeconds(30)
            while (-not (Test-Ready "$QdrantUrl/readyz")) {
                if ((Get-Date) -gt $deadline) { throw "Qdrant did not start; see .local\logs\qdrant.err.log" }
                Start-Sleep -Milliseconds 500
            }
            Write-Host "Qdrant started at $QdrantUrl (storage: .local\qdrant\storage)"
        } else {
            throw "Qdrant at $QdrantUrl is not reachable. Check QDRANT_URL / QDRANT_API_KEY."
        }
    }

    if (-not $SkipMigrations) {
        Write-Host "Applying database migrations..."
        Push-Location $Backend
        try {
            & $VenvPython -m alembic upgrade head
            if ($LASTEXITCODE -ne 0) { throw "Database migration failed. Check DATABASE_URL and network access to Neon." }
        } finally {
            Pop-Location
        }
    }

    if (-not $NoWorker) {
        $Started += Start-Process $VenvPython -ArgumentList "-m", "app.worker", "--role", "all" `
            -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $LogDir "worker.out.log") `
            -RedirectStandardError (Join-Path $LogDir "worker.log")
        Write-Host "Workers started (logs: .local\logs\worker.log)"
    }
    ($Started | ForEach-Object { $_.Id }) -join "`n" | Set-Content $PidFile

    Write-Host ""
    Write-Host "Optimus: http://localhost:$Port"
    if ($Config["BOOTSTRAP_TOKEN"]) { Write-Host "First-run setup token: $($Config["BOOTSTRAP_TOKEN"])" }
    Write-Host "Press Ctrl+C to stop."
    Write-Host ""
    Push-Location $Backend
    try {
        & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    } finally {
        Pop-Location
    }
} finally {
    Stop-Started
}
