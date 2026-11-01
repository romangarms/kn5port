"""Regenerate the README screenshots (docs/screenshots/*.png) from made-up demo data.

Everything shown is synthetic: the tracks, their previews and the BeamNG "level" used as a palette are generated
here, so the screenshots contain no game content. Needs Playwright with Chromium
(set CHROMIUM_PATH to use a Chromium that Playwright didn't install).

    python packaging/screenshots.py            -> docs/screenshots/*.png
"""
import io
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "screenshots"


# ---- a tiny kn5 writer ------------------------------------------------------------------------------------

def _s(t):
    b = t.encode()
    return struct.pack("<i", len(b)) + b


def _png(color, size=16):
    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color).save(buf, "PNG")
    return buf.getvalue()


def write_kn5(path, materials, meshes):
    """materials: [(name, shader, texture name, rgba, alpha tested)]; meshes: [(name, material index, verts, idx)]
    with verts an (n, 3) array of positions (AC is Y-up)."""
    out = b"sc6969" + struct.pack("<ii", 6, 0)
    out += struct.pack("<i", len(materials))
    for name, shader, tex, rgba, at in materials:
        out += struct.pack("<i", 1) + _s(tex) + struct.pack("<i", len(_png(rgba))) + _png(rgba)
    out += struct.pack("<i", len(materials))
    for name, shader, tex, rgba, at in materials:
        out += _s(name) + _s(shader) + bytes([0, 1 if at else 0]) + struct.pack("<i", 0)
        out += struct.pack("<i", 1) + _s("ksDiffuse") + struct.pack("<10f", 0.4, *[0] * 9)
        out += struct.pack("<i", 1) + _s("txDiffuse") + struct.pack("<i", 0) + _s(tex)
    out += struct.pack("<i", 1) + _s("root") + struct.pack("<i", len(meshes)) + bytes([1])
    out += struct.pack("<16f", *np.identity(4).ravel())
    for name, mat, verts, idx in meshes:
        verts = np.asarray(verts, np.float32)
        out += struct.pack("<i", 2) + _s(name) + struct.pack("<i", 0) + bytes([1])
        out += bytes([1, 1, 0]) + struct.pack("<i", len(verts))
        v = np.zeros((len(verts), 11), np.float32)
        v[:, 0:3] = verts
        v[:, 4] = 1                                     # normal +Y
        v[:, 6] = verts[:, 0] / 10
        v[:, 7] = verts[:, 2] / 10
        v[:, 8] = 1                                     # tangent +X
        out += v.tobytes()
        out += struct.pack("<i", len(idx)) + np.asarray(idx, "<u2").tobytes()
        c = verts.mean(0)
        out += struct.pack("<ii", mat, 0) + struct.pack("<2f", 0, 0)
        out += struct.pack("<4f", *c, float(np.linalg.norm(verts - c, axis=1).max())) + bytes([1])
    Path(path).write_bytes(out)


def circuit(seed, n=180):
    """A closed, wobbly loop: list of (x, z) centre-line points."""
    rng = np.random.default_rng(seed)
    harm = [(k, rng.uniform(0.04, 0.16) / k ** 0.6, rng.uniform(0, 6.28)) for k in range(2, 6)]
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        r = 1 + sum(a * math.sin(k * t + p) for k, a, p in harm)
        pts.append((math.cos(t) * r * 420 * (1.25 if seed % 2 else 1), math.sin(t) * r * 300))
    return np.array(pts)


def ribbon(line, half, y=0.0, offset=0.0):
    nxt = np.roll(line, -1, 0)
    d = nxt - line
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    nrm = np.stack([-d[:, 1], d[:, 0]], 1)
    a = line + nrm * (offset - half)
    b = line + nrm * (offset + half)
    verts = np.zeros((2 * len(line), 3))
    verts[0::2, [0, 2]] = a
    verts[1::2, [0, 2]] = b
    verts[:, 1] = y
    idx = []
    n = len(line)
    for i in range(n):
        j = (i + 1) % n
        idx += [2 * i, 2 * j, 2 * i + 1, 2 * i + 1, 2 * j, 2 * j + 1]
    return verts, idx


