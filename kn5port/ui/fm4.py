"""The app's Forza Motorsport 4 tab (beta): one FM4 circuit, every layout, as a BeamNG level.

The game comes as FH1's does (xbox.detect): a disc image, a GOD package or extracted files. FM4's first disc holds every
track. From an ISO or GOD package, files are copied into <work>/game as they're needed: the database, string tables, menu
textures and surface types (about 0.4 GB) with the track list, then each track's own folder and AI lines (65 to 250 MB)
before its first build.

1. extract   those files, out of an ISO or GOD package (skipped for a folder)
2. tracks    fm4ui.py --list: FM4's own track names and photos into <work>/catalogue (seconds, once per game copy)
3. build     fm4build.py <track> into <mods>/unpacked/fm4_<track>; it unpacks the track's bin.zip into <work>/<track>/x

The scripts import FH1's pipeline modules from ../pipeline, so outside a development checkout they are copied into
<work>/app_scripts/{fm4,pipeline} first.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

from .. import runtime, xbox
from . import fh1 as F

REPO_FM4 = F.REPO_FH1 / "fm4"
# the circuits that have been built and driven in BeamNG; the rest build, but nobody has looked at them in the game yet
CHECKED = {"Tsukuba"}
# the circuits fm4build.py builds to the end (all 24, 2026-11-23); others would be listed as not building yet
WORKING = {"Alps", "AmalfiGP", "Autopark", "Catalunya", "CrownJewel", "FujimiKaido", "Hockenheim", "Indi", "Infin", "LagunaSeca", "LeMans", "MapleValley", "Motegi",
           "Mugello", "Nurburgring", "NurburgringF1", "RoadAmerica", "RoadAtlanta", "Sebring", "Silverstone", "SunsetInfield", "Suzuka",
           "TopGear", "Tsukuba"}
COMMON_GB = 0.45        # the files every track needs, copied out of an ISO or GOD package
TRACK_GB = 0.25         # a track's own files, likewise (at most)
WORK_GB = 0.7           # a track's unpacked bin.zip and caches (Nurburgring's 0.64 GB is the most)
LEVEL_GB = 0.8          # a built level: 0.14 GB (Top Gear) to 0.68 GB (Nurburgring)
# what fm4ui.py and fm4build.py open, besides the track's own files
COMMON = re.compile(r"^media/(db/.*|stringtables/.*|ui/textures\.zip|physics/.*)$", re.I)
SOURCE_MARK = ".kn5port-source.json"


def track_files(track):
    return re.compile(rf"^media/(tracks/{re.escape(track)}/.*|ai/tracks/{re.escape(track)}_.*)$", re.I)


def scripts_src():
    """(fm4 scripts folder, pipeline folder): the exe's bundled copies, the repo's, or the installed package's"""
    if runtime.needed():
        base = Path(getattr(sys, "_MEIPASS", ""))
        fm4, pipe = base / "fh1_fm4", base / "fh1_pipeline"
    elif (REPO_FM4 / "fm4build.py").exists():
        fm4, pipe = REPO_FM4, F.REPO_FH1 / "pipeline"
    else:
        base = Path(__file__).resolve().parents[1]
        fm4, pipe = base / "fh1_fm4", base / "fh1_pipeline"
    return (fm4, pipe) if (fm4 / "fm4build.py").exists() and (pipe / "tex.py").exists() else None


def default_work():
    if (REPO_FM4 / "fm4build.py").exists():
        return REPO_FM4                     # a development checkout: its tracks are unpacked in fh1/fm4/<track>
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "kn5port" / "fm4"


def plain_tracks(game, have=()):
    """the tracks from gamedb alone (folder names), for before the catalogue is read: those with a bin.zip, or in have"""
    media, have = Path(game) / "Media", {h.lower() for h in have}
    try:
        db = sqlite3.connect(f"file:{media / 'db' / 'gamedb.slt'}?mode=ro", uri=True)
        try:
            names = [m for (m,) in db.execute("select distinct MediaName from Tracks where Active=1 and IsHomespace=0 order by MediaName")]
        finally:
            db.close()
    except sqlite3.Error:
        return []
    return [{"media": m, "name": m, "location": "", "layouts": []} for m in names
            if m.lower() in have or (media / "Tracks" / m / "bin.zip").exists()]


