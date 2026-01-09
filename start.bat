@echo off
title Second Screen - Auto Start
cd /d "%~dp0"

echo ============================================================
echo   SECOND SCREEN - AUTO START
echo ============================================================
echo.



REM === Check ADB ===
echo [1/4] Checking ADB...
where adb >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] ADB not found! Please install Android SDK Platform Tools.
    echo         Download: https://developer.android.com/studio/releases/platform-tools
    pause
    exit /b 1
)
echo [OK] ADB found.
echo.

REM === Check Python ===
echo [2/4] Checking Python...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)
echo [OK] Python found.
echo.

REM === Install requirements ===
echo [3/4] Installing Python requirements...
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo [WARNING] Some packages may have failed to install.
)
echo [OK] Requirements installed.
echo.

REM === Setup ADB Reverse ===
echo [4/4] Setting up ADB reverse...
adb reverse tcp:8080 tcp:8080
adb reverse tcp:5001 tcp:5001
if %errorlevel% neq 0 (
    echo [WARNING] ADB reverse failed. Is your phone connected with USB debugging enabled?
)
echo [OK] ADB reverse configured.
echo.

echo Installing Android app on connected device...
if exist "app-release.apk" (
    echo   Found app-release.apk, installing with ADB...
    adb install -r "app-release.apk"
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to install app-release.apk.
        echo          Check that your phone is connected with USB debugging enabled.
    ) else (
        echo [OK] app-release.apk installed successfully.
    )
) else (
    echo [WARNING] app-release.apk not found in this folder. Skipping app install.
)

echo.

echo ============================================================
echo   Starting Second Screen Server...
echo ============================================================
echo.
echo   - WebSocket (Browser): http://localhost:8080
echo   - Raw Socket (App):    port 5001
echo.
echo   Press Ctrl+C to stop.
echo ============================================================
echo.

REM === Start Server ===
python secondScreen_ws.py --usb --fps 60 --monitor 2 --quality 100 --no-adaptive --bandwidth 500000

pause
