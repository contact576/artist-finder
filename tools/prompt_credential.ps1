param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("developer_token", "apify_token", "bandsintown_app_id")]
    [string]$Field,
    [string]$PythonPath = "C:\Users\dapat\AppData\Local\Programs\Python\Python312\python.exe"
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Artist Scout - Secure Credential Paste"
$Setup = Join-Path $PSScriptRoot "setup_credentials.py"

Clear-Host
Write-Host "Artist Scout secure credential setup" -ForegroundColor Cyan
Write-Host "The pasted value stays on this computer and is never printed back." -ForegroundColor Gray
Write-Host ""
& $PythonPath $Setup --field $Field
$Code = $LASTEXITCODE
Write-Host ""
if ($Code -eq 0) {
    Write-Host "Saved successfully. You can return to Codex." -ForegroundColor Green
} else {
    Write-Host "Nothing was saved. Leave this window open and return to Codex." -ForegroundColor Yellow
}
Read-Host "Press Enter to close this window"
exit $Code
