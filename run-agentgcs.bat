@echo off
setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "PS_ARGS="
for %%A in (%*) do (
  if /I "%%~A"=="--stop-existing" set "PS_ARGS=!PS_ARGS! -StopExisting"
  if /I "%%~A"=="--keep-existing" set "PS_ARGS=!PS_ARGS! -KeepExisting"
  if /I "%%~A"=="--force-setup" set "PS_ARGS=!PS_ARGS! -ForceSetup"
  if /I "%%~A"=="--use-lan-ip" set "PS_ARGS=!PS_ARGS! -UseLanIp"
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%scripts\run-agentgcs-launcher.ps1" !PS_ARGS!
exit /b %errorlevel%
