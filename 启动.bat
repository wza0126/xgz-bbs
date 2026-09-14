@echo off
chcp 65001 >nul
title 行知教研吧 · 校内教师教研协作平台
cd /d "%~dp0"

set PORT=8009

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
) else (
    set PY=python
)

echo ============================================================
echo   行知教研吧  正在启动...  端口 %PORT%
echo   停止服务：在本窗口按 Ctrl+C
echo ============================================================
echo.

%PY% server.py --port %PORT%

echo.
echo 服务已停止。
pause