class FM4:
    def __init__(self, settings, save, fh1):
        self.settings = settings
        self.save = save
        self.fh1 = fh1                  # shares the mods folder and the Python setup; one Forza job at a time
        self.job = None
        self.lock = threading.Lock()

    def work(self):
        return Path(self.settings.get("fm4_work") or default_work())

    def source(self):
        return xbox.detect(self.settings.get("fm4_game"), title=xbox.FM4)

    def mark(self):
        """what's been copied out of an ISO or GOD package: {key, disc: the tracks on it, copied: tracks copied}"""
        return F._read_json(self.work() / "game" / SOURCE_MARK)

    def game_dir(self, det=None):
        """the folder holding Media/ that the scripts read, or None while the common files still have to be copied"""
        det = det or self.source()
        if det["kind"] == "folder":
            return Path(det["path"])
        if det["kind"] in ("iso", "god") and self.mark().get("key") == F._source_key(det):
            return self.work() / "game"
        return None

    def copied(self, det, track):
        """the track's own files are there to read"""
        return det["kind"] == "folder" or track.lower() in {t.lower() for t in self.mark().get("copied", [])}

    def catalogue(self, game):
        """FM4's own track names and photos, if read for this game copy"""
        c = F._read_json(self.work() / "catalogue" / "tracks.json")
        return c["tracks"] if game and c.get("tracks") and Path(c.get("dump", "")) == Path(game) else None

    def photo(self, media):
        p = self.work() / "catalogue" / f"{media}.jpg"
        return p if p.is_file() and p.parent == (self.work() / "catalogue") else None

    def update(self, body):
        for k in ("fm4_game", "fm4_work"):
            if k in body:
                self.settings[k] = str(body[k] or "").strip().strip('"')
        self.save(self.settings)

    def busy(self):
        return self.job is not None and self.job.alive()

    def state(self):
        det = self.source()
        game = self.game_dir(det)
        mods = self.fh1.mods()
        disc = self.mark().get("disc", []) if det["kind"] in ("iso", "god") else []
        cat = self.catalogue(game)
        tracks = cat if cat is not None else (plain_tracks(game, disc) if game else [])
        for t in tracks:
            lvl = f"fm4_{t['media'].lower()}"
            info = mods / "unpacked" / lvl / "levels" / lvl / "info.json"
            t.update(level=lvl, checked=t["media"] in CHECKED, working=t["media"] in WORKING, photo=bool(self.photo(t["media"])),
                     installed=int(info.stat().st_mtime) if info.exists() else 0, copied=self.copied(det, t["media"]),
                     extracted=(self.work() / t["media"].lower() / "x").is_dir())
        py = F.interpreter()
        return {
            "game": self.settings.get("fm4_game", ""), "source": det, "extracted": bool(game), "work": str(self.work()),
            "work_default": str(default_work()), "tracks": tracks, "catalogue": cat is not None,
            "scripts": bool(scripts_src()), "missing_packages": F.missing_packages(), "python": str(py or ""),
            "runtime": {"needed": runtime.needed(), "ready": bool(py), "mb": runtime.DOWNLOAD_MB, "dir": str(runtime.root())},
            "mods": str(mods) if str(mods) not in ("", ".") else "", "free_gb": F._free_gb(self.work()),
            "mods_free_gb": F._free_gb(mods), "same_drive": F._drive(self.work()) == F._drive(mods),
            "need": {"common": COMMON_GB, "track": TRACK_GB, "work": WORK_GB, "level": LEVEL_GB},
            "job": self.job.view(len(self.job.lines)) if self.job else None,
        }

    def need_gb(self, s, t):
        """(work folder GB, mods GB) a build of track t still needs"""
        work = (0 if s["extracted"] else COMMON_GB) + (0 if not t or t["copied"] else TRACK_GB) + \
            (0 if t and t["extracted"] else WORK_GB)
        return work, LEVEL_GB

    def problems(self, opts):
        s = self.state()
        out = []
        if s["runtime"]["needed"] and not s["runtime"]["ready"]:
            out.append("Set up Python for the converter first (under Before you build).")
        if not s["scripts"]:
            out.append("The FM4 scripts weren't found next to kn5port.")
        if s["missing_packages"]:
            out.append("Missing Python packages: " + ", ".join(s["missing_packages"]) + ".")
        if not s["source"]["kind"]:
            out.append("Pick your copy of Forza Motorsport 4.")
        if not s["mods"]:
            out.append("Set your BeamNG mods folder under Folders.")
        t = None
        if opts.get("task") != "tracks":
            t = next((t for t in s["tracks"] if t["media"] == opts.get("track")), None)
            if not t:
                out.append("Pick a track to build.")
            elif not t["working"]:
                out.append(f"{t['name']} doesn't build yet.")
            if F.beamng_running():
                out.append("Close BeamNG first: building replaces the level's files, which BeamNG keeps open.")
        work, mods = self.need_gb(s, t)
        if opts.get("task") == "tracks":
            mods = 0
        if s["same_drive"]:
            if s["free_gb"] is not None and s["free_gb"] < work + mods + 0.5:
                out.append(f"Not enough free space: about {work + mods:.1f} GB needed, {s['free_gb']:.1f} GB free.")
        elif (s["free_gb"] is not None and s["free_gb"] < work + 0.5) or \
                (s["mods_free_gb"] is not None and s["mods_free_gb"] < mods + 0.3):
            out.append(f"Not enough free space: about {work:.1f} GB for the work folder and {mods:.1f} GB for the level.")
        if self.fh1.busy():
            out.append("Wait for the Forza Horizon job to finish.")
        return out

    def start(self, opts):
        with self.lock:
            if self.busy():
                raise RuntimeError("a Forza Motorsport job is already running")
            why = self.problems(opts)
            if why:
                raise RuntimeError(" ".join(why))
            self.job = Job(self, opts)
            return self.job

    def setup_task(self):
        with self.lock:
            if self.busy() or self.fh1.busy():
                raise RuntimeError("a Forza job is already running")
            self.job = Job(self, {"task": "runtime" if runtime.needed() else "pip"})
            return self.job


