@echo off
rem Explicit V1 workbench fallback.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "omniExit=%errorlevel%"
if not "%omniExit%"=="0" pause
exit /b %omniExit%
