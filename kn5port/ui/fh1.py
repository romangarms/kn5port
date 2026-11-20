"""The app's Forza Horizon 1 tab: checks, settings and the conversion job.

A conversion runs the fh1/pipeline scripts as subprocesses, from a copy of the pipeline inside the work folder
(``<work>/app_pipeline``), because the scripts read and write their data one folder up (``../``):

1. extract   copy the needed game files out of an ISO or GOD package into <work>/game (skipped for a folder)
2. prepare   prepare.py: decode the map's tables into <work> (once per game copy, about 3 minutes)
3. rally     prepare_dlc.py: the Rally Expansion's tables into <work>/dirt (beta; only for that chunk, once per DLC copy)
4. build     build3.py and its post-build scripts, into <mods>/unpacked/<level>
5. radio     radio.py, the separate fh1_radio mod (optional)

The Rally Expansion (chunk coloradodirt) is its own track, read from the DLC package's Media folder, and builds from its own
work folder <work>/dirt. Its prepare step merges in base Colorado's tables, so Colorado's are prepared first.
"""
import ctypes
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import beamng, runtime, xbox

REPO_FH1 = Path(__file__).resolve().parents[2] / "fh1"

# key, level, name, what it is, level size on disk in GB, peak RAM in GB while building, RAM in GB to play it
# (the whole map runs on 16 GB, just; less isn't stopped, only warned about)
CHUNKS = [
    ("colorado", "fh1_colorado", "Whole map", "All of Colorado, 16 km square on a 4 m grid. About 10 minutes.", 3.3, 6, 16),
    ("redrock", "fh1_redrock", "Red Rock", "4 km around Red Rock on a 2 m grid. A quick test, about 2 minutes.", 0.3, 3, 8),
    ("clearsprings", "fh1_clearsprings", "Clear Springs", "4 km around Clear Springs on a 2 m grid. About 4 minutes.", 0.5, 3, 8),
    ("coloradodirt", "fh1_coloradodirt", "Rally Expansion",
     "The Rally Expansion DLC's Colorado with its rally stages. Needs the DLC. Not fully working yet. About 20 minutes.", 2.0, 6, 16),
]
CHUNK = {c[0]: c for c in CHUNKS}
BETA = {"coloradodirt"}
RALLY = "coloradodirt"
RALLY_DIR = "dirt"          # the Rally Expansion's work folder, under the main one (buildq.py's WORKS)
RALLY_PREPARED_GB = 2.5
RALLY_FILES = ("DLCZips/ColoradoDirt_pri_65.zip", "DLCZips/1600_pri_65.zip")
RALLY_TITLE = "4D5309C9"    # FH1's title id; its DLC packages sit in <content>/4D5309C9/00000002/<package>/Media
PACKAGES = [("numpy", "numpy"), ("scipy", "scipy"), ("numba", "numba"), ("PIL", "pillow")]
GAME_GB = 4.2           # the part of media/ the pipeline reads
PREPARED_GB = 2.8       # x/, tables, caches
RADIO_GB = 0.35
CACHE_GB = 0.6          # what a build adds to the work folder: ddscache (textures), impcache, logs, numbacache

# what to copy out of an ISO or GOD package: everything the pipeline opens
WANT = re.compile(r"^(default\.xex|media/[^/]+\.zip|media/tracks/colorado/.*|media/audio/radio/.*|"
                  r"media/audio/tracks/colorado/.*|media/db/.*|media/stringtables/.*|media/ui/textures/.*)$", re.I)
PREPARED_FILES = ["ents.json", "list.json", "fnmap.pkl", "cat2.pkl", "pvsrec.pkl", "mt.npy", "meshtab.pkl",
                  "texshader.pkl", "reg.json", "zonerec.pkl"]


def pipeline_src():
    """the pipeline scripts: the exe's bundled copy, the repo's fh1/pipeline, or the copy installed with the package"""
    if runtime.needed():
        p = Path(getattr(sys, "_MEIPASS", "")) / "fh1_pipeline"
        return p if (p / "build3.py").exists() else None
    if (REPO_FH1 / "pipeline" / "build3.py").exists():
        return REPO_FH1 / "pipeline"
    p = Path(__file__).resolve().parents[1] / "fh1_pipeline"
    return p if (p / "build3.py").exists() else None


