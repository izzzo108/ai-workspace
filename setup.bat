@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === ai-workspace: setup venv ===
echo.

if not exist ".git" (
    where git >nul 2>&1
    if errorlevel 1 (
        echo [WARN] git not found in PATH - skipping repo init.
        echo Install: winget install Git.Git
    ) else (
        echo Initializing git repository...
        git init -q
        echo Git repository initialized.
    )
    echo.
)

set "PY_CMD="
set "PY_VER="

py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3.13"
    set "PY_VER=3.13"
    goto found_python
)

py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3.12"
    set "PY_VER=3.12"
    goto found_python
)

py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3.11"
    set "PY_VER=3.11"
    goto found_python
)

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY_CMD=python"
        for /f "delims=" %%V in ('python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VER=%%V"
        goto found_python
    )
)

echo [ERROR] Python 3.11+ not found.
echo Install: winget install Python.Python.3.11
echo.
pause
exit /b 1

:found_python
echo Using: %PY_CMD% (Python %PY_VER%)
%PY_CMD% --version
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; v=f'{sys.version_info[0]}.{sys.version_info[1]}'; raise SystemExit(0 if v=='%PY_VER%' else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Removing old .venv ^(different Python version^)...
        rmdir /s /q ".venv"
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        echo.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment .venv already exists ^(Python %PY_VER%^).
)

echo.
echo Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] pip upgrade failed.
    echo.
    pause
    exit /b 1
)

echo.
echo Installing dependencies from requirements.txt ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] pip install failed.
    echo.
    pause
    exit /b 1
)

echo.
echo === Setup complete (Python %PY_VER%) ===
echo.
echo Entry point is not defined yet. Once main.py (or equivalent) exists, run it via:
echo   .venv\Scripts\python.exe main.py
echo.
pause

endlocal
exit /b 0
