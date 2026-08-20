@echo off
setlocal EnableExtensions

rem Build and open the private local dashboard from any checkout location.
for %%I in ("%~dp0..") do set "AF_ROOT=%%~fI"
set "AF_PY="

for %%P in (
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%LocalAppData%\Programs\Python\Python310\python.exe"
) do (
  if not defined AF_PY if exist "%%~fP" set "AF_PY=%%~fP"
)

pushd "%AF_ROOT%"
if defined AF_PY (
  "%AF_PY%" "tools\build_dashboard.py" --mode live
  if errorlevel 1 goto :build_failed
  start "Artist Finder Dashboard" /B "%AF_PY%" "tools\dashboard_server.py" --no-browser
) else (
  where py >nul 2>nul
  if errorlevel 1 goto :python_missing
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
  if errorlevel 1 goto :python_missing
  py -3 "tools\build_dashboard.py" --mode live
  if errorlevel 1 goto :build_failed
  start "Artist Finder Dashboard" /B py -3 "tools\dashboard_server.py" --no-browser
)
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "$root = (Resolve-Path '.').Path; $manifest = Get-Content (Join-Path $root 'out\dashboard\manifest.json') -Raw | ConvertFrom-Json; $url = 'http://127.0.0.1:8765/'; for ($attempt = 0; $attempt -lt 20; $attempt++) { try { $response = Invoke-WebRequest -UseBasicParsing ($url + 'dashboard-data.json') -TimeoutSec 1; $data = $response.Content | ConvertFrom-Json; if ($response.StatusCode -eq 200 -and $data.generated_at -eq $manifest.generated_at -and $data.mode -eq $manifest.mode) { exit 0 } } catch {}; Start-Sleep -Milliseconds 250 }; exit 1"
if errorlevel 1 goto :server_failed
start "" "http://127.0.0.1:8765/"
popd
echo Dashboard is ready locally at http://127.0.0.1:8765/
exit /b 0

:build_failed
popd
echo Dashboard build failed. Read the error above; no public deployment was attempted.
exit /b 1

:python_missing
popd
echo Python 3.10+ was not found. Install Python, then run this launcher again.
exit /b 1

:server_failed
popd
echo The dashboard server did not become reachable at http://127.0.0.1:8765/.
echo Another process may be using port 8765, or the local server stopped. Close the conflicting process and run this launcher again.
exit /b 1
