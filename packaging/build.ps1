# Build dist\kn5port.exe on Windows. Double-click build.cmd in the repo root, or:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Options:  -Fresh  rebuild the build Python from scratch;  -Pause  wait for Enter at the end, so a double-clicked window stays open (build.cmd)
#
# What it does, and what it guards against:
# - Finds a 64-bit Python 3.9+ (python on PATH, else the py launcher).
# - Keeps the build Python in %LOCALAPPDATA%\kn5port\build-venv, outside the repo, so editors don't adopt it as the project's Python and hold
#   its python.exe open (VS Code's Black and isort servers did, and the venv couldn't be rebuilt). A healthy venv is reused; a broken one is
#   rebuilt, in a new folder if the old one is locked.
# - Stops at the first failing step, naming it.
# - Stops if dist\kn5port.exe is running, since it can't be replaced.
# - Runs PyInstaller with --clean, and once more if its helper Python dies (OSError: [Errno 22] Invalid argument in isolated\_parent.py).
param([switch]$Fresh, [switch]$Pause)
# Not "Stop": Windows PowerShell 5.1 would treat whatever a native program writes to stderr (pip, PyInstaller) as a fatal error. Each native
# step's exit code is checked instead (Run), and cmdlets that must succeed say -ErrorAction Stop
$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$pauseAtEnd = $Pause -and -not $env:CI

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) {
    Write-Host "`nBuild failed: $text" -ForegroundColor Red
    if ($pauseAtEnd) { Read-Host "Press Enter to close" | Out-Null }
    exit 1
}
function Sha256($path) {
    # .NET's, not Get-FileHash: a Windows PowerShell started from PowerShell 7 can fail to load the module that has it
    $s = [IO.File]::OpenRead((Resolve-Path $path))
    try { return -join ([Security.Cryptography.SHA256]::Create().ComputeHash($s) | ForEach-Object { $_.ToString("x2") }) } finally { $s.Close() }
}
function Run($what, [scriptblock]$cmd) {
    # stderr as plain lines: Windows PowerShell prints a native program's stderr as red NativeCommandError blocks otherwise
    & $cmd 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { Fail "$what (exit code $LASTEXITCODE)" }
}

