"""Retake the README screenshots (docs/screenshots/*.png) from the real app and your own install.

Uses the folders saved in %APPDATA%\\kn5port\\settings.json (run the app once first), converts one track into a
temporary mods folder so your real BeamNG mods are untouched, and replaces your local paths with generic ones
before each shot. Needs Playwright (`pip install playwright`); it drives your installed Chrome, or Chromium if
you ran `playwright install chromium`.

    python packaging/screenshots.py [track folder name]     (default: magione)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "screenshots"
REAL = Path(os.environ["APPDATA"]) / "kn5port"
W, H = 1360, 880

# Replaces each [old, new] pair in every text node and input on the page.
SCRUB = """(pairs) => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
  const fix = s => pairs.reduce((s, [a, b]) => s.split(a).join(b), s);
  while ((n = w.nextNode())) n.nodeValue = fix(n.nodeValue);
  document.querySelectorAll('input').forEach(i => { i.value = fix(i.value); }); }"""


def launch(pw):
    for kw in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
        try:
            return pw.chromium.launch(**kw)
        except Exception:
            pass
    sys.exit("no browser: install Chrome or run `playwright install chromium`")


def main():
    from playwright.sync_api import sync_playwright

    track = sys.argv[1] if len(sys.argv) > 1 else "magione"
    settings = json.loads((REAL / "settings.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="kn5port-shots-"))
    mods = tmp / "mods"
    (mods / "unpacked").mkdir(parents=True)
    (tmp / "kn5port").mkdir()
    for name in ("palettes", "scan_cache.json"):          # skip the slow first scan
        src = REAL / name
        if src.is_dir():
            shutil.copytree(src, tmp / "kn5port" / name)
        elif src.exists():
            shutil.copy(src, tmp / "kn5port" / name)
    (tmp / "kn5port" / "settings.json").write_text(json.dumps({**settings, "mods": str(mods)}))

    home = str(Path.home())
    pairs = [[str(mods), r"C:\Users\you\AppData\Local\BeamNG\BeamNG.drive\current\mods"], [home, r"C:\Users\you"]]
    if settings.get("fh1_game"):
        pairs.insert(0, [settings["fh1_game"], "D:/Games/Forza Horizon"])

    env = {**os.environ, "APPDATA": str(tmp), "PYTHONPATH": str(REPO)}
    code = ("import time; from kn5port.ui.server import start_server; app, httpd, url = start_server(0); "
            "print(url, flush=True); time.sleep(10**6)")
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True, cwd=tmp)
    try:
        url = proc.stdout.readline().strip()
        time.sleep(3)
        with sync_playwright() as pw:
            b = launch(pw)
            page = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto(url)
            page.wait_for_timeout(4000)
            shoot(page, track, pairs)
            b.close()
    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT}")


def shoot(page, track, pairs):
    OUT.mkdir(parents=True, exist_ok=True)
    if page.locator("#n_ok").is_visible():
        page.click("#n_ok")                                 # the one-time "before you convert" notice
        page.wait_for_timeout(300)
    page.click(f'.card[data-id="{track}"]')
    page.wait_for_timeout(2500)
    page.evaluate(SCRUB, pairs)
    page.screenshot(path=str(OUT / "track.png"))

    # Options, from the top down to (not including) the Trees & grass group.
    page.set_viewport_size({"width": W, "height": 3000})    # tall enough that the whole section is on screen
    page.wait_for_timeout(400)
    opts = page.locator("section", has_text="Options")
    box = opts.bounding_box()
    trees = page.locator(".og", has=page.locator(".og-t", has_text="Trees & grass")).bounding_box()
    page.screenshot(path=str(OUT / "options.png"),
                    clip={"x": box["x"], "y": box["y"], "width": box["width"], "height": trees["y"] - box["y"] - 8})
    page.set_viewport_size({"width": W, "height": H})
    page.wait_for_timeout(300)

    page.click("#go")
    page.wait_for_selector(".box.result", timeout=1_800_000)
    page.wait_for_timeout(800)
    page.evaluate(SCRUB, pairs)
    page.eval_on_selector(".box.result", "e => e.scrollIntoView({block: 'end'})")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "converted.png"))

    # Forza Horizon 1 tab: steps 1 and 2 (step 3 lists local tool paths).
    page.click('button[data-tab="fh1"]')
    page.wait_for_timeout(2000)
    page.evaluate(SCRUB, pairs)
    page.wait_for_timeout(200)
    fh = page.locator("#fh").bounding_box()
    step3 = page.locator("#fh section", has_text="Before you build").bounding_box()
    top = page.locator("header").first.bounding_box()
    y = max(fh["y"] - 16, top["y"] + top["height"] + 12)
    page.screenshot(path=str(OUT / "fh1.png"), clip={"x": fh["x"] - 16, "y": y,
                                                    "width": fh["width"] + 32, "height": step3["y"] - y + 4})


if __name__ == "__main__":
    main()
