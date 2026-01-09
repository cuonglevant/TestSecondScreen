@echo off
title Second Screen - Uninstall Virtual Display Driver
cd /d "%~dp0"

echo === Uninstall Virtual Display Driver ===
echo.

REM --- Require administrator rights ---
net session >nul 2>&1
if %errorlevel% neq 0 (
	echo [ERROR] Script must be run as Administrator.
	echo         Right-click this .bat and choose "Run as administrator".
	pause
	exit /b 1
)

REM --- Try to locate devcon.exe (for removing the virtual display device) ---
set "DEVCON_PATH="
if exist "%CD%\devcon.exe" set "DEVCON_PATH=%CD%\devcon.exe"
if not defined DEVCON_PATH (
	for /f "tokens=*" %%I in ('where devcon 2^>nul') do (
		if not defined DEVCON_PATH set "DEVCON_PATH=%%I"
	)
)

if not defined DEVCON_PATH (
	echo [WARNING] devcon.exe not found in current folder or PATH.
	echo          Cannot automatically remove the Virtual Display device.
	echo          If you have devcon.exe, copy it next to this script
	echo          and run again, or uninstall the driver manually.
	goto :done
)

echo [1/2] Using devcon: %DEVCON_PATH%
echo        Searching for devices with name containing "Virtual Display"...

setlocal enabledelayedexpansion
set "FOUND=0"

for /f "usebackq tokens=1,* delims=:" %%A in (`"%DEVCON_PATH%" find * 2^>nul ^| findstr /I "Virtual Display"`) do (
	set "ID=%%A"
	set "NAME=%%B"
	set "ID=!ID: =!"
	set "FOUND=1"
	echo.
	echo   Found: !ID! :!NAME!
	echo   Removing device !ID! ...
	"%DEVCON_PATH%" remove "@!ID!"
)

if !FOUND! == 0 (
	echo.
	echo [INFO] No device with name containing "Virtual Display" was found.
	echo        The driver may already be removed, or has a different name.
)

endlocal

:done
echo.
echo [2/2] Uninstall process finished.
echo You may need to reboot Windows to apply changes completely.
echo.
pause
exit /b 0

