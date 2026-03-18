@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Run install_demo_env.bat first.
    if /i "%ABU_NO_PAUSE%"=="1" exit /b 1
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
set "PYTHONPATH=%CD%"
python scripts\demo_showcase.py
if /i "%ABU_NO_PAUSE%"=="1" exit /b 0
pause
