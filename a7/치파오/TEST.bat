@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Check ComfyUI Batch Files
where py >nul 2>nul
if not errorlevel 1 (
    py -3 run.py --check
) else (
    python run.py --check
)
echo.
pause
