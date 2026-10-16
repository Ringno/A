@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ComfyUI Batch Image Generator

rem Change this ONLY if ComfyUI uses a different address/port.
rem set "COMFYUI_URL=http://127.0.0.1:8188"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 run.py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python 3 is not installed or is not on PATH.
        echo Install Python 3, then start RUN.bat again.
        goto END
    )
    python run.py
)

:END
echo.
echo Press any key to close this window.
pause >nul
