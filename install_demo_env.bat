@echo off
setlocal
cd /d "%~dp0"

set "PY_CMD="
where python >nul 2>nul
if %errorlevel%==0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY_CMD=py -3.13"
    ) else (
        echo [ERROR] No Python interpreter found.
        if /i "%ABU_NO_PAUSE%"=="1" exit /b 1
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo [DONE] Base demo dependencies installed.
echo Run install_optional_extras.bat for optional browser/vector features.
if /i "%ABU_NO_PAUSE%"=="1" exit /b 0
pause
