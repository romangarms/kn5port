"""Sanity-check a level produced by kn5port convert and render a top-down preview.

    python validate_level.py "<mod folder>\\levels\\<level>" [--ac-track "<AC track folder>"]

Checks: every TSStatic's .dae exists and parses, array counts and indices are consistent, every
material used has an entry in the materials file, every texture referenced exists.
Writes preview_topdown.png into the level folder (red = collision shapes, grey = visual only,
blue dots = spawns). With --ac-track, the AC map.png outline is drawn on top in green so you can
confirm the geometry lines up.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


def read_items(level):
    objs = []
    for f in (level / "main").rglob("items.level.json"):
        for line in f.read_text().splitlines():
            if line.strip():
                objs.append(json.loads(line))
    return objs


def drive_check(level, track, samples=3000):
    """For every AC layout's AI racing line: is there a collision surface (mesh or terrain) under it, and does
    the terrain stay below the road? Returns a list of (layout, n, covered_frac, terrain_above_frac)."""
    import struct
    from .terrain import load_col_tris
    port = json.loads((level / "kn5_port.json").read_text())
    off = np.array(port["offset_bng"])
    tris, _flat = load_col_tris(level)
    cell = 8.0
    mn = np.floor(tris[:, :, :2].min(1) / cell).astype(np.int64)
    mx = np.floor(tris[:, :, :2].max(1) / cell).astype(np.int64)
    buckets = {}
    for i in range(len(tris)):
        for x in range(mn[i, 0], mx[i, 0] + 1):
            for y in range(mn[i, 1], mx[i, 1] + 1):
                buckets.setdefault((x, y), []).append(i)
    t = port.get("terrain") or {}
    Ht = None
    ter = level / "theTerrain.ter"
    if ter.exists() and "maxHeight" in t:
        b = ter.read_bytes(); n = int.from_bytes(b[1:5], "little")
        Ht = np.frombuffer(b, "<u2", n * n, 5).reshape(n, n).astype(np.float64) / 65535 * t["maxHeight"] + t["zmin"]
        x0, y0, sq = t["rect"][0], t["rect"][1], t["square"]

    def surface(x, y, z):
        best = None
        for i in buckets.get((int(np.floor(x / cell)), int(np.floor(y / cell))), ()):
            A, B, C = tris[i]
            v0, v1 = B[:2] - A[:2], C[:2] - A[:2]
            d = v0[0] * v1[1] - v0[1] * v1[0]
            if abs(d) < 1e-9:
                continue
            qx, qy = x - A[0], y - A[1]
            u = (qx * v1[1] - qy * v1[0]) / d; v = (v0[0] * qy - v0[1] * qx) / d
            if u >= -1e-6 and v >= -1e-6 and u + v <= 1 + 1e-6:
                zz = A[2] + u * (B[2] - A[2]) + v * (C[2] - A[2])
                if best is None or abs(zz - z) < abs(best - z):
                    best = zz
        return best

    def terrain_max(x, y):
        gx, gy = (x - x0) / sq, (y - y0) / sq
        if not (0 <= gx < Ht.shape[0] - 1 and 0 <= gy < Ht.shape[0] - 1):
            return None
        ix, iy = int(gx), int(gy); fx, fy = gx - ix, gy - iy
        a_, b_, c_, d_ = Ht[iy, ix], Ht[iy, ix + 1], Ht[iy + 1, ix], Ht[iy + 1, ix + 1]
        s1 = a_ + (b_ - a_) * fx + (d_ - b_) * fy if fx > fy else a_ + (c_ - a_) * fy + (d_ - c_) * fx
        s2 = a_ + (b_ - a_) * fx + (c_ - a_) * fy if fx + fy < 1 else d_ + (c_ - d_) * (1 - fx) + (b_ - d_) * (1 - fy)
        return max(s1, s2)        # whichever way BeamNG splits the cell

    out = []
    drive_check.misses = {}
    for ai in sorted(Path(track).glob("**/ai/fast_lane.ai")):
        b = ai.read_bytes()
        n_pts = struct.unpack_from("<4i", b, 0)[1]
        pts = np.frombuffer(b, dtype=np.dtype([("p", "<f4", 3), ("l", "<f4"), ("i", "<i4")]), count=n_pts,
                            offset=16)["p"].astype(np.float64)
        q = np.stack([pts[:, 0], -pts[:, 2], pts[:, 1]], 1) - off
        q = q[:: max(1, len(q) // samples)]
        cov = above = 0
        misses = []
        for x, y, z in q:
            s = surface(x, y, z)
            on_terrain = False
            if Ht is not None and (s is None or not -0.5 < z - s < 1.0):
                tm = terrain_max(x, y)
                on_terrain = tm is not None and -0.5 < z - tm < 1.0     # verge / runoff the AI line cuts over
            # AC records AI lines a little above the surface (more over crests): road up to 1 m below counts
            if (s is not None and -0.5 < z - s < 1.0) or on_terrain:
                cov += 1
            else:
                misses.append((x, y, z, s))
            if Ht is not None:
                tm = terrain_max(x, y)
                if tm is not None and tm > z + 0.02:
                    above += 1
        lay = ai.parent.parent.name if ai.parent.parent != Path(track) else "(main)"
        out.append((lay, len(q), cov / len(q), above / len(q)))
        drive_check.misses[lay] = misses
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("level_dir")
    ap.add_argument("--ac-track")
    ap.add_argument("--px", type=float, default=8.0, help="metres per preview pixel")
    ap.add_argument("--drive-check", action="store_true",
                    help="with --ac-track: check the AI racing lines have road under them and no terrain above")
    a = ap.parse_args(argv)
    level = Path(a.level_dir)
    lvl_rel = "/levels/" + level.name
    errors = []

    mats = {}
    for mf in level.rglob("*.materials.json"):
        mats.update(json.loads(mf.read_text()))
    for name, m in mats.items():
        if not isinstance(m, dict):
            continue
        fields = m["Stages"][0].items() if m.get("Stages") else m.items()   # TerrainMaterial: flat fields
        for k, v in fields:
            if (k.endswith("Map") or k.endswith("Tex")) and isinstance(v, str) and v.startswith(lvl_rel + "/") and \
                    not (level / v[len(lvl_rel) + 1:]).exists():
                errors.append(f"material {name}: missing texture {v}")
    ter = [o for o in read_items(level) if o.get("class") == "TerrainBlock"]
    for o in ter:
        tf = level / o["terrainFile"][len(lvl_rel) + 1:]
        if not tf.exists():
            errors.append(f"terrain file missing: {o['terrainFile']}")
        else:
            b = tf.read_bytes()[:5]
            n = int.from_bytes(b[1:5], "little")
            if tf.stat().st_size < 5 + 3 * n * n:
                errors.append(f"terrain file truncated: {tf.name}")
        if o.get("materialTextureSet") and o["materialTextureSet"] not in mats:
            errors.append(f"TerrainBlock materialTextureSet {o['materialTextureSet']} not defined")
    # terrain textures must match the sizes their TerrainMaterialTextureSet declares
    from PIL import Image as _I
    sets = {k: m for k, m in mats.items() if isinstance(m, dict) and m.get("class") == "TerrainMaterialTextureSet"}
    for name, m in mats.items():
        if not isinstance(m, dict) or m.get("class") != "TerrainMaterial" or not sets:
            continue
        ts_ = next(iter(sets.values()))
        for k, v_ in m.items():
            if not (isinstance(v_, str) and v_.startswith(lvl_rel + "/") and k.endswith("Tex")):
                continue
            kind = "baseTexSize" if "Base" in k else "detailTexSize" if "Detail" in k else "macroTexSize"
            want = tuple(ts_.get(kind, []))
            fp = level / v_[len(lvl_rel) + 1:]
            if fp.exists() and want:
                with _I.open(fp) as im:
                    if im.size != want:
                        errors.append(f"terrain material {m.get('internalName')}: {fp.name} is {im.size}, texture set wants {want}")
    # textures BeamNG would refuse to import: power-of-two sides of at least 16 px; DDS compressed or 32-bit
    checked = set()
    for name, m in mats.items():
        if not isinstance(m, dict):
            continue
        for st in (m.get("Stages") or [m]):
            for kk, p in st.items():
                if not (isinstance(p, str) and p.startswith(lvl_rel + "/") and p not in checked):
                    continue
                checked.add(p)
                fp = level / p[len(lvl_rel) + 1:]
                if not fp.exists() or m.get("class") == "TerrainMaterialTextureSet":
                    continue
                try:
                    if fp.suffix.lower() == ".dds":
                        b = fp.read_bytes()[:128]
                        h, w = int.from_bytes(b[12:16], "little"), int.from_bytes(b[16:20], "little")
                        fl, bits = int.from_bytes(b[80:84], "little"), int.from_bytes(b[88:92], "little")
                        fmt_bad = not (fl & 0x4) and bits != 32
                    else:
                        from PIL import Image as _I2
                        with _I2.open(fp) as im:
                            w, h = im.size
                        fmt_bad = False
                    p2 = lambda x: x >= 16 and not (x & (x - 1))
                    if not (p2(w) and p2(h)) or fmt_bad:
                        errors.append(f"texture BeamNG won't import: {fp.name} ({w}x{h}{', unsupported DDS format' if fmt_bad else ''})")
                except Exception:
                    pass

    # every group the scene declares must have its folder (BeamNG: "Unable to deserialize ... Path not found")
    for o in read_items(level):
        if o.get("class") == "SimGroup" and o.get("__parent") == "MissionGroup":
            if not (level / "main" / "MissionGroup" / o["name"] / "items.level.json").exists():
                errors.append(f"scene group '{o['name']}' is declared but its items.level.json is missing")
    tnames = {m.get("internalName") for m in mats.values() if isinstance(m, dict) and m.get("class") == "TerrainMaterial"}
    for o in read_items(level):
        if o.get("class") == "GroundCover":
            if o.get("material") not in mats:
                errors.append(f"GroundCover {o['name']}: material {o.get('material')} not defined")
            for t in o.get("Types", []):
                if t.get("layer") and t["layer"] not in tnames:
                    errors.append(f"GroundCover {o['name']}: layer {t['layer']} has no terrain material")

    objs = read_items(level)
    statics = [o for o in objs if o["class"] == "TSStatic"]
    spawns = [o for o in objs if o["class"] == "SpawnSphere"]
    pts = {"col": [], "vis": []}
    tris = verts = 0
    for st in statics:
        dae = level / st["shapeName"][len(lvl_rel) + 1:]
        if not dae.exists():
            errors.append(f"{st['name']}: missing {dae}"); continue
        try:
            root = ET.parse(dae).getroot()
        except ET.ParseError as e:
            errors.append(f"{dae.name}: XML error {e}"); continue
        kind = "col" if st.get("collisionType", "None") != "None" else "vis"
        origin = np.array(st["position"])
        for geom in root.iterfind(".//c:geometry", NS):
            arrays = {}
            for src in geom.iterfind(".//c:source", NS):
                fa = src.find("c:float_array", NS)
                vals = np.array(fa.text.split(), dtype=np.float32)
                if len(vals) != int(fa.get("count")):
                    errors.append(f"{dae.name}/{geom.get('id')}: float_array count mismatch")
                arrays[src.get("id").rsplit("-", 1)[1]] = vals
            tri = geom.find(".//c:triangles", NS)
            idx = np.array(tri.find("c:p", NS).text.split(), dtype=np.int64)
            nv = len(arrays["p"]) // 3
            if len(idx) != 3 * int(tri.get("count")) or (len(idx) and idx.max() >= nv):
                errors.append(f"{dae.name}/{geom.get('id')}: bad index buffer")
            if tri.get("material") not in mats:
                errors.append(f"{dae.name}: material {tri.get('material')} not in materials file")
            p = arrays["p"].reshape(-1, 3)[:, :2] + origin[:2]
            pts[kind].append(p[:: max(1, len(p) // 4000)])
            tris += int(tri.get("count")); verts += nv

    allp = np.concatenate(pts["col"] + pts["vis"])
    lo, hi = allp.min(0), allp.max(0)
    W, H = int((hi[0] - lo[0]) / a.px) + 1, int((hi[1] - lo[1]) / a.px) + 1
    img = Image.new("RGB", (W, H), (20, 20, 24))
    d = ImageDraw.Draw(img)

    def to_px(xy):
        return (xy[:, 0] - lo[0]) / a.px, (hi[1] - xy[:, 1]) / a.px   # north up

    for kind, col in (("vis", (110, 110, 110)), ("col", (230, 60, 50))):
        for p in pts[kind]:
            xs, ys = to_px(p)
            for x, y in zip(xs, ys):
                d.point((x, y), fill=col)

    if a.ac_track:
        info = json.loads((level / "kn5_port.json").read_text())
        off = np.array(info["offset_bng"])
        track = Path(a.ac_track)
        mapini = next(track.rglob("map.ini"), None)
        mappng = track / "map.png"
        if mapini and mappng.exists():
            cfg = dict(re.findall(r"(\w+)=([-\d.]+)", mapini.read_text()))
            sc, xo, zo = float(cfg["SCALE_FACTOR"]), float(cfg["X_OFFSET"]), float(cfg["Z_OFFSET"])
            m = np.array(Image.open(mappng).convert("RGBA"))
            kx, ky = float(cfg["WIDTH"]) / m.shape[1], float(cfg["HEIGHT"]) / m.shape[0]  # png may be downscaled
            ys, xs = np.nonzero(m[:, :, 3] > 128)
            ac_x = xs * kx * sc - xo; ac_z = ys * ky * sc - zo
            bx = ac_x - off[0]; by = -ac_z - off[1]
            px, py = to_px(np.stack([bx, by], 1)[:: max(1, len(xs) // 60000)])
            for x, y in zip(px, py):
                d.point((x, y), fill=(60, 220, 90))

    for s in spawns:
        x, y = to_px(np.array([s["position"][:2]]))
        d.ellipse((x[0] - 5, y[0] - 5, x[0] + 5, y[0] + 5), outline=(80, 140, 255), width=2)
    out = level / "preview_topdown.png"
    img.save(out)

    print(f"TSStatics: {len(statics)}  spawns: {len(spawns)}  materials: {len(mats)}")
    print(f"triangles: {tris:,}  vertices: {verts:,}")
    print(f"extent: {np.round(hi - lo).tolist()} m  preview: {out}")
    if a.drive_check and a.ac_track:
        for lay, n_, cov, above in drive_check(level, a.ac_track):
            print(f"AI line {lay}: {n_} points, road surface under {cov * 100:.1f}%, terrain poking above "
                  f"{above * 100:.2f}%")
            if cov < 0.98 or above > 0.005:
                errors.append(f"AI line {lay}: road surface under {cov * 100:.1f}%, terrain above "
                              f"{above * 100:.2f}%")
    if errors:
        print(f"{len(errors)} problem(s):"); [print("  -", e) for e in errors[:50]]
        sys.exit(1)
    print("no problems found")


if __name__ == "__main__":
    main()
