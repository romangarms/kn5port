"""kn5port's local web UI: a small standard-library HTTP server bound to 127.0.0.1.

Every API call must carry the per-session token (header ``X-Token`` or ``?t=``) and a localhost Host header,
so other websites open in your browser can't drive it. Conversions run as ``python -m kn5port convert``
subprocesses whose output is streamed to the page.
"""
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__, beamng

STATIC = Path(__file__).parent / "static"


# ---- settings ---------------------------------------------------------------------------------------------

def settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "kn5port" / "settings.json"


def load_settings():
    s = {}
    try:
        s = json.loads(settings_path().read_text())
    except (OSError, ValueError):
        pass
    if not s.get("tracks"):
        s["tracks"] = str(beamng.find_ac_tracks() or "")
    if not s.get("beamng"):
        s["beamng"] = str(beamng.find_install() or "")
    if not s.get("mods"):
        s["mods"] = str(beamng.find_mods() or "")
    return s


def save_settings(s):
    p = settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2))


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name).lower()


# ---- convertibility scan ----------------------------------------------------------------------------------

CHECK_VERSION = 2            # bump when check_kn5 changes, so cached results are redone


def check_kn5(path):
    """'ok' if the file opens, its node tree walks, and its payload is plain data (KN5.verify); otherwise
    'protected' (covers encrypted, scrambled or damaged files)."""
    from ..kn5 import KN5
    try:
        with KN5(str(path)) as k:
            why = k.verify()
            return f"protected: {why}" if why else "ok"
    except Exception as e:     # KN5Error or anything a malformed file triggers
        text = str(e).replace(f"{path}: ", "").replace(str(path), Path(path).name)
        msg = text.rsplit(" (", 1)[-1].rstrip(")") if text.endswith(")") else (text or type(e).__name__)
        return f"protected: unreadable ({msg})"


class Scanner:
    """Checks every track's kn5 files in the background; results cached by file size + mtime."""

    def __init__(self):
        self.cache_file = settings_path().parent / "scan_cache.json"
        try:
            self.cache = json.loads(self.cache_file.read_text())
        except (OSError, ValueError):
            self.cache = {}
        self.status = {}          # track id -> {"state": ..., "ok": n, "bad": n}
        self.running = False
        self.lock = threading.Lock()

    def start(self, root):
        with self.lock:
            if self.running:
                return
            self.running = True
        threading.Thread(target=self._run, args=(Path(root),), daemon=True).start()

    def _run(self, root):
        try:
            dirs = [d for d in sorted(root.iterdir(), key=lambda p: p.name.lower())
                    if d.is_dir()] if root.is_dir() else []
            for d in dirs:
                files = sorted(d.glob("*.kn5"))
                if not files:
                    continue
                ok = bad = 0
                reasons = []
                for f in files:
                    st = f.stat()
                    key = f"v{CHECK_VERSION}|{f}|{st.st_size}|{int(st.st_mtime)}"
                    r = self.cache.get(key)
                    if r is None:
                        r = self.cache[key] = check_kn5(f)
                    ok += r == "ok"; bad += r != "ok"
                    if r != "ok":
                        reasons.append(f"{f.name}: {r.split(': ', 1)[-1]}")
                state = "ok" if not bad else ("protected" if not ok else "partial")
                self.status[d.name] = {"state": state, "ok": ok, "bad": bad, "reasons": reasons[:5]}
            try:
                self.cache_file.parent.mkdir(parents=True, exist_ok=True)
                self.cache_file.write_text(json.dumps(self.cache))
            except OSError:
                pass
        finally:
            self.running = False


# ---- tracks -----------------------------------------------------------------------------------------------

