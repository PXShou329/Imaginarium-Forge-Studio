# Target: Windows PowerShell 5.1+ / PowerShell 7
# Purpose: start the local workbench once, wait for readiness, and open the browser.
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8525,
    [switch]$NoBrowser
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

function Get-ImaginariumListener {
    $Listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($Listeners.Count -eq 0) { return $null }
    $ProcessIds = @($Listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($ProcessIds.Count -ne 1) {
        throw "Port $Port has ambiguous ownership. Use -Port with a free local port."
    }
    return Get-CimInstance Win32_Process -Filter "ProcessId = $($ProcessIds[0])"
}

function Assert-ImaginariumOwner($ListenerProcess) {
    # A healthy Streamlit endpoint alone cannot identify this checkout. Opening
    # an older copy would send the author to another database and older code.
    if ($null -eq $ListenerProcess) { return }
    $CommandText = [string]$ListenerProcess.CommandLine
    $ExecutableText = [string]$ListenerProcess.ExecutablePath
    $OwnPython = [System.IO.Path]::GetFullPath($PythonPath)
    $OwnApp = [System.IO.Path]::GetFullPath($AppPath)
    $OwnInterpreter = [string]::Equals($ExecutableText, $OwnPython, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $OwnInterpreter) {
        # Windows venv Python is a redirector: the base interpreter owns the
        # socket while the exact checkout's launcher remains its parent.
        $LauncherParent = Get-CimInstance Win32_Process -Filter "ProcessId = $($ListenerProcess.ParentProcessId)"
        $OwnInterpreter = $null -ne $LauncherParent -and
            [string]::Equals([string]$LauncherParent.ExecutablePath, $OwnPython, [System.StringComparison]::OrdinalIgnoreCase) -and
            ([string]$LauncherParent.CommandLine).IndexOf($OwnApp, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            [string]::Equals([System.IO.Path]::GetFileName($ExecutableText), "python.exe", [System.StringComparison]::OrdinalIgnoreCase)
    }
    if (-not $OwnInterpreter -or
        $CommandText.IndexOf($OwnApp, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "Port $Port belongs to another app or checkout. Nothing was stopped or opened. Choose a free port with -Port."
    }
}

$ExistingListener = Get-ImaginariumListener
Assert-ImaginariumOwner $ExistingListener

if ($null -ne $ExistingListener -and -not (Test-ImaginariumHealth)) {
    throw "This checkout already owns port $Port but is not ready. Keep its work intact; retry later or choose a free -Port."
}

if ($null -eq $ExistingListener) {
    $Arguments = @(
        "-m", "streamlit", "run", ('"' + $AppPath + '"'),
        "--server.address", "127.0.0.1",
        "--server.port", "$Port",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false"
    )
    $StartedProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden -PassThru

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-ImaginariumHealth) {
            $ReadyListener = Get-ImaginariumListener
            Assert-ImaginariumOwner $ReadyListener
            if ($null -eq $ReadyListener -or
                ($ReadyListener.ProcessId -ne $StartedProcess.Id -and
                 $ReadyListener.ParentProcessId -ne $StartedProcess.Id)) {
                throw "Port ownership changed during startup. No browser was opened."
            }
            $Ready = $true
            break
        }
    }
    if (-not $Ready) {
        throw "The local app did not become ready at $AppUrl"
    }
}

Assert-ImaginariumOwner (Get-ImaginariumListener)
if (-not $NoBrowser) {
    Start-Process $AppUrl | Out-Null
}
Write-Host "Imaginarium Forge checkout: $ProjectRoot"
Write-Host "Imaginarium Forge is ready: $AppUrl"