class Job(F.Job):
    """FH1's job (its log, stages, copying out of a disc, cancelling and Python setup), running FM4's scripts"""

    def view(self, since=0):
        return {**super().view(since), "track": self.opts.get("track")}

    def _scripts(self, game):
        """(command prefix, folder to run from, env): the scripts in place in a checkout, else a fresh copy under the work folder"""
        f = self.fh1                # an FM4 here; Job keeps FH1's attribute name
        fm4, pipe = scripts_src()
        work = f.work()
        if fm4 != REPO_FM4:
            dst = work / "app_scripts"
            if dst.exists():
                shutil.rmtree(dst)
            ign = shutil.ignore_patterns("__pycache__", "*.pyc", "*.o", "*.lib", "*.exp")
            shutil.copytree(fm4, dst / "fm4", ignore=ign); shutil.copytree(pipe, dst / "pipeline", ignore=ign)
            fm4 = dst / "fm4"
        py, env = F.converter(work, FM4_DUMP=str(game), FM4_WORK=str(work), KN5PORT_MODS=str(f.fh1.mods()))
        return py, fm4, env

    def _copy(self, det, want, what):
        """copy files out of the ISO or GOD package into <work>/game, then update its mark with what(mark)"""
        f = self.fh1
        dest = f.work() / "game"
        mark = f.mark() if f.mark().get("key") == F._source_key(det) else {}
        self._extract(det, dest, want)
        what(mark)
        (dest / SOURCE_MARK).write_text(json.dumps({**mark, "key": F._source_key(det)}))

    def _convert(self):
        f = self.fh1
        work = f.work(); work.mkdir(parents=True, exist_ok=True)
        det = f.source()
        game = f.game_dir(det)
        track = self.opts.get("track") if self.opts.get("task") != "tracks" else None
        need_common = not game
        need_track = bool(track) and not f.copied(det, track)
        self.stages = ["extract"] * (need_common or need_track) + ["tracks"] * (need_common or not f.catalogue(game)) + \
            ["build"] * bool(track)
        if "extract" in self.stages:
            self.stage = "extract"
            if need_common:
                disc = []

                def common(p):
                    m = re.match(r"^media/tracks/([^/]+)/bin\.zip$", p, re.I)
                    if m:
                        disc.append(m.group(1))
                    return bool(COMMON.match(p))
                self._copy(det, common, lambda m: m.update(disc=sorted(disc), copied=[]))
                game = work / "game"
                if not (game / "Media" / "db" / "gamedb.slt").exists():
                    raise RuntimeError("This copy has no Media/db/gamedb.slt, so it isn't Forza Motorsport 4, or it's incomplete.")
            if need_track:
                self.say(f"Copying {track}'s files")
                self._copy(det, lambda p: bool(track_files(track).match(p)),
                           lambda m: m.update(copied=sorted(set(m.get("copied", [])) | {track})))
        py, fm4, env = self._scripts(game)
        if "tracks" in self.stages:
            self.stage = "tracks"
            self.say("Reading FM4's track names and photos")
            disc = f.mark().get("disc", []) if det["kind"] in ("iso", "god") else []
            self._cmd(py + ["fm4ui.py", "--list", str(work / "catalogue")] + disc, fm4, env)
        if not track:
            return
        lvl = f"fm4_{track.lower()}"
        out = f.fh1.mods() / "unpacked" / lvl
        self.stage = "build"
        self.say(f"Building {track} as {lvl} into {out}")
        if F.beamng_running():
            raise RuntimeError("BeamNG is running: close it, then build again.")
        self._cmd(py + ["fm4build.py", track, str(out)], fm4, env)
        self.stage = "done"
        self.say(f"Done. Fully restart BeamNG, then pick the track (level {lvl}) from the level list.")