def quad(x0, z0, x1, z1, y=0.0):
    return np.array([(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]), [0, 2, 1, 0, 3, 2]


def trees(line, seed, count=60):
    rng = np.random.default_rng(seed)
    verts, idx = [], []
    for i in range(count):
        p = line[rng.integers(len(line))] * rng.uniform(1.12, 1.35)
        h = rng.uniform(9, 22)
        w = h * 0.35
        b = len(verts)
        for ang in (0, math.pi / 2):               # two crossed cards
            dx, dz = math.cos(ang) * w, math.sin(ang) * w
            k = len(verts)
            verts += [(p[0] - dx, 0, p[1] - dz), (p[0] + dx, 0, p[1] + dz), (p[0] + dx, h, p[1] + dz),
                      (p[0] - dx, h, p[1] - dz)]
            idx += [k, k + 1, k + 2, k, k + 2, k + 3]
    return np.array(verts), idx


def make_track_kn5(path, seed):
    line = circuit(seed)
    mats = [("asphalt", "ksPerPixel", "asphalt.png", (70, 72, 76, 255), False),
            ("kerb", "ksPerPixel", "kerb.png", (200, 60, 50, 255), False),
            ("grass", "ksMultilayer", "grass.png", (80, 120, 60, 255), False),
            ("tree_pine", "ksTree", "pine.png", (40, 80, 40, 255), True),
            ("bld_pitbox", "ksPerPixel", "pit.png", (180, 180, 185, 255), False)]
    road = ribbon(line, 6)
    kerb_l = ribbon(line, 0.8, 0.02, -6.6)
    kerb_r = ribbon(line, 0.8, 0.02, 6.6)
    lo, hi = line.min(0) - 160, line.max(0) + 160
    grass = quad(lo[0], lo[1], hi[0], hi[1], -0.05)
    tv, ti = trees(line, seed)
    pit = quad(line[0, 0] - 40, line[0, 1] + 14, line[0, 0] + 40, line[0, 1] + 26, 0.0)
    pit = (pit[0] + [0, 4, 0], pit[1])
    meshes = [("1ROAD_main", 0, *road), ("1KERB_left", 1, *kerb_l), ("1KERB_right", 1, *kerb_r),
              ("1GRASS_infield", 2, *grass), ("TREE_pines", 3, tv, ti), ("pitbuilding", 4, *pit)]
    write_kn5(path, mats, meshes)


# ---- demo AC tracks --------------------------------------------------------------------------------------

TRACKS = [
    # folder, name, layouts, country, city, length, tags, palette (sky, ground), kind
    ("pinewood_ridge", "Pinewood Ridge Raceway", ["gp", "club"], "Canada", "Pinewood", "4.1 km",
     ["circuit", "original", "forest"], ((126, 168, 205), (66, 104, 62)), "ok"),
    ("harbor_street", "Harbor Street Circuit", [], "Portugal", "Porto Velho", "3.3 km",
     ["street", "original"], ((236, 170, 120), (92, 96, 104)), "ok"),
    ("kestrel_valley", "Kestrel Valley", ["full"], "USA", "Kestrel", "5.8 km",
     ["circuit", "hills"], ((150, 190, 222), (120, 136, 74)), "partial"),
    ("dune_bay", "Dune Bay Speedway", [], "Australia", "Dune Bay", "2.4 km",
     ["oval", "coast"], ((120, 190, 230), (214, 188, 132)), "ok"),
    ("lakeside_kart", "Lakeside Kart Park", [], "Sweden", "Sjöby", "1.1 km",
     ["karting", "lake"], ((170, 200, 225), (70, 116, 80)), "ok"),
    ("old_mill_hillclimb", "Old Mill Hillclimb", [], "Germany", "Mühlbach", "6.2 km",
     ["hillclimb", "point-to-point"], ((190, 200, 214), (88, 98, 70)), "protected"),
]


def preview(seed, sky, ground, size=(710, 400)):
    """A stylised aerial-ish illustration: sky gradient, rolling ground, the circuit as a ribbon."""
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    hor = int(h * 0.36)
    for y in range(hor):
        t = y / hor
        d.line([(0, y), (w, y)], fill=tuple(int(c * (0.78 + 0.22 * t)) for c in sky))
    rng = np.random.default_rng(seed)
    for k, shade in ((3, 0.72), (2, 0.84), (1, 1.0)):
        ys = hor + (3 - k) * 18
        pts = [(x, ys - 20 * k * (0.5 + 0.5 * math.sin(x / (90 + 40 * k) + rng.uniform(0, 6)))) for x in range(0, w + 10, 10)]
        d.polygon(pts + [(w, h), (0, h)], fill=tuple(int(c * shade) for c in ground))
    line = circuit(seed)
    line = (line - line.min(0)) / (line.max(0) - line.min(0))
    pts = [(w * 0.12 + x * w * 0.76, hor + 40 + z * (h - hor - 70)) for x, z in line]
    d.line(pts + pts[:1], fill=(232, 232, 236), width=22, joint="curve")
    d.line(pts + pts[:1], fill=(58, 60, 66), width=16, joint="curve")
    for x, y in pts[::14]:
        d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(210, 70, 60))
    for _ in range(26):
        x, y = rng.uniform(0, w), rng.uniform(hor + 30, h - 10)
        if any(abs(x - px) < 40 and abs(y - py) < 30 for px, py in pts[::3]):
            continue                                    # keep trees off the track
        r = rng.uniform(5, 9)
        d.ellipse((x - r, y - r * 1.6, x + r, y + r * 0.4), fill=tuple(int(c * 0.6) for c in ground))
    return img.filter(ImageFilter.SMOOTH)


