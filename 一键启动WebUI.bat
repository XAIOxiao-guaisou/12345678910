@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

:: 切换到脚本所在目录（支持从桌面/任意位置双击启动）
cd /d "%~dp0"

title Aiduanju Engine - 启动中...
color 0A

echo.
echo  ================================================
echo    Aiduanju Engine  ^|  AI Production Pipeline
echo  ================================================
echo.

:: ── [1/3]  Python 环境检测（兼容 pyenv shim）
echo  [1/3] 检测 Python 环境...

set PYTHON_EXE=python

:: 优先使用 pyenv 管理的解释器（不走 shim）
if exist "%USERPROFILE%\.pyenv\pyenv-win\versions\" (
    for /f "delims=" %%d in ('dir /b /ad "%USERPROFILE%\.pyenv\pyenv-win\versions\" 2^>nul') do (
        if exist "%USERPROFILE%\.pyenv\pyenv-win\versions\%%d\python.exe" (
            set PYTHON_EXE=%USERPROFILE%\.pyenv\pyenv-win\versions\%%d\python.exe
        )
    )
)

:: 本地 .python-version 文件优先覆盖
if exist "%~dp0.python-version" (
    set /p LOCAL_VER=<"%~dp0.python-version"
    set LOCAL_VER=!LOCAL_VER: =!
    if exist "%USERPROFILE%\.pyenv\pyenv-win\versions\!LOCAL_VER!\python.exe" (
        set PYTHON_EXE=%USERPROFILE%\.pyenv\pyenv-win\versions\!LOCAL_VER!\python.exe
    )
)

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] 找不到可用的 Python 解释器，请检查：
    echo          - pyenv install  或  python.org 安装包
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo  [OK ]  %%v (路径: %PYTHON_EXE%)

:: ── [2/3]  Aria2c RPC 服务
echo  [2/3] 启动 Aria2c RPC 服务...
aria2c --version >nul 2>&1
if errorlevel 1 (
    echo  [WARN]  aria2c 未找到，断点续传功能已禁用
) else (
    taskkill /f /im aria2c.exe >nul 2>&1
    start "" /B aria2c ^
        --enable-rpc ^
        --rpc-listen-all=true ^
        --rpc-allow-origin-all ^
        --rpc-listen-port=6800 ^
        --max-concurrent-downloads=5 ^
        --dir="%~dp0Download" ^
        >nul 2>&1
    echo  [OK ]  Aria2c RPC 已挂载 (port 6800)
)

:: ── [3/3]  等待就绪后弹出浏览器
echo  [3/3] 拉起 Web 服务，4 秒后自动打开浏览器...
start "" /B cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"

echo.
echo  ------------------------------------------------
echo    访问地址 : http://127.0.0.1:8000
echo    退出方式 : 此窗口按 [Ctrl + C]
echo  ------------------------------------------------
echo.

title Aiduanju Engine - 运行中 http://127.0.0.1:8000

:: ── 启动 Uvicorn（前台阻塞）
"%PYTHON_EXE%" -m uvicorn web.app:app ^
    --host 127.0.0.1 ^
    --port 8000 ^
    --env-file .env ^
    --log-level info

:: ── 退出清理
echo.
echo  [退出] 正在停止所有后台服务...
taskkill /f /im aria2c.exe >nul 2>&1
title Aiduanju Engine - 已停止
echo  [完成] 服务已全部停止。
echo.
pause
