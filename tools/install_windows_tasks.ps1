param(
    [string]$PythonPath = "C:\Users\dapat\AppData\Local\Programs\Python\Python312\python.exe",
    [switch]$Uninstall,
    [switch]$RunWeeklyNow,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$WeeklyTask = "Artist Scout Weekly"
$MonthlyTask = "Artist Scout Monthly Demand"
$Runner = Join-Path $PSScriptRoot "run_automation.py"
$Schtasks = Join-Path $env:WINDIR "System32\schtasks.exe"

function Quote-TaskPath([string]$Path) {
    # Task Scheduler receives one /TR string. Escaping keeps spaces and moved repo paths valid.
    return '"' + $Path.Replace('"', '\"') + '"'
}

$WeeklyCommand = ('{0} {1} weekly' -f (Quote-TaskPath $PythonPath), (Quote-TaskPath $Runner))
$MonthlyCommand = ('{0} {1} monthly --history-months 48' -f (Quote-TaskPath $PythonPath), (Quote-TaskPath $Runner))

function Show-Plan {
    Write-Host "Task Scheduler dry-run - no tasks will be changed"
    if ($Uninstall) {
        Write-Host "  DELETE $WeeklyTask"
        Write-Host "  DELETE $MonthlyTask"
    } else {
        Write-Host "  CREATE ${WeeklyTask}: $WeeklyCommand"
        Write-Host "  Schedule: Monday 09:30, SYSTEM, highest privileges"
        Write-Host "  CREATE ${MonthlyTask}: $MonthlyCommand"
        Write-Host "  Schedule: day 3 at 09:40, SYSTEM, highest privileges"
        if ($RunWeeklyNow) { Write-Host "  THEN RUN $WeeklyTask" }
    }
    Write-Host "  Runner writes only non-secret status/log metadata under out\automation"
}

if ($WhatIf) { Show-Plan; exit 0 }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python was not found at: $PythonPath" }
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) { throw "Automation runner was not found at: $Runner" }
if (-not (Test-Path -LiteralPath $Schtasks -PathType Leaf)) { throw "schtasks.exe was not found at: $Schtasks" }

$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator approval is required to install background SYSTEM tasks. Use -WhatIf first to inspect the exact commands."
}

if ($Uninstall) {
    & $Schtasks /Delete /TN $WeeklyTask /F 2>$null
    & $Schtasks /Delete /TN $MonthlyTask /F 2>$null
    Write-Host "Removed Artist Scout scheduled tasks. Data and logs were not deleted."
    exit 0
}

& $Schtasks /Create /TN $WeeklyTask /TR $WeeklyCommand /SC WEEKLY /D MON /ST 09:30 /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) { throw "Could not create the weekly task." }
& $Schtasks /Create /TN $MonthlyTask /TR $MonthlyCommand /SC MONTHLY /D 3 /ST 09:40 /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) { throw "Could not create the monthly task." }

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Set-ScheduledTask -TaskName $WeeklyTask -Settings $Settings | Out-Null
Set-ScheduledTask -TaskName $MonthlyTask -Settings $Settings | Out-Null

Write-Host "Installed: $WeeklyTask (Mondays 09:30) and $MonthlyTask (day 3, 09:40)."
Write-Host "Both run locally as SYSTEM. Logs/status: out\automation"
if ($RunWeeklyNow) {
    & $Schtasks /Run /TN $WeeklyTask
    if ($LASTEXITCODE -ne 0) { throw "The weekly task was installed but could not be started." }
    Write-Host "Weekly task started. Inspect out\automation\status.json after it finishes."
}
