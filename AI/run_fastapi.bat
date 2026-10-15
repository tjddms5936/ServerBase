@echo off
cd /d "%~dp0"

set "ROOT=%~dp0"
set "DEPS=%ROOT%.deps"

echo [FastAPI] Checking dependencies...
if not exist "%DEPS%\joblib\__init__.py" goto install
if not exist "%DEPS%\sklearn\__init__.py" goto install
if not exist "%DEPS%\fastapi\__init__.py" goto install
if not exist "%DEPS%\uvicorn\__init__.py" goto install
if not exist "%DEPS%\pandas\__init__.py" goto install
if not exist "%DEPS%\numpy\__init__.py" goto install
goto start

:install
    echo [FastAPI] Installing dependencies into AI\.deps...
    python -m pip install --target "%DEPS%" -r "%ROOT%requirements-inference.txt"
    if errorlevel 1 (
        echo [FastAPI] Dependency installation failed.
        pause
        exit /b 1
    )
:start
echo [FastAPI] Starting http://127.0.0.1:8000
echo [FastAPI] Approved RF mode - no dummy fallback
python -X utf8 "%ROOT%scripts\run_inference_api.py"
pause
