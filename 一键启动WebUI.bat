@echo off
chcp 65001 >nul
echo ==================================================
echo         AI短剧自动化流水线 2.0 (多进程集群)
echo ==================================================

echo 正在启动前端 API 服务...
start "Aiduanju API Engine" cmd /c "python -m uvicorn web.app:app --host 127.0.0.1 --port 8000"

echo 正在启动后端 Worker 守护进程...
start "Aiduanju Worker Daemon" cmd /c "python worker.py"

echo ==================================================
echo 所有服务已投递。前端可访问 http://127.0.0.1:8000
echo Worker 控制台请查看弹出的独立黑色窗口。
echo ==================================================
pause
