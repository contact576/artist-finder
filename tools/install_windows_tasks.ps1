param(
    [string]$PythonPath = "C:\Users\dapat\AppData\Local\Programs\Python\Python312\python.exe",
    [switch]$Uninstall,
    [switch]$RunWeeklyNow
)

$ErrorActionPreference = "Stop"
$Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Administrator approval is required to install background SYSTEM tasks."
}
$WeeklyTask = "Artist Scout Weekly"
$MonthlyTask = "Artist Scout Monthly Demand"
$Runner = Join-Path $PSScriptRoot "run_automation.py"
$RunnerShort = (New-Object -ComObject Scripting.FileSystemObject).GetFile($Runner).ShortPath

if ($Uninstall) {
    & "$env:WINDIR\System32\schtasks.exe" /Delete /TN $WeeklyTask /F 2>$null
    & "$env:WINDIR\System32\schtasks.exe" /Delete /TN $MonthlyTask /F 2>$null
    Write-Host "Removed Artist Scout scheduled tasks. Data and logs were not deleted."
    exit 0
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python was not found at: $PythonPath"
}
if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Automation runner was not found at: $Runner"
}

$WeeklyCommand = ('{0} {1} weekly' -f $PythonPath, $RunnerShort)
$MonthlyCommand = ('{0} {1} monthly --history-months 48' -f $PythonPath, $RunnerShort)
$Schtasks = "$env:WINDIR\System32\schtasks.exe"

& $Schtasks /Create /TN $WeeklyTask /TR $WeeklyCommand /SC WEEKLY /D MON /ST 09:30 /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) { throw "Could not create the weekly task." }

& $Schtasks /Create /TN $MonthlyTask /TR $MonthlyCommand /SC MONTHLY /D 3 /ST 09:40 /RU SYSTEM /RL HIGHEST /F
if ($LASTEXITCODE -ne 0) { throw "Could not create the monthly task." }

$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Set-ScheduledTask -TaskName $WeeklyTask -Settings $Settings | Out-Null
Set-ScheduledTask -TaskName $MonthlyTask -Settings $Settings | Out-Null

Write-Host "Installed:"
Write-Host "  $WeeklyTask - Mondays at 09:30"
Write-Host "  $MonthlyTask - day 3 of every month at 09:40"
Write-Host "  Runs in the background as SYSTEM, even while signed out or on battery"
Write-Host "Logs: out\automation\"

if ($RunWeeklyNow) {
    & $Schtasks /Run /TN $WeeklyTask
    if ($LASTEXITCODE -ne 0) { throw "The weekly task was installed but could not be started." }
    Write-Host "Weekly task started."
}