def make_tracks(root):
    for i, (folder, name, layouts, country, city, length, tags, (sky, ground), kind) in enumerate(TRACKS):
        d = root / folder
        (d / "ui").mkdir(parents=True)
        (d / "data").mkdir()
        if kind == "protected":
            (d / f"{folder}.kn5").write_bytes(b"sc6969" + struct.pack("<ii", 6, 0) + os.urandom(4096))
        else:
            make_track_kn5(d / f"{folder}.kn5", i + 3)
        if kind == "partial":
            (d / f"{folder}_extra.kn5").write_bytes(b"sc6969" + struct.pack("<ii", 6, 0) + os.urandom(4096))
        desc = (f"A fictional {tags[0]} near {city}, generated for kn5port's screenshots. Fast sweepers, a tight "
                f"hairpin and a long back straight.")
        info = {"name": name, "description": desc, "tags": tags, "country": country, "city": city,
                "length": length, "author": "kn5port demo"}
        img = preview(i + 3, sky, ground)
        if layouts:
            for lay in layouts:
                (d / "ui" / lay).mkdir()
                (d / "ui" / lay / "ui_track.json").write_text(json.dumps({**info, "name": f"{name} - {lay.upper()}"}))
                img.save(d / "ui" / lay / "preview.png")
        else:
            (d / "ui" / "ui_track.json").write_text(json.dumps(info))
            img.save(d / "ui" / "preview.png")


# ---- a demo BeamNG install with one made-up level to borrow vegetation from -------------------------------

def dae(height):
    pts = " ".join(f"{x} {y} {z}" for x, y, z in ((0, 0, 0), (1, 0, 0), (0, 0, height)))
    return (f'<?xml version="1.0"?><COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">'
            f'<asset><unit meter="1"/><up_axis>Z_UP</up_axis></asset><library_geometries><geometry><mesh><source>'
            f'<float_array count="9">{pts}</float_array></source></mesh></geometry></library_geometries></COLLADA>')


