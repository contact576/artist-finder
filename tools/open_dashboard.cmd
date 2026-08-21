@echo off
setlocal EnableExtensions
chcp 65001 >nul
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0open_dashboard.ps1"
exit /b %ERRORLEVEL%