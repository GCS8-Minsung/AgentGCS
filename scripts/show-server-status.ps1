$repoRoot = Split-Path -Parent $PSScriptRoot

while ($true) {
    Clear-Host
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] AgentGCS Runtime Status" -ForegroundColor Cyan

    $listeners = Get-NetTCPConnection -LocalPort 3000,8000 -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $listeners | Select-Object LocalPort,OwningProcess,State | Sort-Object LocalPort | Format-Table -AutoSize
    } else {
        Write-Host 'No listening process on 3000/8000' -ForegroundColor Yellow
    }

    try {
        $backendHealth = Invoke-WebRequest -Uri 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 3
        Write-Host "Backend health: $($backendHealth.Content)" -ForegroundColor Green
    } catch {
        Write-Host "Backend health: down ($($_.Exception.Message))" -ForegroundColor Red
    }

    try {
        $frontend = Invoke-WebRequest -Uri 'http://localhost:3000' -UseBasicParsing -TimeoutSec 3
        Write-Host "Frontend: HTTP $($frontend.StatusCode)" -ForegroundColor Green
    } catch {
        Write-Host "Frontend: down ($($_.Exception.Message))" -ForegroundColor Red
    }

    Write-Host "\nPress Ctrl+C to exit." -ForegroundColor DarkGray
    Start-Sleep -Seconds 2
}
