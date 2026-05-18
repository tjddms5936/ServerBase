@echo off
cd /d "%~dp0"

if not exist "BIN\Debug\Server.exe" (
    echo [Server] BIN\Debug\Server.exe was not found.
    echo [Server] Build the solution first.
    pause
    exit /b 1
)

echo [Server] Starting C++ TCP server.
echo [Server] Type quit and press Enter to stop.
BIN\Debug\Server.exe
pause