def read_ui(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return {}


def scan_tracks(settings, scanner=None):
    root = Path(settings.get("tracks") or "")
    mods = Path(settings.get("mods") or "")
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or not (any(d.glob("*.kn5")) or any(d.glob("models*.ini"))):
            continue
        uis = sorted((d / "ui").glob("*/ui_track.json")) if (d / "ui").exists() else []
        main_ui = d / "ui" / "ui_track.json"
        ui = read_ui(main_ui) if main_ui.exists() else (read_ui(uis[0]) if uis else {})
        prev = next((p for p in [d / "ui" / "preview.png"] + [u.parent / "preview.png" for u in uis] if p.exists()),
                    None)
        name = ui.get("name") or d.name
        if uis and " - " in name:
            name = name.split(" - ")[0]
        level = safe_name(d.name)
        converted = mods.is_dir() and (mods / "unpacked" / level / "levels" / level / "info.json").exists()
        out.append({
            "id": d.name, "name": name, "author": ui.get("author", ""), "country": ui.get("country", ""),
            "city": ui.get("city", ""), "length": ui.get("length", ""), "description": ui.get("description", ""),
            "tags": ui.get("tags", [])[:6] if isinstance(ui.get("tags"), list) else [],
            "layouts": [u.parent.name for u in uis] or ["(single)"],
            "preview": bool(prev), "converted": bool(converted), "level": level,
            "check": (scanner.status.get(d.name) if scanner else None) or {"state": "checking"},
        })
    return out


def track_dir(settings, tid):
    root = Path(settings.get("tracks") or "")
    d = (root / tid).resolve()
    if root.resolve() not in d.parents or not d.is_dir():
        raise KeyError(tid)
    return d


# ---- jobs -------------------------------------------------------------------------------------------------

class Job:
    def __init__(self, track, args):
        self.id = secrets.token_hex(6)
        self.track = track
        self.lines = []
        self.step = 0
        self.status = "running"
        self.started = time.time()
        self.ended = None
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        cmd = [sys.executable, "convert"] if getattr(sys, "frozen", False) else \
            [sys.executable, "-m", "kn5port", "convert"]
        self.proc = subprocess.Popen(cmd + args,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", bufsize=1, env=env, creationflags=flags)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            if "RuntimeWarning" in line or line.strip().startswith("l3 = "):
                continue
            m = re.match(r"== (\d)/4", line)
            if m:
                self.step = int(m.group(1))
            self.lines.append(line)
        rc = self.proc.wait()
        self.ended = time.time()
        if self.status != "cancelled":
            problems = any("problem(s):" in l for l in self.lines)
            self.status = "failed" if rc else ("warnings" if problems else "done")
        if not rc:
            self.step = 5

    def cancel(self):
        if self.status == "running":
            self.status = "cancelled"
            self.proc.terminate()

    def view(self, since=0):
        return {"id": self.id, "track": self.track, "status": self.status, "step": self.step,
                "lines": self.lines[since:], "next": len(self.lines),
                "elapsed": round((self.ended or time.time()) - self.started)}


def pick_folder_tk(start=""):
    """Native folder picker for browser mode (Tk in a child process, so it never touches the server threads)."""
    if getattr(sys, "frozen", False):
        return ""
    code = ("import sys, tkinter as tk; from tkinter import filedialog; r = tk.Tk(); r.withdraw(); "
            "r.attributes('-topmost', True); "
            "print(filedialog.askdirectory(initialdir=sys.argv[1] or None, title='Choose a folder') or '')")
    try:
        out = subprocess.run([sys.executable, "-c", code, start], capture_output=True, text=True, timeout=600)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ---- HTTP -------------------------------------------------------------------------------------------------

class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(18)
        self.settings = load_settings()
        self.jobs = {}
        self.inspect_cache = {}
        self.lock = threading.Lock()
        self.scanner = Scanner()
        if self.settings.get("tracks"):
            self.scanner.start(self.settings["tracks"])
        self.pick_folder = None       # set by the desktop app (native dialog)


def make_handler(app, port_ref):
    class H(BaseHTTPRequestHandler):
        server_version = f"kn5port/{__version__}"

        def log_message(self, *a):      # quiet
            pass

        # -- helpers
        def _send(self, code, body, ctype="application/json", cache=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self, q):
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                return False
            tok = self.headers.get("X-Token") or (q.get("t") or [""])[0]
            return secrets.compare_digest(tok, app.token)

        def _json_body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        # -- routes
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if not self._allowed(q):
                return self._send(403, {"error": "forbidden"})
            try:
                if u.path == "/api/state":
                    return self._send(200, {"version": __version__, "settings": app.settings,
                                            "tracks": scan_tracks(app.settings, app.scanner),
                                            "scanning": app.scanner.running, "native": app.pick_folder is not None,
                                            "jobs": [j.view(len(j.lines)) for j in app.jobs.values()]})
                m = re.match(r"^/api/track/([^/]+)/preview$", u.path)
                if m:
                    d = track_dir(app.settings, m.group(1))
                    lay = (q.get("layout") or [""])[0]
                    cands = [d / "ui" / lay / "preview.png"] if lay else []
                    cands += [d / "ui" / "preview.png"] + sorted((d / "ui").glob("*/preview.png"))
                    p = next((c for c in cands if c.exists()), None)
                    if not p:
                        return self._send(404, {"error": "no preview"})
                    return self._send(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/png", cache=True)
                m = re.match(r"^/api/track/([^/]+)/inspect$", u.path)
                if m:
                    d = track_dir(app.settings, m.group(1))
                    key = (str(d), max((f.stat().st_mtime for f in d.glob("*.kn5")), default=0))
                    if key not in app.inspect_cache:
                        from ..cli import inspect_track
                        app.inspect_cache[key] = inspect_track(d, None, app.settings.get("beamng"),
                                                               app.settings.get("mods"))
                    return self._send(200, app.inspect_cache[key])
                m = re.match(r"^/api/jobs/([0-9a-f]+)$", u.path)
                if m:
                    j = app.jobs.get(m.group(1))
                    if not j:
                        return self._send(404, {"error": "no such job"})
                    return self._send(200, j.view(int((q.get("since") or ["0"])[0])))
            except KeyError:
                return self._send(404, {"error": "unknown track"})
            except Exception as e:      # surface errors to the page instead of dropping the connection
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if not self._allowed(q):
                return self._send(403, {"error": "forbidden"})
            body = self._json_body()
            try:
                if u.path == "/api/settings":
                    for k in ("tracks", "beamng", "mods"):
                        if k in body:
                            app.settings[k] = str(body[k]).strip()
                    save_settings(app.settings)
                    if app.settings.get("tracks"):
                        app.scanner.start(app.settings["tracks"])
                    return self._send(200, {"settings": app.settings,
                                            "tracks": scan_tracks(app.settings, app.scanner)})
                if u.path == "/api/detect":
                    # re-run auto-detection for any folder (empty values are filled in)
                    det = {"tracks": str(beamng.find_ac_tracks() or ""), "beamng": str(beamng.find_install() or ""),
                           "mods": str(beamng.find_mods() or "")}
                    return self._send(200, det)
                if u.path == "/api/pick-folder":
                    start = str(body.get("start") or "")
                    path = app.pick_folder(start) if app.pick_folder else pick_folder_tk(start)
                    return self._send(200, {"path": path or ""})
                if u.path == "/api/convert":
                    d = track_dir(app.settings, body.get("track", ""))
                    with app.lock:
                        if any(j.status == "running" for j in app.jobs.values()):
                            return self._send(409, {"error": "a conversion is already running"})
                        args = [str(d)]
                        if app.settings.get("mods"):
                            args += ["--mods", app.settings["mods"]]
                        if app.settings.get("beamng"):
                            args += ["--beamng", app.settings["beamng"]]
                        o = body.get("options") or {}
                        if o.get("terrain") in ("auto", "off"):
                            args += ["--terrain", o["terrain"]]
                        for key, flag in (("tree_density", "--tree-density"), ("ao", "--ao"),
                                          ("near_gain", "--near-gain")):
                            if o.get(key) not in (None, ""):
                                args += [flag, str(float(o[key]))]
                        if o.get("forest") is False:
                            args += ["--no-forest"]
                        j = Job(d.name, args)
                        app.jobs[j.id] = j
                    return self._send(200, j.view())
                m = re.match(r"^/api/jobs/([0-9a-f]+)/cancel$", u.path)
                if m and m.group(1) in app.jobs:
                    app.jobs[m.group(1)].cancel()
                    return self._send(200, app.jobs[m.group(1)].view())
                if u.path == "/api/open":
                    # only ever opens the converted level or the track folder
                    tid = body.get("track", "")
                    d = track_dir(app.settings, tid)
                    target = d
                    if body.get("what") == "level":
                        lvl = safe_name(tid)
                        target = Path(app.settings.get("mods", "")) / "unpacked" / lvl / "levels" / lvl
                    if not target.exists():
                        return self._send(404, {"error": "not found"})
                    if sys.platform == "win32":
                        os.startfile(str(target))          # noqa: S606 (local, fixed paths only)
                    else:
                        subprocess.Popen(["xdg-open", str(target)])
                    return self._send(200, {"ok": True})
            except KeyError:
                return self._send(404, {"error": "unknown track"})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            return self._send(404, {"error": "not found"})

    return H


def start_server(port=0):
    """Start the server in a background thread; returns (app, httpd, url)."""
    app = App()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, {}))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={app.token}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return app, httpd, url


def serve(port=0, open_browser=True):
    app = App()
    port_ref = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port_ref))
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={app.token}"
    print(f"kn5port UI running at {url}\n(keep this window open; Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for j in app.jobs.values():
            j.cancel()
        httpd.server_close()
