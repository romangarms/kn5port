"""Locate Steam libraries, Assetto Corsa, the BeamNG.drive install and the user's BeamNG mods folder.

Steam itself is found through the Windows registry (any drive), then every library it knows about is read from
``libraryfolders.vdf``. A game's library is the one holding its ``appmanifest_<appid>.acf``.
"""
import os
import re
import sys
from pathlib import Path

AC_APPID = "244210"
BEAMNG_APPID = "284160"


def _steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, val)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
    roots += [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
              Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"]
    return [r for r in roots if (r / "steamapps").exists()]


def steam_libraries():
    """Every Steam library folder (the ones containing 'steamapps'), on any drive."""
    libs = []
    for root in _steam_roots():
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
    seen, out = set(), []
    for lib in libs:
        key = str(lib).lower().rstrip("\\/")
        if key not in seen and (lib / "steamapps").exists():
            seen.add(key); out.append(lib)
    return out


def _game_dir(appid, folder):
    libs = steam_libraries()
    # the library that has the app manifest is authoritative; otherwise any library with the folder
    for lib in libs:
        if (lib / "steamapps" / f"appmanifest_{appid}.acf").exists() and (lib / "steamapps" / "common" / folder).exists():
            return lib / "steamapps" / "common" / folder
    for lib in libs:
        if (lib / "steamapps" / "common" / folder).exists():
            return lib / "steamapps" / "common" / folder
    return None


def find_ac_tracks(explicit=None):
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    ac = _game_dir(AC_APPID, "assettocorsa")
    return ac / "content" / "tracks" if ac and (ac / "content" / "tracks").exists() else None


def find_install(explicit=None):
    """BeamNG.drive install folder (contains content/levels)."""
    for c in (explicit, os.environ.get("BEAMNG_PATH")):
        if c and (Path(c) / "content" / "levels").exists():
            return Path(c)
        if c and Path(c).name == "levels" and Path(c).exists():      # a content/levels folder was given
            return Path(c)
    g = _game_dir(BEAMNG_APPID, "BeamNG.drive")
    return g if g and (g / "content" / "levels").exists() else None


def find_mods(explicit=None):
    """The user's mods folder: %LOCALAPPDATA%/BeamNG/BeamNG.drive/current/mods on 0.32+, older layouts too."""
    if explicit:
        return Path(explicit)
    local = os.environ.get("LOCALAPPDATA")
    cands = []
    if local:
        cands.append(Path(local) / "BeamNG" / "BeamNG.drive" / "current" / "mods")
        old = Path(local) / "BeamNG.drive"
        if old.exists():
            cands += sorted(old.glob("*/mods"), reverse=True)
    cands.append(Path.home() / "Documents" / "BeamNG.drive" / "mods")
    found = next((c for c in cands if c.exists()), None)
    if found is None and local and (Path(local) / "BeamNG" / "BeamNG.drive" / "current").exists():
        found = Path(local) / "BeamNG" / "BeamNG.drive" / "current" / "mods"     # game run, folder not yet made
    return found
