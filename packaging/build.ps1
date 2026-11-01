# Build dist\kn5port.exe on Windows.   Usage:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\python -m pip install . pyinstaller pywebview
Push-Location packaging
..\.venv-build\Scripts\pyinstaller kn5port.spec --noconfirm --distpath ..\dist --workpath ..\build
Pop-Location
Write-Host "`nBuilt dist\kn5port.exe"
