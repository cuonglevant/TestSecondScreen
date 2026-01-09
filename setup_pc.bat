@echo off
title Second Screen - Setup Enviroment Start
cd /d "%~dp0"

REM === Auto install Python 3.13.2 and run VDD.Control ===
echo [0/4] Preparing Python and VDD.Control...

REM --- Install Python 3.13.2 if not present ---
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [0.1] Python not found. Installing Python 3.13.2...
    if exist "python-3.13.2.exe" (
        start /wait "" "python-3.13.2.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_launcher=0
        if %errorlevel% neq 0 (
            echo [ERROR] Python installation failed.
            pause
            exit /b 1
        )
    ) else (
        echo [ERROR] python-3.13.2.exe not found in this folder.
        echo         Please copy python-3.13.2.exe into this folder and run again.
        pause
        exit /b 1
    )
)

REM --- Extract VDD.Control.25.7.23.zip (if present) ---
if exist "VDD.Control.25.7.23.zip" (
    if not exist "VDD.Control.25.7.23" (
        echo [0.2] Extracting VDD.Control.25.7.23.zip...
        powershell -NoLogo -NoProfile -Command "Expand-Archive -Path 'VDD.Control.25.7.23.zip' -DestinationPath 'VDD.Control.25.7.23' -Force" 
        if %errorlevel% neq 0 (
            echo [WARNING] Failed to extract VDD.Control.25.7.23.zip.
        )
    )
) else (
    echo [INFO] VDD.Control.25.7.23.zip not found. Skipping extract.
)

REM --- Start EXE inside VDD.Control (if any) ---
REM --- Start VDD Control.exe inside VDD.Control.25.7.23 (if present) ---
if exist "VDD.Control.25.7.23\VDD Control.exe" (
    echo [0.3] Starting VDD Control.exe...
    echo        Launching VDD Control.exe ...
    start "" "VDD.Control.25.7.23\VDD Control.exe"
) else (
    echo [WARNING] VDD Control.exe not found inside VDD.Control.25.7.23.
)

echo.

pause