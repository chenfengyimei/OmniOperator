@echo off
rem OmniOperator workbench launcher. Wraps scripts\start.ps1 (opens pairing link automatically).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
if errorlevel 1 pause
