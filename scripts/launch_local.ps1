# Target: Windows PowerShell 5.1+ / PowerShell 7
# Purpose: start the local workbench once, wait for readiness, and open the browser.
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8525
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BootstrapPath = Join-Path $ProjectRoot "scripts\bootstrap.ps1"
$AppPath = Join-Path $ProjectRoot "src\imaginarium_forge\main.py"
$AppUrl = "http://127.0.0.1:$Port/"
$HealthUrl = "http://127.0.0.1:$Port/_stcore/health"

Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "Preparing the locked local environment for the first launch..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BootstrapPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Test-ImaginariumHealth {
    try {
        $Response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 2
        return $Response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Test-ImaginariumHealth)) {
    $Arguments = @(
        "-m", "streamlit", "run", $AppPath,
        "--server.address", "127.0.0.1",
        "--server.port", "$Port",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    )
    Start-Process `
        -FilePath $PythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden | Out-Null

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-ImaginariumHealth) {
            $Ready = $true
            break
        }
    }
    if (-not $Ready) {
        throw "The local app did not become ready at $AppUrl"
    }
}

Start-Process $AppUrl | Out-Null
Write-Host "Imaginarium Forge is ready: $AppUrl"
