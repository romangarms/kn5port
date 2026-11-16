# Build dist\kn5port.exe on Windows.   Usage:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# ffmpeg goes inside the exe (the FH1 sound and radio need its XMA2 decoder and libvorbis encoder): Gyan's essentials build,
# pinned to one version and checked against a fixed checksum. Its LICENSE ships next to it (GPLv3; source at ffmpeg.org).
$FfVersion = "9.0.1"
$FfUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$FfVersion/ffmpeg-$FfVersion-essentials_build.zip"
$FfSha = "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3"    # bin\ffmpeg.exe in that zip
$bin = "packaging\bin"
$ff = "$bin\ffmpeg.exe"
if (-not ((Test-Path $ff) -and (Get-FileHash $ff -Algorithm SHA256).Hash -eq $FfSha)) {
    New-Item -ItemType Directory -Force $bin | Out-Null
    $zip = Join-Path $env:TEMP "ffmpeg-$FfVersion-essentials_build.zip"
    Write-Host "Downloading ffmpeg $FfVersion"
    Invoke-WebRequest $FfUrl -OutFile $zip
    $tmp = Join-Path $env:TEMP "ffmpeg-$FfVersion-extract"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp
    Copy-Item "$tmp\ffmpeg-$FfVersion-essentials_build\bin\ffmpeg.exe" $ff -Force
    Copy-Item "$tmp\ffmpeg-$FfVersion-essentials_build\LICENSE" "$bin\FFMPEG_LICENSE.txt" -Force
    Remove-Item -Recurse -Force $tmp, $zip
    $got = (Get-FileHash $ff -Algorithm SHA256).Hash
    if ($got -ne $FfSha) { Remove-Item $ff; throw "ffmpeg.exe checksum is $got, expected $FfSha" }
}

python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install --upgrade pip
.\.venv-build\Scripts\python -m pip install . pyinstaller
Push-Location packaging
..\.venv-build\Scripts\pyinstaller kn5port.spec --noconfirm --distpath ..\dist --workpath ..\build
Pop-Location
Write-Host "`nBuilt dist\kn5port.exe"
