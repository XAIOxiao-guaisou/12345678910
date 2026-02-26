@echo off
chcp 65001 >nul
title Aiduanju API Engine - WebUI Server
color 0B

echo =======================================================
echo          [Aiduanju API Engine] 启动程序
echo =======================================================
echo.
echo 正在启动底层 API 网关服务及 Web 监控台...
echo.
echo 如果需要退出，请在此窗口按下 [Ctrl + C]。
echo 访问地址: http://127.0.0.1:8000 
echo.
echo =======================================================
echo.

python -m uvicorn web.app:app --host 127.0.0.1 --port 8000

echo.
echo 服务器已关闭或发生错误。
pause
