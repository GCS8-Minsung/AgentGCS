$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendEnv = Join-Path $repoRoot "Frontend\.env.local"
$backendHealthUrl = "http://localhost:8000/health"
$backendPort = 8000

if (Test-Path $frontendEnv) {
    $backendUrlLine = Get-Content $frontendEnv -ErrorAction SilentlyContinue | Where-Object { $_ -match '^NEXT_PUBLIC_BACKEND_URL=' } | Select-Object -Last 1
    if ($backendUrlLine) {
        $backendUrl = ($backendUrlLine -replace '^NEXT_PUBLIC_BACKEND_URL=', '').Trim()
        if ($backendUrl) {
            $backendHealthUrl = "$backendUrl/health"
            try {
                $uri = [Uri]$backendUrl
                if ($uri.Port -gt 0) {
                    $backendPort = $uri.Port
                }
            } catch {}
        }
    }
}

while ($true) {
    Clear-Host
    Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] AgentGCS Runtime Status" -ForegroundColor Cyan

    $listeners = Get-NetTCPConnection -LocalPort 3000,$backendPort -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $listeners | Select-Object LocalPort,OwningProcess,State | Sort-Object LocalPort | Format-Table -AutoSize
    } else {
        Write-Host "No listening process on 3000/$backendPort" -ForegroundColor Yellow
    }

    try {
        $backendHealth = Invoke-WebRequest -Uri $backendHealthUrl -UseBasicParsing -TimeoutSec 3
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
