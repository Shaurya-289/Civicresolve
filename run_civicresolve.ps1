$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# Read the saved Windows user setting when this terminal has an older environment.
# An explicitly supplied process variable takes precedence (for deployment or testing).
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env')) -and [string]::IsNullOrWhiteSpace($env:GEMINI_API_KEY)) {
    $env:GEMINI_API_KEY = [Environment]::GetEnvironmentVariable('GEMINI_API_KEY', 'User')
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python environment not found. Install requirements in .venv first.'
}

& $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
