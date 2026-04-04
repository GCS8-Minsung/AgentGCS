param(
    [switch]$StopExisting = $false,
    [switch]$KeepExisting = $false,
    [switch]$ForceSetup = $false,
    [switch]$UseLanIp = $false
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$effectiveStopExisting = $true
if ($KeepExisting) {
    $effectiveStopExisting = $false
}
if ($StopExisting) {
    $effectiveStopExisting = $true
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "Backend"
$frontendDir = Join-Path $repoRoot "Frontend"
$backendVenvDir = Join-Path $backendDir ".venv"
$backendPy = Join-Path $backendVenvDir "Scripts\python.exe"
$backendReadyMark = Join-Path $backendVenvDir ".agentgcs_ready"
$backendPort = 8010
$targetPorts = @(3000, $backendPort)

Write-Host ""
Write-Host "[AgentGCS] launcher"
Write-Host "Root: $repoRoot"
Write-Host ""

function Assert-Command([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "$Name not found in PATH."
    }
}

function Stop-ExistingProcesses {
    param(
        [int[]]$Ports
    )

    Write-Host "[1/8] Stopping existing AgentGCS processes..."

    $targets = New-Object System.Collections.Generic.HashSet[int]
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $Ports -contains $_.LocalPort }
    foreach ($listener in $listeners) {
        [void]$targets.Add([int]$listener.OwningProcess)
    }

    $hinted = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.CommandLine -like "*uvicorn app.main:app*") -or
        (($_.CommandLine -like "*npm run dev*") -and ($_.CommandLine -like "*Frontend*")) -or
        ($_.CommandLine -like "*show-server-status.ps1*")
    }
    foreach ($proc in $hinted) {
        [void]$targets.Add([int]$proc.ProcessId)
    }

    foreach ($procId in $targets) {
        if ($procId -le 0) { continue }
        try {
            $null = & cmd /c "taskkill /PID $procId /T /F" 2>$null
            Write-Host "Stopped PID $procId (tree)"
        } catch {
        }
    }

    $deadline = (Get-Date).AddSeconds(12)
    do {
        $remaining = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $Ports -contains $_.LocalPort }
        if (-not $remaining) {
            Write-Host "[OK] Existing listeners were removed."
            return @()
        }
        foreach ($row in $remaining) {
            $procPid = [int]$row.OwningProcess
            if ($procPid -gt 0) {
                try {
                    $null = & cmd /c "taskkill /PID $procPid /T /F" 2>$null
                } catch {
                }
            }
        }
        Start-Sleep -Milliseconds 350
    } while ((Get-Date) -lt $deadline)

    Write-Warning "Could not fully clear all listeners. Remaining listeners will be reused if possible."
    $remaining | Select-Object LocalPort, OwningProcess, State | Format-Table -AutoSize
    return @($remaining)
}

function Resolve-BackendHost {
    param(
        [switch]$Lan
    )
    if (-not $Lan) {
        return "localhost"
    }
    $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if ([string]::IsNullOrWhiteSpace($ip)) {
        return "localhost"
    }
    return $ip
}

