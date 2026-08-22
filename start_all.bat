@echo off
setlocal
rem Locate the repository from this script's own directory (no hardcoded paths).
cd /d "%~dp0"

echo ========================================
echo   QualiBug Auto-Start Services
echo ========================================

rem JWT secret: fail fast instead of silently starting with a known default.
if "%QUALIBUG_JWT_SECRET%"=="" (
  if exist ".env.local" (
    echo [WARN] QUALIBUG_JWT_SECRET not set in environment; trying .env.local via backend dotenv loader.
  ) else (
    echo [ERROR] QUALIBUG_JWT_SECRET is required.
    echo         Generate one in PowerShell:
    echo           [Convert]::ToBase64String^((1..48 ^| ForEach-Object { Get-Random -Max 256 }) -as [byte[]]^)
    echo         Then set it for this session:  set QUALIBUG_JWT_SECRET=^<value^>
    echo         Or persist it in .env.local at the repository root.
    pause
    exit /b 1
  )
)

if "%QUALIBUG_PAGE_AGENT_BRIDGE_URL%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_URL=http://127.0.0.1:8797/execute
if "%QUALIBUG_PAGE_AGENT_BRIDGE_MODE%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_MODE=page_agent_browser_plan
if "%QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START=true

echo [%time%] Starting QualiBug backend (:8088)...
start "QualiBug-Backend" cmd /c "set ENABLE_V12_STATE_GRAPH_ENGINE=true && set QUALIBUG_JWT_SECRET=%QUALIBUG_JWT_SECRET% && set QUALIBUG_PAGE_AGENT_BRIDGE_URL=%QUALIBUG_PAGE_AGENT_BRIDGE_URL% && set QUALIBUG_PAGE_AGENT_BRIDGE_MODE=%QUALIBUG_PAGE_AGENT_BRIDGE_MODE% && set QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START=%QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START% && python -m ai_test_asset_center.private_pilot_entrypoint"
timeout /t 4 /nobreak >nul

echo [%time%] Starting frontend (:5174)...
start "QualiBug-Frontend" cmd /c "cd frontend && npm run dev"

echo.
echo All services started:
echo   Backend    : http://127.0.0.1:8088
echo   Frontend   : http://localhost:5174
echo.
echo Press any key to stop all services...
pause >nul

taskkill /FI "WINDOWTITLE eq QualiBug-*" /T /F >nul 2>&1
echo Services stopped.
endlocal
