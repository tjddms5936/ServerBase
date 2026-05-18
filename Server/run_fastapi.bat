@echo off
cd /d "%~dp0"

set "AI_RUNNER=%~dp0..\AI\run_fastapi.bat"

if not exist "%AI_RUNNER%" (
    echo [FastAPI] AI\run_fastapi.bat was not found.
    pause
    exit /b 1
)

call "%AI_RUNNER%"
