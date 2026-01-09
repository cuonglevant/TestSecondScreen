@echo off
cd /d "%~dp0"
echo === Setup ADB Reverse for Second Screen ===
echo.

REM Check ADB
where adb >nul 2>nul
if %errorlevel% neq 0 (
    echo [INFO] ADB not found in PATH. Trying to set up from adb.zip...

    if exist "adb.zip" (
        echo [1/2] Extracting adb.zip...
        powershell -NoLogo -NoProfile -Command "Expand-Archive -Path 'adb.zip' -DestinationPath 'adb' -Force" 
        if %errorlevel% neq 0 (
            echo [ERROR] Failed to extract adb.zip.
            pause
            exit /b 1
        )

        echo [2/2] Adding local platform-tools to PATH...
        set "PATH=%CD%\adb\platform-tools;%PATH%"

        REM Re-check ADB after updating PATH
        where adb >nul 2>nul
        if %errorlevel% neq 0 (
            echo [ERROR] ADB still not found after extracting adb.zip.
            echo        Please check that adb.zip contains a platform-tools folder.
            pause
            exit /b 1
        )
    ) else (
        echo [ERROR] adb.zip not found in this folder.
        echo         Please place adb.zip (Android SDK Platform Tools) next to this script.
        pause
        exit /b 1
    )
)

echo Setting up port forwarding...
adb reverse tcp:8080 tcp:8080
adb reverse tcp:5001 tcp:5001

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
echo === Done! ===
echo Port 8080: WebSocket (browser)
echo Port 5001: Raw socket (Android app)
echo.
echo Now open the Second Screen app on your phone.
pause