def interpreter():
    """the Python that runs the pipeline: this one, or the exe's private one once it's set up (else None)"""
    return runtime.python() if runtime.needed() else Path(sys.executable)


def missing_packages():
    if runtime.needed():
        return []                       # the private Python comes with all of them
    return [pip for mod, pip in PACKAGES if importlib.util.find_spec(mod) is None]


def default_work():
    if (REPO_FH1 / "pipeline").exists() and (REPO_FH1 / "x").is_dir():
        return REPO_FH1                         # a development checkout with its data already prepared
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "kn5port" / "fh1"


def _free_gb(p):
    p = Path(p)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free / 2 ** 30
    except OSError:
        return None


def _drive(p):
    """the volume a path is on (its nearest existing folder's), for telling whether two folders share free space"""
    p = Path(p).resolve()
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return p.stat().st_dev
    except OSError:
        return str(p.anchor).lower()


def _ram_gb():
    if sys.platform != "win32":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
        except (ValueError, OSError, AttributeError):
            return None

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + \
                   [(n, ctypes.c_ulonglong) for n in ("total", "avail", "tpf", "apf", "tv", "av", "aev")]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    return m.total / 2 ** 30 if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) else None


def find_ffmpeg():
    """the ffmpeg inside kn5port.exe (packaging/build.ps1 puts it there), or for a source install the one on PATH. Always an absolute path"""
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    if getattr(sys, "_MEIPASS", None) and bundled.exists():
        return str(bundled)
    hit = shutil.which("ffmpeg")
    return os.path.abspath(hit) if hit else ""


def _is_rally_media(p):
    return all((p / f).is_file() for f in RALLY_FILES)


def detect_dlc(path):
    """the Rally Expansion's Media folder from the path given: that folder, the package folder above it, FH1's
    00000002 folder, or a Xenia content or Xenia folder. {"path": the Media folder or None, "why": what was found}"""
    path = str(path or "").strip().strip('"')
    if not path:
        return {"path": None, "why": ""}
    p = Path(path)
    if p.is_file():
        return {"path": None, "why": "This is a single file. If it's the DLC package copied off a console, install it in Xenia "
                                     "(File > Install Content), then pick Xenia's content folder."}
    if not p.is_dir():
        return {"path": None, "why": "This folder doesn't exist."}
    cands = [p, p / "Media"]
    for root in (p, p / RALLY_TITLE, p / RALLY_TITLE / "00000002", p / "content" / RALLY_TITLE / "00000002"):
        try:
            if root.is_dir():
                cands += [q / "Media" for q in root.iterdir() if q.is_dir()]
        except OSError:
            pass
    for c in cands:
        if _is_rally_media(c):
            return {"path": str(c), "why": "Found the Rally Expansion."}
    return {"path": None, "why": "No Rally Expansion here: its package's Media folder holds DLCZips\\ColoradoDirt_pri_65.zip."}


def default_dlc():
    """the Rally Expansion where Xenia installs it by default, if it's there"""
    xenia = Path(os.path.expanduser("~")) / "Documents" / "Xenia"
    return detect_dlc(xenia)["path"] if xenia.is_dir() else None


def beamng_running():
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False
    return "beamng.drive" in out


def _read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _source_key(det):
    """identifies a game copy: path plus the size and time of its main file, so a replaced copy is extracted again"""
    p = Path(det["path"])
    st = p.stat() if p.exists() else None
    return f"{det['kind']}|{p}|{st.st_size if st else 0}|{int(st.st_mtime) if st else 0}"


