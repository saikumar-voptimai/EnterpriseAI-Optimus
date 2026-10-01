<#
.SYNOPSIS
  Stop Qdrant and worker processes left running by scripts\local\start.ps1
  (for example after its window was closed instead of pressing Ctrl+C).
#>
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PidFile = Join-Path $Root ".local\pids.txt"
if (-not (Test-Path $PidFile)) {
    Write-Host "Nothing recorded as running."
    return
}
foreach ($line in Get-Content $PidFile) {
    if ($line -match '^\d+$') {
        $process = Get-Process -Id ([int]$line) -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $process.Id -Force
            Write-Host "Stopped $($process.ProcessName) ($($process.Id))"
        }
    }
}
Remove-Item $PidFile