try {
    # ---- ffmpeg goes inside the exe (the FH1 sound and radio need its XMA2 decoder and libvorbis encoder): Gyan's essentials build,
    # pinned to one version and checked against a fixed checksum. Its LICENSE ships next to it (GPLv3; source at ffmpeg.org).
    $FfVersion = "9.0.1"
    $FfUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$FfVersion/ffmpeg-$FfVersion-essentials_build.zip"
    $FfSha = "72a489eccd008c2ec2c0a5856c5c75bc3d8bbfa90166c4566865c246445e6aa3"    # bin\ffmpeg.exe in that zip
    $bin = "packaging\bin"
    $ff = "$bin\ffmpeg.exe"
    if (-not ((Test-Path $ff) -and (Test-Path "$bin\FFMPEG_LICENSE.txt") -and (Sha256 $ff) -eq $FfSha)) {
        Step "Downloading ffmpeg $FfVersion"
        New-Item -ItemType Directory -Force $bin | Out-Null
        $zip = Join-Path $env:TEMP "ffmpeg-$FfVersion-essentials_build.zip"
        $ProgressPreference = "SilentlyContinue"        # Invoke-WebRequest's progress bar slows it to a crawl
        Invoke-WebRequest $FfUrl -OutFile $zip -UseBasicParsing -ErrorAction Stop
        $tmp = Join-Path $env:TEMP "ffmpeg-$FfVersion-extract"
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        Expand-Archive $zip $tmp -ErrorAction Stop
        Copy-Item "$tmp\ffmpeg-$FfVersion-essentials_build\bin\ffmpeg.exe" $ff -Force -ErrorAction Stop
        Copy-Item "$tmp\ffmpeg-$FfVersion-essentials_build\LICENSE" "$bin\FFMPEG_LICENSE.txt" -Force -ErrorAction Stop
        Remove-Item -Recurse -Force $tmp, $zip
        $got = Sha256 $ff
        if ($got -ne $FfSha) { Remove-Item $ff; Fail "ffmpeg.exe checksum is $got, expected $FfSha" }
    }

    # ---- a Python to make the build venv from
    Step "Finding Python"
    $base = $null
    foreach ($c in @(@("python"), @("py", "-3.12"), @("py", "-3"))) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $exe = $c[0]; $pre = @($c | Select-Object -Skip 1)
        $out = & $exe @pre -c "import sys, struct; print(sys.version_info >= (3, 9) and struct.calcsize('P') == 8, sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out -match '^True (.+)$') {
            $p = $Matches[1].Trim()
            if ($p -notlike "*build-venv*" -and $p -notlike "*.venv-build*") { $base = $p; break }      # not a build venv itself
        }
    }
    if (-not $base) { Fail "no 64-bit Python 3.9 or newer found. Install Python 3.12 from python.org (tick 'Add to PATH'), then run this again." }
    Write-Host "Using $base"

    # ---- the build venv, outside the repo
    $venvRoot = Join-Path $(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }) "kn5port"
    # build-venv, or build-venv-<time> made when an old one was broken and in use: the newest that works is reused
    $venv = $null
    $old = @(Get-ChildItem $venvRoot -Directory -Filter "build-venv*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending)
    if (-not $Fresh) {
        foreach ($d in $old) {
            $p = Join-Path $d.FullName "Scripts\python.exe"
            if (-not (Test-Path $p)) { continue }
            & $p -c "import sys, pip" 2>$null
            if ($LASTEXITCODE -eq 0) { $venv = $d.FullName; break }
        }
    }
    foreach ($d in $old) {      # the rest: removed where nothing holds them open
        if ($d.FullName -ne $venv) { Remove-Item -Recurse -Force $d.FullName -ErrorAction SilentlyContinue }
    }
    if ($venv) {
        Write-Host "Reusing the build Python in $venv"
    } else {
        $venv = Join-Path $venvRoot "build-venv"
        if (Test-Path $venv) {      # couldn't be removed: something has a file in it open
            $venv = Join-Path $venvRoot ("build-venv-" + (Get-Date -Format "yyyyMMddHHmmss"))
            Write-Host "The old build Python is in use; making a new one beside it."
        }
        Step "Making the build Python in $venv"
        Run "creating the venv" { & $base -m venv $venv }
    }
    $vpy = Join-Path $venv "Scripts\python.exe"

    Step "Installing kn5port's dependencies and PyInstaller"
    Run "upgrading pip" { & $vpy -m pip install --disable-pip-version-check --quiet --upgrade pip }
    # setuptools warns about pyproject.toml's license table and the data folders it sees as packages; neither affects the exe
    $env:PYTHONWARNINGS = "ignore"
    Run "pip install" { & $vpy -m pip install --disable-pip-version-check --quiet . pyinstaller }
    Remove-Item Env:PYTHONWARNINGS

    # ---- the exe can't be replaced while it runs
    $target = Join-Path $root "dist\kn5port.exe"
    $running = Get-Process kn5port -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $target }
    if ($running) { Fail "dist\kn5port.exe is running. Close kn5port, then build again." }

    # ---- PyInstaller, once more if its helper Python dies
    Push-Location packaging
    try {
        foreach ($try in 1, 2) {
            Step ("Building the exe with PyInstaller" + $(if ($try -gt 1) { " (second try)" } else { "" }))
            $log = Join-Path $env:TEMP "kn5port-pyinstaller.log"
            # the full log goes to $log; the screen leaves out warnings that are expected: the pipeline scripts ship as data, not modules,
            # and pywebview's Android backend and pycparser's generated tables don't exist on Windows
            & $vpy -m PyInstaller kn5port.spec --noconfirm --clean --distpath ..\dist --workpath ..\build --log-level WARN 2>&1 |
                ForEach-Object { "$_" } | Tee-Object -FilePath $log |
                Where-Object { $_ -notmatch "Hidden import [`"']?(kn5port\.fh1_(pipeline|fm4)|pycparser\.(lextab|yacctab))|webview\.platforms\.android|^\s*'webview\.platforms\.android' raised" }
            if ($LASTEXITCODE -eq 0) { break }
            $helper = Select-String -Path $log -Pattern "isolated\\_parent.py|Errno 22" -Quiet
            if ($try -eq 1 -and $helper) { Write-Host "PyInstaller's helper Python stopped; trying again." -ForegroundColor Yellow; continue }
            Fail "PyInstaller (exit code $LASTEXITCODE); its full output is in $log"
        }
    } finally { Pop-Location }

    Step "Checking the exe"
    # a windowed exe: PowerShell doesn't wait for it on its own
    $vout = Join-Path $env:TEMP "kn5port-version.txt"
    $p = Start-Process -FilePath $target -ArgumentList "--version" -Wait -PassThru -NoNewWindow -RedirectStandardOutput $vout
    $v = Get-Content $vout -Raw -ErrorAction SilentlyContinue
    $v = if ($v) { $v.Trim() } else { "" }
    if ($p.ExitCode -ne 0 -or $v -notmatch '^kn5port ') { Fail "the new exe didn't start properly (exit code $($p.ExitCode)): $v" }
    $mb = [math]::Round((Get-Item $target).Length / 1MB)
    Write-Host "`nBuilt dist\kn5port.exe ($v, $mb MB)" -ForegroundColor Green
    if ($pauseAtEnd) { Read-Host "Press Enter to close" | Out-Null }
} catch {
    Fail $_.Exception.Message
}
