# Target: Windows PowerShell
# Purpose: launch the complete local Imaginarium Forge workbench.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Resolve-Path (Join-Path $PSScriptRoot ".."))
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "The locked environment is missing. Run .\scripts\bootstrap.ps1 first."
}
& .\.venv\Scripts\python.exe -m streamlit run src\imaginarium_forge\main.py @args
exit $LASTEXITCODE
