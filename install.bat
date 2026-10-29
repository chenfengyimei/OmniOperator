@echo off
rem OmniOperator GUI installer. Zero dependencies (Windows built-in PowerShell + WinForms).
start "" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_gui.ps1"
