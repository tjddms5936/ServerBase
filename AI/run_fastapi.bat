@echo off
cd /d "%~dp0"

set "ROOT=%~dp0"
set "DEPS=%ROOT%.deps"

echo [FastAPI] Checking dependencies...
if not exist "%DEPS%\fastapi\__init__.py" (
    echo [FastAPI] Installing dependencies into AI\.deps...
    python -m pip install --target "%DEPS%" -r "%ROOT%requirements.txt"
    if errorlevel 1 (
        echo [FastAPI] Dependency installation failed.
        pause
        exit /b 1
    )
)

echo [FastAPI] Starting http://127.0.0.1:8000
python -c "import sys; sys.path.insert(0, r'%DEPS%'); import uvicorn; uvicorn.run('inference_api.main:app', host='127.0.0.1', port=8000)"
pause
