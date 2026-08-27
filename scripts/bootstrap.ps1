# Target: Windows PowerShell 5.1+ / PowerShell 7
# Purpose: create the locked project environment and an optional local .env.
#          No model downloads are performed.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Resolve-Path (Join-Path $PSScriptRoot ".."))

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Install uv, reopen PowerShell, then run this script again."
}

Write-Host "== Imaginarium Forge bootstrap (locked) =="
& uv sync --frozen --all-groups
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Host "created .env from .env.example (edit non-secret model settings as needed)"
}

& .\.venv\Scripts\python.exe --version
Write-Host "Bootstrap complete. Next: .\scripts\run_app.ps1"
Write-Host "Optional OpenAI use reads OPENAI_API_KEY from the launch process; never save it in .env."
