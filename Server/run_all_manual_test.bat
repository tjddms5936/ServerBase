@echo off
cd /d "%~dp0"

echo [Test] Opening FastAPI, C++ Server, and DummyClient windows.
echo [Test] After all windows open, type any text in the DummyClient window.

start "FastAPI Inference API" cmd /k "%~dp0run_fastapi.bat"
timeout /t 2 > nul

start "C++ TCP Server" cmd /k "%~dp0run_server.bat"
timeout /t 2 > nul

start "Dummy Client" cmd /k "%~dp0run_dummy_client.bat"

echo [Test] Windows opened.
pause
