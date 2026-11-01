# PyInstaller spec for kn5port.exe (one file, windowed). Build with packaging/build.ps1.
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    datas=[("../kn5port/ui/static", "kn5port/ui/static")],
    hiddenimports=collect_submodules("kn5port") + collect_submodules("webview"),
    excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="kn5port",
    console=False,            # windowed; conversions re-launch the exe and stream their output over a pipe
    upx=False,
    icon="kn5port.ico",       # regenerate with: python packaging/make_icon.py
)
