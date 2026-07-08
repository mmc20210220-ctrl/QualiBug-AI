@echo off
cd /d D:\QualiBug-AI\QualiBug-AI-main

echo ========================================
echo   QualiBug Auto-Start Services
echo ========================================

:loop_backend
echo [%time%] Starting QualiBug backend (:8088)...
:: JWT secret is required for production; dev mode uses a local value.
:: In production, set QUALIBUG_JWT_SECRET in .env or system environment.
if "%QUALIBUG_JWT_SECRET%"=="" set QUALIBUG_JWT_SECRET=local-dev-only
if "%QUALIBUG_PAGE_AGENT_BRIDGE_URL%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_URL=http://127.0.0.1:8797/execute
if "%QUALIBUG_PAGE_AGENT_BRIDGE_MODE%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_MODE=page_agent_browser_plan
if "%QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START%"=="" set QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START=true
start "QualiBug-Backend" cmd /c "set ENABLE_V12_STATE_GRAPH_ENGINE=true && set QUALIBUG_JWT_SECRET=%QUALIBUG_JWT_SECRET% && set QUALIBUG_PAGE_AGENT_BRIDGE_URL=%QUALIBUG_PAGE_AGENT_BRIDGE_URL% && set QUALIBUG_PAGE_AGENT_BRIDGE_MODE=%QUALIBUG_PAGE_AGENT_BRIDGE_MODE% && set QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START=%QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START% && python -m ai_test_asset_center.private_pilot_entrypoint"
timeout /t 4 /nobreak >nul

:loop_frontend
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
