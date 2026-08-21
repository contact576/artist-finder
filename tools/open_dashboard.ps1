$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = @(
    (Join-Path $env:LocalAppData 'Programs\Python\Python312\python.exe'),
    (Join-Path $env:LocalAppData 'Programs\Python\Python311\python.exe'),
    (Join-Path $env:LocalAppData 'Programs\Python\Python310\python.exe')
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $python) {
    throw 'Python 3.10+ was not found. Install Python, then run this launcher again.'
}

Push-Location $root
try {
    & $python (Join-Path $root 'tools\build_dashboard.py') --mode live
    if ($LASTEXITCODE -ne 0) {
        throw 'Dashboard build failed. No public deployment was attempted.'
    }

    $manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'out\dashboard\manifest.json') | ConvertFrom-Json
    $selected = $null
    $needsStart = $false

    foreach ($port in 8765..8785) {
        $url = "http://127.0.0.1:$port/"
        try {
            $remote = Invoke-RestMethod -Uri ($url + 'manifest.json') -TimeoutSec 1
            if ($remote.generated_at -eq $manifest.generated_at -and $remote.mode -eq $manifest.mode) {
                $selected = $port
                break
            }
        } catch {}

        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        try {
            $listener.Start()
            $listener.Stop()
            $selected = $port
            $needsStart = $true
            break
        } catch {
            try { $listener.Stop() } catch {}
        }
    }

    if (-not $selected) {
        throw 'No free loopback dashboard port was found from 8765 through 8785.'
    }

    $url = "http://127.0.0.1:$selected/"
    if ($needsStart) {
        $server = Join-Path $root 'tools\dashboard_server.py'
        $arguments = '"{0}" --no-browser --port {1}' -f $server, $selected
        Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden
    }

    $ready = $false
    foreach ($attempt in 1..30) {
        try {
            $data = Invoke-RestMethod -Uri ($url + 'dashboard-data.json') -TimeoutSec 1
            if ($data.generated_at -eq $manifest.generated_at -and $data.mode -eq $manifest.mode) {
                $ready = $true
                break
            }
        } catch {}
        Start-Sleep -Milliseconds 250
    }
    if (-not $ready) {
        throw "The current dashboard did not become reachable at $url"
    }

    if (-not $env:AF_NO_BROWSER) {
        Start-Process $url
    }
    Write-Host "Dashboard is ready locally at $url"
} finally {
    Pop-Location
}