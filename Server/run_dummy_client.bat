@echo off
cd /d "%~dp0"

if not exist "BIN\Debug\DummyClient.exe" (
    echo [DummyClient] BIN\Debug\DummyClient.exe was not found.
    echo [DummyClient] Build the solution first.
    pause
    exit /b 1
)

echo [DummyClient] Starting test client.
echo [DummyClient] Type any text and press Enter to send a test packet.
echo [DummyClient] Type quit and press Enter to stop.
BIN\Debug\DummyClient.exe
pause
