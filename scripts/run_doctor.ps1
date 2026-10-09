# Target: Windows PowerShell
# Purpose: environment doctor (reports only; downloads nothing).
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Resolve-Path (Join-Path $PSScriptRoot ".."))
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "The locked environment is missing. Run .\scripts\bootstrap.ps1 first."
}
& .\.venv\Scripts\python.exe -m imaginarium_forge.doctor @args
exit $LASTEXITCODE
