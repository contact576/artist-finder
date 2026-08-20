@echo off
title Artist Scout - Secure Developer Token Paste
color 0B
cls
echo Artist Scout secure credential setup
echo ====================================================
echo Copy the Google Ads Developer token from the browser.
echo Paste it below and press Enter.
echo The pasted token is hidden and will not be printed.
echo.
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0setup_credentials.py" --field developer_token
echo.
echo You can now return to Codex.
pause
