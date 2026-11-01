"""Locate a BeamNG.drive install and the user's mods folder."""
import os
import re
from pathlib import Path


def steam_libraries():
    roots = []
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)") + r"\Steam",
                 os.environ.get("ProgramFiles", r"C:\Program Files") + r"\Steam",
                 os.path.expanduser("~/.steam/steam"), os.path.expanduser("~/.local/share/Steam")):
        vdf = Path(base) / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            roots.append(Path(base))
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="replace")):
                roots.append(Path(m.group(1).replace("\\\\", "\\")))
    seen, out = set(), []
    for r in roots:
        if str(r).lower() not in seen:
            seen.add(str(r).lower()); out.append(r)
    return out


def find_install(explicit=None):
    """BeamNG.drive install folder (contains content/levels)."""
    cands = [explicit, os.environ.get("BEAMNG_PATH")] + \
            [str(lib / "steamapps" / "common" / "BeamNG.drive") for lib in steam_libraries()]
    for c in cands:
        if c and (Path(c) / "content" / "levels").exists():
            return Path(c)
        if c and Path(c).name == "levels" and Path(c).exists():      # a content/levels folder was given
            return Path(c)
    return None


def find_mods(explicit=None):
    """The user's mods folder (BeamNG 0.32+: %LOCALAPPDATA%/BeamNG/BeamNG.drive/current/mods)."""
    if explicit:
        return Path(explicit)
    local = os.environ.get("LOCALAPPDATA")
    cands = []
    if local:
        cands += [Path(local) / "BeamNG" / "BeamNG.drive" / "current" / "mods"]
        old = Path(local) / "BeamNG.drive"
        if old.exists():
            cands += sorted(old.glob("*/mods"), reverse=True)
    cands += [Path.home() / "Documents" / "BeamNG.drive" / "mods"]
    return next((c for c in cands if c.exists()), None)