class FH1:
    def __init__(self, settings, save):
        self.settings = settings
        self.save = save
        self.job = None
        self.lock = threading.Lock()

    # -- settings
    def work(self):
        return Path(self.settings.get("fh1_work") or default_work())

    def mods(self):
        return Path(self.settings.get("mods") or beamng.find_mods() or "")

    def beamng(self):
        """the BeamNG install (its stock levels donate vegetation): the Folders setting, BEAMNG_PATH, or Steam"""
        return beamng.find_install(self.settings.get("beamng") or None)

    def missing_folders(self):
        """Folders-dialog fields the build needs that aren't set or found, for the page to ask for"""
        return [k for k, ok in (("beamng", self.beamng()), ("mods", str(self.mods()) not in ("", "."))) if not ok]

    def rally_work(self):
        return self.work() / RALLY_DIR

    def dlc(self):
        """the Rally Expansion: what the setting points at, else Xenia's default place"""
        d = detect_dlc(self.settings.get("fh1_dlc"))
        if not d["path"] and not self.settings.get("fh1_dlc"):
            found = default_dlc()
            if found:
                d = {"path": found, "why": "Found the Rally Expansion in Xenia's content folder."}
        return d

    def rally_prepared(self, game):
        """the Rally Expansion's tables are in <work>/dirt, for this game copy and DLC, merged with base Colorado's"""
        w, dlc = self.rally_work(), self.dlc()["path"]
        if not (game and dlc and all((w / f).exists() for f in PREPARED_FILES) and (w / "merge.json").exists()):
            return False
        mark = _read_json(w / "prepared.json")
        if mark:
            return Path(mark.get("dump", "")) == Path(game) and Path(mark.get("dlc", "")) == Path(dlc)
        # prepared by hand with prepare_dlc.py (a development checkout): trust its track.json and dump_path.txt
        t, saved = _read_json(w / "track.json"), w / "dump_path.txt"
        return (Path(t.get("dlc", "")) == Path(dlc) and saved.exists()
                and Path(saved.read_text(encoding="utf8").strip()) == Path(game))

    def update(self, body):
        for k in ("fh1_game", "fh1_work", "fh1_dlc"):
            if k in body:
                self.settings[k] = str(body[k] or "").strip().strip('"')
        self.save(self.settings)

    # -- what's there
    def game_dir(self, det=None):
        """the extracted game folder the pipeline reads, or None if it still has to be extracted"""
        det = det or xbox.detect(self.settings.get("fh1_game"))
        if det["kind"] == "folder":
            return Path(det["path"])
        if det["kind"] in ("iso", "god"):
            mark = _read_json(self.work() / "game" / ".kn5port-source.json")
            if mark.get("key") == _source_key(det) and (self.work() / "game/media/tracks/colorado/bin.zip").exists():
                return self.work() / "game"
        return None

    def prepared(self, game):
        mark = _read_json(self.work() / "prepared.json")
        files = all((self.work() / f).exists() for f in PREPARED_FILES)
        if game and files and Path(mark.get("dump", "")) == Path(game):
            return True
        # a development checkout prepared by hand before this app existed: trust its dump_path.txt
        saved = self.work() / "dump_path.txt"
        return bool(game and files and not mark and saved.exists()
                    and Path(saved.read_text(encoding="utf8").strip()) == Path(game))

    def state(self):
        det = xbox.detect(self.settings.get("fh1_game"))
        game = self.game_dir(det)
        work = self.work()
        mods = self.mods()
        levels = {}
        for key, lvl, *_ in CHUNKS:
            info = mods / "unpacked" / lvl / "levels" / lvl / "info.json"
            levels[key] = int(info.stat().st_mtime) if info.exists() else 0
        py = interpreter()
        s = {
            "game": self.settings.get("fh1_game", ""), "work": str(work), "work_default": str(default_work()),
            "source": det, "extracted": bool(game), "prepared": self.prepared(game),
            "pipeline": bool(pipeline_src()), "missing_packages": missing_packages(), "python": str(py or ""),
            "runtime": {"needed": runtime.needed(), "ready": bool(py), "mb": runtime.DOWNLOAD_MB,
                        "dir": str(runtime.root())},
            "ffmpeg": find_ffmpeg(), "free_gb": _free_gb(work), "mods_free_gb": _free_gb(mods),
            "ram_gb": _ram_gb(), "mods": str(mods) if str(mods) not in ("", ".") else "",
            "levels": levels, "radio": (mods / "unpacked" / "fh1_radio").exists(),
            "chunks": [{"key": c[0], "level": c[1], "name": c[2], "desc": c[3], "gb": c[4], "ram": c[5], "play_ram": c[6],
                        "beta": c[0] in BETA, "dlc": c[0] == RALLY} for c in CHUNKS],
            "dlc": self.settings.get("fh1_dlc", ""), "dlc_source": self.dlc(),
            "rally_prepared": self.rally_prepared(game),
            "need": {"game": GAME_GB, "prepared": PREPARED_GB, "radio": RADIO_GB, "cache": CACHE_GB,
                     "rally": RALLY_PREPARED_GB},
            "job": self.job.view(len(self.job.lines)) if self.job else None,
        }
        s["disk"] = {c[0]: {r: self.disk(s, c[0], r == "radio") for r in ("plain", "radio")} for c in CHUNKS}
        return s

    def disk(self, s, chunk, radio):
        """free space a build of this chunk needs, in GB: in the work folder, in the mods folder, and in all when
        both are on one drive. A level that's already there is wiped before the build writes, so its space counts."""
        work = (0 if s["extracted"] else GAME_GB) + (0 if s["prepared"] else PREPARED_GB) + CACHE_GB
        if chunk == RALLY:      # its own tables and caches, in <work>/dirt
            work += (0 if s["rally_prepared"] else RALLY_PREPARED_GB) + CACHE_GB
        c = CHUNK.get(chunk)
        level = c[4] if c else 0
        mods = level + (RADIO_GB if radio and not s["radio"] else 0)
        freed = level if c and s["levels"].get(chunk) else 0
        same = _drive(self.work()) == _drive(self.mods())
        return {"work": work, "mods": mods, "freed": freed, "same_drive": same,
                "total": work + mods - freed if same else None}

    def problems(self, opts):
        """reasons a conversion can't start now"""
        s = self.state()
        out = []
        if s["runtime"]["needed"] and not s["runtime"]["ready"]:
            out.append("Set up Python for the converter first (under Before you build).")
        if not s["pipeline"]:
            out.append("The FH1 pipeline scripts weren't found next to kn5port.")
        if s["missing_packages"]:
            out.append("Missing Python packages: " + ", ".join(s["missing_packages"]) + ".")
        if not s["ffmpeg"]:
            out.append("ffmpeg wasn't found. Install it from ffmpeg.org and put it on your PATH (kn5port.exe has its own).")
        if not s["source"]["kind"]:
            out.append("Pick your copy of Forza Horizon.")
        if not s["mods"]:
            out.append("Set your BeamNG mods folder under Folders.")
        if not self.beamng():
            out.append("BeamNG.drive wasn't found. Set its install folder under Folders: the folder that holds content\\levels.")
        if opts.get("chunk") not in CHUNK:
            out.append("Pick which part of the map to build.")
        if opts.get("chunk") == RALLY and not s["dlc_source"]["path"]:
            out.append("Pick your copy of the Rally Expansion DLC: its package's Media folder, or Xenia's content folder.")
        if beamng_running():
            out.append("Close BeamNG first: building replaces the level's files, which BeamNG keeps open.")
        d = self.disk(s, opts.get("chunk"), opts.get("radio"))
        if d["same_drive"]:
            if s["free_gb"] is not None and s["free_gb"] < d["total"] + 0.5:
                out.append(f"Not enough free space: the work folder and the level need {d['total']:.1f} GB on the same "
                           f"drive, {s['free_gb']:.1f} GB free.")
        else:
            if s["free_gb"] is not None and s["free_gb"] < d["work"] + 0.5:
                out.append(f"Not enough free space for the work folder: {d['work']:.1f} GB needed, {s['free_gb']:.1f} GB free.")
            if s["mods_free_gb"] is not None and s["mods_free_gb"] < d["mods"] - d["freed"] + 0.3:
                out.append(f"Not enough free space in the mods folder: {d['mods'] - d['freed']:.1f} GB needed, "
                           f"{s['mods_free_gb']:.1f} GB free.")
        return out

    # -- jobs
    def busy(self):
        return self.job is not None and self.job.alive()

    def start(self, opts):
        with self.lock:
            if self.busy():
                raise RuntimeError("a Forza Horizon job is already running")
            why = self.problems(opts)
            if why:
                raise RuntimeError(" ".join(why))
            self.job = Job(self, opts)
            return self.job

    def setup_task(self, what):
        """'python': the packages (pip) or, in the exe, the private Python"""
        with self.lock:
            if self.busy():
                raise RuntimeError("a Forza Horizon job is already running")
            task = "runtime" if runtime.needed() else "pip"
            self.job = Job(self, {"task": task})
            return self.job