function Wait-Listeners {
    param(
        [int[]]$Ports,
        [int]$TimeoutSeconds = 25
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $Ports -contains $_.LocalPort }
        $readyPorts = @()
        if ($listeners) {
            $readyPorts = $listeners | Select-Object -ExpandProperty LocalPort -Unique
        }
        if (($readyPorts -contains 3000) -and ($readyPorts -contains $backendPort)) {
            Write-Host "[OK] Frontend/Backend listeners are active."
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    Write-Warning "Not all listeners are up yet."
    if ($listeners) {
        $listeners | Select-Object LocalPort, OwningProcess, State | Format-Table -AutoSize
    }
}

Assert-Command "python"
Assert-Command "npm"

if (-not (Test-Path $backendDir)) { throw "Backend folder not found: $backendDir" }
if (-not (Test-Path $frontendDir)) { throw "Frontend folder not found: $frontendDir" }

if ($effectiveStopExisting) {
    [void](Stop-ExistingProcesses -Ports $targetPorts)
} else {
    Write-Host "[1/8] Keeping existing processes (requested)."
}

Write-Host "[2/8] Checking backend venv..."
if (-not (Test-Path $backendPy)) {
    Push-Location $backendDir
    try {
        python -m venv .venv
    } finally {
        Pop-Location
    }
}

$backendEnv = Join-Path $backendDir ".env"
$backendEnvExample = Join-Path $backendDir ".env.example"
if (-not (Test-Path $backendEnv) -and (Test-Path $backendEnvExample)) {
    Write-Host "[3/8] Creating Backend\.env from .env.example"
    Copy-Item -Path $backendEnvExample -Destination $backendEnv -Force
}

if ($ForceSetup -and (Test-Path $backendReadyMark)) {
    Remove-Item -Path $backendReadyMark -Force -ErrorAction SilentlyContinue
}

$backendInstallRequired = -not (Test-Path $backendReadyMark)
if ($backendInstallRequired) {
    Write-Host "[4/8] Installing backend dependencies..."
    Push-Location $backendDir
    try {
        & $backendPy -m pip install --upgrade pip
        & $backendPy -m pip install -r requirements.txt
        Set-Content -Path $backendReadyMark -Value "ready" -Encoding UTF8
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[4/8] Backend dependencies already prepared."
}

$frontendEnv = Join-Path $frontendDir ".env.local"
$frontendEnvExample = Join-Path $frontendDir ".env.example"
if (-not (Test-Path $frontendEnv) -and (Test-Path $frontendEnvExample)) {
    Write-Host "[5/8] Creating Frontend\.env.local from .env.example"
    Copy-Item -Path $frontendEnvExample -Destination $frontendEnv -Force
}

Write-Host "[5.5/8] Configuring frontend backend URL..."
$backendHost = Resolve-BackendHost -Lan:$UseLanIp
$backendPublicUrl = "http://$backendHost`:$backendPort"
$backendWsUrl = "ws://$backendHost`:$backendPort/ws/agents"

$envLines = @()
if (Test-Path $frontendEnv) {
    $envLines = Get-Content $frontendEnv -ErrorAction SilentlyContinue
}
$envLines = @($envLines | Where-Object { $_ -notmatch '^NEXT_PUBLIC_BACKEND_URL=' -and $_ -notmatch '^NEXT_PUBLIC_BACKEND_WS_URL=' })
$envLines += "NEXT_PUBLIC_BACKEND_URL=$backendPublicUrl"
$envLines += "NEXT_PUBLIC_BACKEND_WS_URL=$backendWsUrl"
Set-Content -Path $frontendEnv -Value $envLines -Encoding UTF8

if ($ForceSetup -and (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "[6/8] Removing Frontend\node_modules for clean install..."
    Remove-Item -Path (Join-Path $frontendDir "node_modules") -Recurse -Force
}

if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
    Write-Host "[6/8] Installing frontend dependencies..."
    Push-Location $frontendDir
    try {
        npm install
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[6/8] Frontend dependencies already prepared."
}

Write-Host "[7/8] Starting backend/frontend/status windows..."
$backendCmd = "cd /d `"$backendDir`" && `"$backendPy`" -m uvicorn app.main:app --host 0.0.0.0 --port $backendPort --reload"
$frontendCmd = "cd /d `"$frontendDir`" && npm run dev"
$currentListeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(3000, $backendPort) }
$backendAlreadyUp = $false
$frontendAlreadyUp = $false
if ($currentListeners) {
    $portsUp = $currentListeners | Select-Object -ExpandProperty LocalPort -Unique
    $backendAlreadyUp = $portsUp -contains $backendPort
    $frontendAlreadyUp = $portsUp -contains 3000
}

if ($backendAlreadyUp) {
    Write-Warning "Backend port $backendPort is already in use. Existing process will be reused."
} else {
    Start-Process cmd.exe -ArgumentList "/k", $backendCmd -WindowStyle Normal
}

if ($frontendAlreadyUp) {
    Write-Warning "Frontend port 3000 is already in use. Existing process will be reused."
} else {
    Start-Process cmd.exe -ArgumentList "/k", $frontendCmd -WindowStyle Normal
}

$statusScript = Join-Path $repoRoot "scripts\show-server-status.ps1"
if (Test-Path $statusScript) {
    Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$statusScript`""
}

Write-Host "[8/8] Verifying listeners (3000, $backendPort)..."
Wait-Listeners -Ports @(3000, $backendPort) -TimeoutSeconds 25

Write-Host ""
Write-Host "Done."
Write-Host "Frontend: http://localhost:3000"
Write-Host "Backend : $backendPublicUrl"
Write-Host ""
Write-Host "Options:"
Write-Host "  run-agentgcs.bat --stop-existing"
Write-Host "  run-agentgcs.bat --keep-existing"
Write-Host "  run-agentgcs.bat --use-lan-ip"
Write-Host "  run-agentgcs.bat --force-setup"
Write-Host "  run-agentgcs.bat --stop-existing --force-setup"
Write-Host ""
