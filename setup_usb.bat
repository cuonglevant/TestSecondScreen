@echo off
echo === Setup ADB Reverse for Second Screen ===
echo.

REM Check ADB
where adb >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] ADB not found. Please install Android SDK Platform Tools.
    pause
    exit /b 1
)

echo Setting up port forwarding...
adb reverse tcp:8080 tcp:8080
adb reverse tcp:5001 tcp:5001

echo.
echo === Done! ===
echo Port 8080: WebSocket (browser)
echo Port 5001: Raw socket (Android app)
echo.
echo Now open the Second Screen app on your phone.
pause