class Job:
    def __init__(self, fh1, opts):
        self.fh1 = fh1
        self.opts = opts
        self.id = secrets.token_hex(6)
        self.lines = []
        self.status = "running"
        self.stage = ""
        self.stages = []
        self.progress = None          # 0..1 while extracting
        self.started = time.time()
        self.ended = None
        self.proc = None
        self._cancel = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def alive(self):
        return self.thread.is_alive()

    def say(self, text):
        for line in str(text).splitlines() or [""]:
            self.lines.append(line.rstrip())

    def view(self, since=0):
        return {"id": self.id, "status": self.status, "stage": self.stage, "stages": self.stages,
                "progress": self.progress, "lines": self.lines[since:], "next": len(self.lines),
                "chunk": self.opts.get("chunk"), "task": self.opts.get("task", ""),
                "elapsed": round((self.ended or time.time()) - self.started)}

    def cancel(self):
        if self.status != "running":
            return
        self._cancel = True
        self.status = "cancelled"
        p = self.proc
        if p and p.poll() is None:
            if sys.platform == "win32":    # the build starts worker processes of its own: end the whole tree
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                               creationflags=0x08000000)
            else:
                p.kill()

    # -- running
    def _cmd(self, args, cwd, env=None):
        self.proc = subprocess.Popen(args, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                                     creationflags=0x08000000 if sys.platform == "win32" else 0)
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.strip():
                self.lines.append(line)
        rc = self.proc.wait()
        if self._cancel:
            raise KeyboardInterrupt
        if rc:
            raise RuntimeError(f"{Path(args[2] if len(args) > 2 else args[0]).name} failed (exit code {rc})")

    def _check_ffmpeg(self, py, cwd, env):
        """start ffmpeg the way the build's scripts do (their Python, folder and PATH), so a broken one stops here and not at the end of the build"""
        test = "import subprocess; subprocess.run(['ffmpeg', '-version'], check=True, capture_output=True)"
        r = subprocess.run(py + ["-c", test], cwd=str(cwd), env=env, capture_output=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
        if r.returncode:
            why = ((r.stdout + r.stderr).strip().splitlines() or ["no output"])[-1]
            raise RuntimeError(
                f"ffmpeg at {find_ffmpeg() or '(not found)'} won't start ({why}), and the level's sound needs it. Nothing was built. "
                "To fix it: 1) if your antivirus blocked or removed it, allow kn5port.exe and restore it; 2) restart kn5port; 3) build again.")

    def _run(self):
        try:
            task = self.opts.get("task")
            if task == "pip":
                pkgs = missing_packages() or [p for _, p in PACKAGES]
                self.stages = ["python"]; self.stage = "python"
                self.say(f"Installing {', '.join(pkgs)} into {sys.executable}")
                self._cmd([sys.executable, "-m", "pip", "install", "--disable-pip-version-check"] + pkgs, Path.home())
            elif task == "runtime":
                self.stages = ["python"]; self.stage = "python"

                def prog(x):
                    self.progress = x
                runtime.setup(self.say, prog, lambda: self._cancel)
                self.progress = None
            else:
                self._convert()
            if self.status == "running":
                self.status = "done"
        except KeyboardInterrupt:
            self.status = "cancelled"
            self.say("Cancelled.")
        except Exception as e:                  # noqa: BLE001 (shown to the user)
            if self.status == "running":
                self.status = "failed"
            self.say(f"Error: {e}")
        finally:
            self.ended = time.time()
            self.proc = None

    def _convert(self):
        f = self.fh1
        work = f.work(); work.mkdir(parents=True, exist_ok=True)
        det = xbox.detect(f.settings.get("fh1_game"))
        game = f.game_dir(det)
        prepared = f.prepared(game) and not self.opts.get("force_prepare")
        rally = self.opts.get("chunk") == RALLY
        dlc = f.dlc()["path"] if rally else None
        rally_prepared = rally and prepared and f.rally_prepared(game)      # it merges in Colorado's tables, so it follows them
        self.stages = (["extract"] if not game else []) + (["prepare"] if not prepared else []) + \
            (["rally"] if rally and not rally_prepared else []) + ["build"] + (["radio"] if self.opts.get("radio") else [])

        if not game:
            self.stage = "extract"
            game = work / "game"
            self.say(f"Copying the game files the converter needs out of the {det['kind'].upper()} into {game}")
            g = xbox.open_game(det["path"])
            try:
                last = [0.0]

                def prog(done, total, path):
                    self.progress = done / max(total, 1)
                    if time.time() - last[0] > 2:
                        last[0] = time.time()
                        self.say(f"  {self.progress * 100:4.0f}%  {path}")
                n = g.extract(game, lambda p: bool(WANT.match(p)), prog, lambda: self._cancel)
            finally:
                g.close()
            if not (game / "media/tracks/colorado/bin.zip").exists():
                raise RuntimeError("This copy has no media/tracks/colorado/bin.zip, so it isn't Forza Horizon (2012), "
                                   "or it's incomplete.")
            (game / ".kn5port-source.json").write_text(json.dumps({"key": _source_key(det)}))
            self.progress = None
            self.say(f"Copied {n} files.")

        snap = work / "app_pipeline"
        src = pipeline_src()
        if snap.exists():
            shutil.rmtree(snap)
        shutil.copytree(src, snap, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.o", "*.lib", "*.exp"))
        (work / "dump_path.txt").write_text(str(game).replace("\\", "/") + "\n", encoding="utf8")
        py_exe = interpreter()
        if not py_exe:
            raise RuntimeError("Python for the converter isn't set up yet.")
        if runtime.needed():      # the private Python imports kn5port from a copy of the exe's bundled sources
            pkg_root = str(runtime.app_sources(Path(sys._MEIPASS) / "kn5port_src" / "kn5port"))
        else:
            pkg_root = str(Path(__file__).resolve().parents[2])
        ff = find_ffmpeg()
        path = os.pathsep.join([str(Path(ff).parent)] * bool(ff) + [os.environ.get("PATH", "")])
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONSTARTUP")}
        env.update(PATH=path, FH1_DUMP=str(game), KN5PORT_MODS=str(f.mods()), BEAMNG_PATH=str(f.beamng() or ""), PYTHONIOENCODING="utf8",
                   PYTHONUNBUFFERED="1", NUMBA_CACHE_DIR=str(work / "numbacache"),
                   PYTHONPATH=os.pathsep.join([pkg_root] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
        if runtime.needed():
            env["PYTHONNOUSERSITE"] = "1"      # the private Python sees only its own packages
        py = [str(py_exe), "-u"]
        self._check_ffmpeg(py, snap, env)

        if "prepare" in self.stages:
            self.stage = "prepare"
            self.say("Reading the map's tables (about 3 minutes; only done once per copy of the game)")
            (work / "prepared.json").unlink(missing_ok=True)
            self._cmd(py + ["prepare.py"], snap, env)
            (work / "prepared.json").write_text(json.dumps({"dump": str(game), "time": int(time.time())}))

        build_dir = snap
        if rally:
            rw = f.rally_work()
            if "rally" in self.stages:
                self.stage = "rally"
                self.say(f"Reading the Rally Expansion's tables from {dlc} into {rw} (only done once per copy of the DLC)")
                rw.mkdir(parents=True, exist_ok=True)
                (rw / "prepared.json").unlink(missing_ok=True)
                self._cmd(py + ["prepare_dlc.py", dlc, str(rw)], snap, env)       # from snap, so it merges <work>'s Colorado
                (rw / "prepared.json").write_text(json.dumps({"dump": str(game), "dlc": dlc, "time": int(time.time())}))
            build_dir = rw / "app_pipeline"         # build3.py reads the track's data one folder up, as for Colorado
            if build_dir.exists():
                shutil.rmtree(build_dir)
            shutil.copytree(src, build_dir, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.o", "*.lib", "*.exp"))

        key, lvl, name = CHUNK[self.opts["chunk"]][:3]
        self.stage = "build"
        self.say(f"Building {name} as {lvl} into {f.mods() / 'unpacked' / lvl}")
        if beamng_running():
            raise RuntimeError("BeamNG is running: close it, then build again.")
        self._cmd(py + ["build3.py"], build_dir, dict(env, FH1_CHUNK=key))

        if self.opts.get("radio"):
            self.stage = "radio"
            self.say("Building the FH1 radio mod (fh1_radio)")
            self._cmd(py + ["radio.py"], snap, env)
        self.stage = "done"
        self.say(f"Done. Fully restart BeamNG, then pick \"{CHUNK[key][2]}\" (level {lvl}) from the level list.")


def pick_tk(start="", file=False):
    """a native folder or file dialog through tkinter, for the browser version of the app"""
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk(); root.withdraw(); root.attributes("-topmost", True)
    try:
        if file:
            return filedialog.askopenfilename(initialdir=start or None, parent=root,
                                              filetypes=[("Disc images", "*.iso *.xiso"), ("All files", "*.*")]) or ""
        return filedialog.askdirectory(initialdir=start or None, parent=root, mustexist=True) or ""
    finally:
        root.destroy()

