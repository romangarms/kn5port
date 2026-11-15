"""A private Python for the FH1 pipeline, for the packaged kn5port.exe (which has no Python of its own to run scripts).

setup() downloads the official embeddable Python from python.org and the wheels the pipeline needs from PyPI, each
pinned to a version and checked against its SHA-256, and unpacks them into %LOCALAPPDATA%/kn5port/runtime/py.
No pip, no build step: a wheel is a zip. A ._pth file keeps that Python from reading any other Python install's
paths, and sitecustomize.py restores the two things the pipeline expects from a normal interpreter: the script's
folder on sys.path, and PYTHONPATH (which ._pth mode ignores).

Running from source (``pip install .``) doesn't use any of this: the pipeline runs on sys.executable.
"""
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

PY = ("https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
      "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3", 11133606)
_W = "https://files.pythonhosted.org/packages/"
WHEELS = [
    (_W + "12/33/fefe62073dc8acfd0f2b9ed7c003af2f50aa61555e113e6db02b8f79f145/numpy-2.5.4-cp312-cp312-win_amd64.whl",
     "a7b1b6353e36a7e50de2973a38d705c88ee93adcf120673cee7f45a4a3fa223a", 12573091),
    (_W + "39/e7/979fd14e75008623df31ba70d6bb144700f68feadcea042021c06a05bf82/scipy-1.18.1-cp312-cp312-win_amd64.whl",
     "5e4d44984abc0020154ea81b247adeddcc3ac5527b975ff798bd1ba0adc513c2", 36658278),
    (_W + "7e/2b/1b1f8b118cec28513665d8a53ff4f037d6c05720bd9e6f32f947c93c367f/numba-0.68.0-cp312-cp312-win_amd64.whl",
     "530961dc7e41ee358eca2b828baf7b645ce6fa466d778bb9dc73855dd103c4f7", 2830891),
    (_W + "64/70/f395702c20b514363061055b5bdebe3513e544139e6d412a5c86e8ea0b30/llvmlite-0.50.0-cp312-cp312-win_amd64.whl",
     "c20595cc3a76e3c85140fdafbf9246c732ddf8e0e646ba2f4e4881f87567300d", 41865553),
    (_W + "45/89/da2f7971a317f83d807fdd4065c0af40208e59e692cc43d315a71a0e96d1/pillow-12.3.0-cp312-cp312-win_amd64.whl",
     "a2b55dd6b2a4c4b7d87ffa56bdb33fdc5fdb9a462173861a7bc097f17d91cb09", 7227137),
]
RUNTIME_ID = hashlib.sha1(json.dumps([PY] + WHEELS).encode()).hexdigest()[:12]
DOWNLOAD_MB = round((PY[2] + sum(w[2] for w in WHEELS)) / 2 ** 20)

PTH = "python312.zip\n.\nLib\\site-packages\nimport site\n"
SITECUSTOMIZE = '''"""kn5port: the script's folder and PYTHONPATH on sys.path, as a normal interpreter has them (._pth mode drops both)"""
import os, sys
for _p in reversed([p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]):
    sys.path.insert(0, _p)
if sys.argv and sys.argv[0] not in ("", "-c", "-m") and os.path.exists(sys.argv[0]):
    sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))
'''


def root():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "kn5port" / "runtime"


def python():
    """the private Python, if it's fully set up for this version of kn5port, else None"""
    py = root() / "py" / "python.exe"
    try:
        ok = json.loads((root() / "py" / "kn5port-runtime.json").read_text()).get("id") == RUNTIME_ID
    except (OSError, ValueError):
        ok = False
    return py if ok and py.exists() else None


def needed():
    """True when kn5port runs as the packaged exe (sys.executable is kn5port.exe, not a Python)"""
    return bool(getattr(sys, "frozen", False))


def _download(url, sha, size, dest, say, progress, cancelled, done_before, total):
    if dest.exists() and dest.stat().st_size == size and hashlib.sha256(dest.read_bytes()).hexdigest() == sha:
        return
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256(); got = 0
    req = urllib.request.Request(url, headers={"User-Agent": "kn5port"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            if cancelled():
                raise KeyboardInterrupt
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk); h.update(chunk); got += len(chunk)
            progress((done_before + got) / total)
    if h.hexdigest() != sha:
        tmp.unlink()
        raise RuntimeError(f"{dest.name}: the download doesn't match its expected checksum, so it wasn't used. Try again.")
    os.replace(tmp, dest)


def _unpack_wheel(whl, site):
    """a wheel is a zip: files go into site-packages, '<name>.data/purelib|platlib/' too; scripts and headers are skipped"""
    with zipfile.ZipFile(whl) as z:
        for info in z.infolist():
            name = info.filename
            parts = name.split("/")
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue
                name = "/".join(parts[2:])
            if name.endswith("/"):
                continue
            out = site / name
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)


def setup(say, progress=lambda f: None, cancelled=lambda: False):
    """download, check and unpack the private Python into root()/py"""
    r = root(); dl = r / "downloads"; dl.mkdir(parents=True, exist_ok=True)
    items = [PY] + WHEELS
    total = sum(i[2] for i in items); done = 0
    say(f"Downloading Python 3.12 from python.org and numpy, scipy, numba, llvmlite and Pillow from PyPI "
        f"({DOWNLOAD_MB} MB), each checked against a fixed checksum")
    for url, sha, size in items:
        name = url.rsplit("/", 1)[1]
        say(f"  {name} ({size / 2 ** 20:.0f} MB)")
        _download(url, sha, size, dl / name, say, progress, cancelled, done, total)
        done += size
    say("Unpacking")
    tmp = r / "py.new"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    with zipfile.ZipFile(dl / PY[0].rsplit("/", 1)[1]) as z:
        z.extractall(tmp)
    (tmp / "python312._pth").write_text(PTH)
    site = tmp / "Lib" / "site-packages"; site.mkdir(parents=True)
    (site / "sitecustomize.py").write_text(SITECUSTOMIZE)
    for url, _, _ in WHEELS:
        if cancelled():
            raise KeyboardInterrupt
        _unpack_wheel(dl / url.rsplit("/", 1)[1], site)
    (tmp / "kn5port-runtime.json").write_text(json.dumps({"id": RUNTIME_ID, "time": int(time.time())}))
    old = r / "py"
    if old.exists():
        shutil.rmtree(old)
    os.replace(tmp, old)
    shutil.rmtree(dl, ignore_errors=True)          # 112 MB of downloads no longer needed
    say(f"Python is ready in {old}")


def app_sources(src_pkg):
    """kn5port's own sources where the private Python can import them (the pipeline uses kn5port.beamng and others):
    copied from the exe's bundled copy into root()/app/kn5port -> the folder to put on PYTHONPATH"""
    dest = root() / "app"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_pkg, dest / "kn5port", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "static"))
    return dest
