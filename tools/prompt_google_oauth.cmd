@echo off
setlocal
chcp 65001 >nul
title Artist Scout - Google OAuth Setup

echo Artist Scout - Google OAuth setup
echo ==================================
echo Paste the Desktop app Client ID and Client Secret when requested.
echo The Client Secret is hidden and none of the credentials are printed.
echo.

"C:\Users\dapat\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0setup_credentials.py" --oauth
set "SETUP_EXIT=%ERRORLEVEL%"

echo.
if "%SETUP_EXIT%"=="0" (
    echo Google OAuth was saved successfully. Return to Codex.
) else (
    echo Google OAuth is not complete. Leave this window open and return to Codex.
)
echo.
pause
exit /b %SETUP_EXIT%
