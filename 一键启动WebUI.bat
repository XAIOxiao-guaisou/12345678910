@echo off
chcp 65001 >nul
echo =======================================================
echo          AI Short Drama Automation v2.2
echo =======================================================

echo [1/4] Cleaning up legacy background processes...
taskkill /F /IM python.exe /T 2>nul
timeout /t 2 /nobreak >nul

echo [2/4] Starting Web API Engine (FastAPI)...
start "Aiduanju API Engine" cmd /c "python -m uvicorn web.app:app --host 127.0.0.1 --port 8000"

echo [3/4] Starting Async Worker Daemon...
start "Aiduanju Worker Daemon" cmd /c "python worker.py"

echo [4/4] Opening WebUI in Default Browser...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8000

echo =======================================================
echo All tasks have been submitted!
echo API and Worker are running in background windows.
echo Check the browser window for the UI.
echo =======================================================
pause
