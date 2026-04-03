@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0"
set "BACKEND_DIR=%ROOT_DIR%Backend"
set "FRONTEND_DIR=%ROOT_DIR%Frontend"
set "BACKEND_VENV_DIR=%BACKEND_DIR%\.venv"
set "BACKEND_PY=%BACKEND_VENV_DIR%\Scripts\python.exe"
set "BACKEND_READY_MARK=%BACKEND_VENV_DIR%\.agentgcs_ready"
set "STOP_EXISTING=0"
set "FORCE_SETUP=0"

if /I "%~1"=="--stop-existing" set "STOP_EXISTING=1"
if /I "%~1"=="--force-setup" set "FORCE_SETUP=1"
if /I "%~2"=="--stop-existing" set "STOP_EXISTING=1"
if /I "%~2"=="--force-setup" set "FORCE_SETUP=1"

echo.
echo [AgentGCS] launcher
echo Root: %ROOT_DIR%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo [ERROR] npm not found in PATH.
  exit /b 1
)

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend folder not found: %BACKEND_DIR%
  exit /b 1
)
if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend folder not found: %FRONTEND_DIR%
  exit /b 1
)

if "%STOP_EXISTING%"=="1" (
  echo [1/7] Stopping listeners on ports 3000 and 8000...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$listeners = Get-NetTCPConnection -LocalPort 3000,8000 -State Listen -ErrorAction SilentlyContinue; foreach($l in $listeners){ try { Stop-Process -Id $l.OwningProcess -Force -ErrorAction Stop; Write-Host ('Stopped PID ' + $l.OwningProcess) } catch {} }"
)

echo [2/7] Checking backend venv...
if not exist "%BACKEND_PY%" (
  pushd "%BACKEND_DIR%"
  python -m venv .venv
  if errorlevel 1 (
    popd
    echo [ERROR] Failed to create backend virtual environment.
    exit /b 1
  )
  popd
)

if not exist "%BACKEND_DIR%\.env" (
  if exist "%BACKEND_DIR%\.env.example" (
    echo [3/7] Creating Backend\.env from .env.example
    copy /Y "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
  )
)

if "%FORCE_SETUP%"=="1" (
  if exist "%BACKEND_READY_MARK%" del /f /q "%BACKEND_READY_MARK%" >nul 2>nul
)

if not exist "%BACKEND_READY_MARK%" (
  echo [4/7] Installing backend dependencies...
  pushd "%BACKEND_DIR%"
  "%BACKEND_PY%" -m pip install --upgrade pip
  if errorlevel 1 (
    popd
    echo [ERROR] pip upgrade failed.
    exit /b 1
  )
  "%BACKEND_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    popd
    echo [ERROR] backend dependency install failed.
    exit /b 1
  )
  >"%BACKEND_READY_MARK%" echo ready
  popd
) else (
  echo [4/7] Backend dependencies already prepared.
)

if not exist "%FRONTEND_DIR%\.env.local" (
  if exist "%FRONTEND_DIR%\.env.example" (
    echo [5/7] Creating Frontend\.env.local from .env.example
    copy /Y "%FRONTEND_DIR%\.env.example" "%FRONTEND_DIR%\.env.local" >nul
  )
)

if "%FORCE_SETUP%"=="1" (
  if exist "%FRONTEND_DIR%\node_modules" (
    echo [6/7] Removing Frontend\node_modules for clean install...
    rmdir /s /q "%FRONTEND_DIR%\node_modules"
  )
)

if not exist "%FRONTEND_DIR%\node_modules" (
  echo [6/7] Installing frontend dependencies...
  pushd "%FRONTEND_DIR%"
  call npm install
  if errorlevel 1 (
    popd
    echo [ERROR] frontend dependency install failed.
    exit /b 1
  )
  popd
) else (
  echo [6/7] Frontend dependencies already prepared.
)

echo [7/7] Starting backend/frontend/status windows...
start "AgentGCS Backend :8000" cmd /k "cd /d ""%BACKEND_DIR%"" && ""%BACKEND_PY%"" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
start "AgentGCS Frontend :3000" cmd /k "cd /d ""%FRONTEND_DIR%"" && npm run dev"
if exist "%ROOT_DIR%scripts\show-server-status.ps1" (
  start "AgentGCS Runtime Status" powershell -NoExit -ExecutionPolicy Bypass -File "%ROOT_DIR%scripts\show-server-status.ps1"
)

echo.
echo Done.
echo Frontend: http://localhost:3000
echo Backend : http://localhost:8000
echo.
echo Options:
echo   run-agentgcs.bat --stop-existing
echo   run-agentgcs.bat --force-setup
echo   run-agentgcs.bat --stop-existing --force-setup
echo.

exit /b 0

