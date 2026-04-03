param(
    [switch]$StopExisting
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'Backend'
$frontendDir = Join-Path $repoRoot 'Frontend'

if ($StopExisting) {
    $listeners = Get-NetTCPConnection -LocalPort 3000,8000 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        try {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
        } catch {
            Write-Host "Failed to stop PID $($listener.OwningProcess): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
}

$backendCommand = "Set-Location '$backendDir'; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
$frontendCommand = "Set-Location '$frontendDir'; npm run dev"

Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCommand
Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCommand

Write-Host 'Started backend and frontend in visible PowerShell windows.' -ForegroundColor Green
Write-Host 'Backend: http://localhost:8000  Frontend: http://localhost:3000' -ForegroundColor Cyan
