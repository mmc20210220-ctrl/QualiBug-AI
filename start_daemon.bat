@echo off
REM QualiBug Auto-Restart Daemon Wrapper
REM If the daemon dies for any reason, restart it.
cd /d "C:\Users\Test\Desktop\QualiBug_AI_Enterprise_Edition_Phase61_Complete"

:loop
echo [%date% %time%] Starting QualiBug daemon...
python -u loop_daemon.py 2>&1
echo [%date% %time%] Daemon exited with code %ERRORLEVEL%. Restarting in 10s...
timeout /t 10 /nobreak >nul
goto loop
