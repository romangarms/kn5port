"""Update check and self-update from kn5port's GitHub releases.

check() asks the GitHub API for the latest release (drafts and prereleases are never returned by /releases/latest)
and compares its tag with __version__. For the packaged kn5port.exe, install() downloads the release's kn5port.exe
next to the running one, checks its size and the SHA-256 GitHub publishes for every release asset, then swaps
the files: Windows won't let a running exe be overwritten or deleted, but it can be renamed, so the running exe
becomes kn5port.exe.old, the download takes its name, and the new exe is started. The next start deletes the .old.

Running from source (``pip install .``) only reports that a release exists; update that with pip or git.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import __version__

REPO = "romangarms/kn5port"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "kn5port.exe"
OLD_SUFFIX = ".old"             # kn5port.exe -> kn5port.exe.old while it's replaced
DOWNLOAD_SUFFIX = ".download"


def parse_version(s):
    """'v1.2.3' or '1.2.3' -> (1, 2, 3); anything else -> None"""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(s or "").strip())
    return tuple(map(int, m.groups())) if m else None


def can_self_update():
    """only the packaged Windows exe replaces itself"""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32" \
        and Path(sys.executable).suffix.lower() == ".exe"


def fetch_latest(timeout=10):
    req = urllib.request.Request(LATEST_API, headers={"User-Agent": "kn5port-updater",
                                                       "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def describe(release, current=__version__):
    """the parts of a GitHub release JSON the app uses, and whether it's newer than `current`"""
    tag = release.get("tag_name") or ""
    asset = next((a for a in release.get("assets") or [] if a.get("name") == ASSET), None)
    digest = (asset or {}).get("digest") or ""
    newer = parse_version(tag) is not None and parse_version(current) is not None \
        and parse_version(tag) > parse_version(current)
    return {
        "version": tag.lstrip("v"), "newer": newer, "page": release.get("html_url") or RELEASES_PAGE,
        "notes": (release.get("body") or "").strip()[:4000],
        "asset": {"url": asset["browser_download_url"], "size": int(asset.get("size") or 0),
                  "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None}
        if asset and asset.get("browser_download_url") else None,
    }


def download(asset, dest, progress=lambda f: None, timeout=60):
    """download the release exe to `dest`, checked against its size, SHA-256 and the PE header"""
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256(); got = 0
    req = urllib.request.Request(asset["url"], headers={"User-Agent": "kn5port-updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk); h.update(chunk); got += len(chunk)
                if asset["size"]:
                    progress(min(got / asset["size"], 1.0))
        if asset["size"] and got != asset["size"]:
            raise RuntimeError(f"the download stopped early ({got} of {asset['size']} bytes). Try again.")
        if asset["sha256"] and h.hexdigest() != asset["sha256"]:
            raise RuntimeError("the download doesn't match the checksum GitHub lists for it, so it wasn't used. Try again.")
        with open(tmp, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError("the download isn't a Windows program, so it wasn't used.")
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return dest


def swap(exe, new):
    """put `new` in place of the running `exe`; returns where the old exe went"""
    old = exe.with_name(exe.name + OLD_SUFFIX)
    if old.exists():
        try:
            old.unlink()
        except OSError:                          # still held by an earlier instance: pick a fresh name
            old = exe.with_name(f"{exe.name}{OLD_SUFFIX}{int(time.time())}")
    os.replace(exe, old)                         # allowed for a running exe on Windows
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)                     # put the running one back
        raise
    return old


def cleanup(exe=None, wait=30.0):
    """delete what an update left next to the exe: the old exe (once its process has exited) and stray downloads"""
    exe = Path(exe or sys.executable)
    leftovers = [p for p in exe.parent.glob(exe.name + ".*")
                 if p.name.startswith(exe.name + OLD_SUFFIX) or p.name.endswith((DOWNLOAD_SUFFIX, ".part"))]
    deadline = time.time() + wait
    while leftovers:
        for p in list(leftovers):
            try:
                p.unlink()
                leftovers.remove(p)
            except FileNotFoundError:
                leftovers.remove(p)
            except OSError:
                pass
        if not leftovers or time.time() > deadline:
            break
        time.sleep(1)


def relaunch(exe):
    """start `exe` as a fresh, independent kn5port"""
    env = dict(os.environ)
    # a onefile exe started by another one would otherwise reuse the parent's unpacked files (PyInstaller >= 6.9)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in list(env):
        if k.startswith(("_MEI", "_PYI")):
            env.pop(k)
    flags = 0x00000008 | 0x00000200              # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Updater:
    """state for the UI: idle -> checking -> current | available | error; available -> downloading -> restarting"""

    def __init__(self):
        self.state = "idle"
        self.info = None
        self.error = ""
        self.progress = 0.0
        self.checked = None
        self.lock = threading.Lock()
        self.quit = None                         # set by the desktop app: closes the window so the process exits

    def view(self):
        i = self.info or {}
        return {"state": self.state, "current": __version__, "latest": i.get("version"), "notes": i.get("notes", ""),
                "page": i.get("page", RELEASES_PAGE), "can_install": can_self_update() and bool(i.get("asset")),
                "progress": round(self.progress, 3), "error": self.error, "checked": self.checked}

    def check(self, background=True):
        with self.lock:
            if self.state in ("checking", "downloading", "restarting"):
                return
            self.state, self.error = "checking", ""

        def run():
            try:
                self.info = describe(fetch_latest())
                self.state = "available" if self.info["newer"] else "current"
            except Exception as e:
                self.state, self.error = "error", f"Couldn't reach GitHub ({type(e).__name__}: {e})"
            self.checked = int(time.time())
        if background:
            threading.Thread(target=run, daemon=True).start()
        else:
            run()

    def install(self):
        """download and swap in the new exe, then restart into it; errors land in self.error"""
        with self.lock:
            if self.state != "available" or not can_self_update() or not (self.info or {}).get("asset"):
                raise RuntimeError("no update to install")
            self.state, self.error, self.progress = "downloading", "", 0.0
        exe = Path(sys.executable)

        def run():
            try:
                new = exe.with_name(exe.name + DOWNLOAD_SUFFIX)
                try:
                    open(new, "wb").close()
                except OSError:
                    raise RuntimeError(f"kn5port can't write to {exe.parent}. Download the new version from the "
                                       f"releases page instead, or move kn5port.exe to a folder you can write to.")
                download(self.info["asset"], new, progress=lambda f: setattr(self, "progress", f))
                swap(exe, new)
                self.state = "restarting"
                relaunch(exe)
                time.sleep(0.8)                  # let the page get its last answer before the window closes
                if self.quit:
                    self.quit()
                else:
                    os._exit(0)
            except Exception as e:
                self.state, self.error = "available", str(e) or type(e).__name__
                cleanup(exe, wait=0)
        threading.Thread(target=run, daemon=True).start()