def make_beamng(root, level="demo_highlands"):
    lv = root / "content" / "levels"
    lv.mkdir(parents=True)
    items = {"pine_tall": 24, "pine_small": 11, "oak_large": 16, "birch_a": 13, "bush_round": 2.2,
             "shrub_low": 1.1, "grass_clump": 0.6, "fern_a": 0.8}
    pal = {"demo_highlands": ((150, 186, 214), (86, 118, 70))}
    with zipfile.ZipFile(lv / f"{level}.zip", "w") as z:
        managed = {}
        for name, h in items.items():
            sf = f"levels/{level}/art/shapes/trees/{name}.dae"
            z.writestr(sf, dae(h))
            managed[name] = {"class": "TSForestItemData", "name": name, "shapeFile": sf}
        z.writestr(f"levels/{level}/art/forest/managedItemData.json", json.dumps(managed))
        z.writestr(f"levels/{level}/forest/trees.forest4.json",
                   "\n".join(json.dumps({"type": n}) for n in items for _ in range(5)))
        mats = {n: {"class": "TerrainMaterial", "internalName": n}
                for n in ("grass", "dirt_grass", "forest_floor", "dirt", "gravel", "rock_cliff", "beachsand")}
        z.writestr(f"levels/{level}/art/terrains/main.materials.json", json.dumps(mats))
        buf = io.BytesIO()
        preview(11, *pal[level]).save(buf, "JPEG", quality=88)
        z.writestr(f"levels/{level}/{level}_preview.jpg", buf.getvalue())


# ---- capture -----------------------------------------------------------------------------------------------

def main():
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="kn5port-shots-"))
    tracks, beamng, mods, appdata = tmp / "tracks", tmp / "BeamNG.drive", tmp / "mods", tmp / "appdata"
    make_tracks(tracks)
    make_beamng(beamng)
    lvl = mods / "unpacked" / "pinewood_ridge" / "levels" / "pinewood_ridge"
    lvl.mkdir(parents=True)
    (lvl / "info.json").write_text("{}")
    (appdata / "kn5port").mkdir(parents=True)
    (appdata / "kn5port" / "settings.json").write_text(json.dumps(
        {"tracks": str(tracks), "beamng": str(beamng), "mods": str(mods)}))

    env = {**os.environ, "APPDATA": str(appdata), "LOCALAPPDATA": str(tmp / "local"), "PYTHONPATH": str(REPO)}
    code = ("import time; from kn5port.ui.server import start_server; app, httpd, url = start_server(0); "
            "print(url, flush=True); time.sleep(10**6)")
    proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True, cwd=tmp)
    try:
        url = proc.stdout.readline().strip()
        time.sleep(2)                                   # let the background scan and palette analysis finish
        with sync_playwright() as pw:
            exe = os.environ.get("CHROMIUM_PATH")         # e.g. a preinstalled Chromium
            b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
            page = b.new_page(viewport={"width": 1360, "height": 880}, device_scale_factor=1)
            page.goto(url)
            page.wait_for_timeout(2500)
            shoot(page)
            b.close()
    finally:
        proc.terminate()
        proc.wait()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT}")


def shoot(page):
    page.click("#n_ok")                                 # the one-time "before you convert" notice
    page.wait_for_timeout(300)
    page.click('.card[data-id="pinewood_ridge"]')
    page.wait_for_timeout(1500)
    page.screenshot(path=str(OUT / "track.png"))
    page.click(".pal")                                  # the demo palette (the default is West Coast USA)
    page.click("#go")
    page.wait_for_selector(".box.result", timeout=300_000)
    page.wait_for_timeout(800)
    page.eval_on_selector(".box.result", "e => e.scrollIntoView({block: 'end'})")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / "converted.png"))

if __name__ == "__main__":
    main()
