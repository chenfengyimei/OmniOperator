@echo off
rem OmniOperator V2 workbench; explicit V1 fallback is start-v1.bat.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-v2.ps1" %*
set "omniExit=%errorlevel%"
if not "%omniExit%"=="0" pause
exit /b %omniExit%
