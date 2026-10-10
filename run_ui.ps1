# Sketch-to-Space - Demo UI Launcher
# Starts uvicorn on localhost:8000 and launches the browser

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent -MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

Write-Host "Starting Sketch-to-Space Demo Backend on http://127.0.0.1:8000..." -ForegroundColor Cyan

$venvUvicorn = Join-Path $ScriptDir "venv\Scripts\uvicorn.exe"
if (Test-Path $venvUvicorn) {
    $uvicornCmd = $venvUvicorn
} else {
    $uvicornCmd = "uvicorn"
}

# Open browser to local UI
Start-Process "http://127.0.0.1:8000"

# Run uvicorn server
& $uvicornCmd backend.app:app --host 127.0.0.1 --port 8000
