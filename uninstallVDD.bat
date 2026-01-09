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
echo        Removing Virtual Display Driver devices...
echo.

REM --- Gỡ trực tiếp bằng Hardware ID patterns ---
echo   Removing ROOT\DISPLAY devices...
"%DEVCON_PATH%" remove "ROOT\DISPLAY\*"

echo.
echo   Removing MTT1337 display devices...
"%DEVCON_PATH%" remove "DISPLAY\MTT1337\*"

echo.
echo   Removing MttVDD driver...
"%DEVCON_PATH%" remove "*MttVDD*"

echo.

:done
echo.
echo [2/2] Uninstall process finished.
echo You may need to reboot Windows to apply changes completely.
echo.
pause
exit /b 0

