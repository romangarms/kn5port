"""Build a BeamNG TerrainBlock (with GroundCover grass) from an AC track's ground meshes.

    python kn5port (terrain step) "<AC track folder>" "<mod folder>\\levels\\<level>" ^
        --beamng "C:\\Program Files (x86)\\Steam\\steamapps\\common\\BeamNG.drive"

Run order: kn5port convert --terrain 4096  ->  kn5port (terrain step)  ->  kn5port (forest step)

What it does:
  * rasterises AC's ground (multilayer tiles near the road, coarse distant terrain elsewhere) into a
    SIZE x SIZE heightmap (theTerrain.ter)
  * carves the terrain just below every remaining collision surface (road, shoulders, lots, rock cuts),
    so it never pokes through the road meshes
  * paints terrain material layers from AC's ground masks (forest floor / grass / dry grass / soil) and a
    terrain-wide base colour map from AC's colours; detail textures come from West Coast USA
  * copies West Coast USA's GroundCover (grass, weeds, groundcover shrubs) for those layers
  * re-snaps any existing forest items to the terrain surface
"""
import argparse
import io
import json
import math
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from .kn5 import KN5, KN5Error
from .profile import Profile, read_surfaces, mostly_flat, phys_key, material_uses, is_natural_ground

DONOR = "west_coast_usa"
# terrain layers in .ter order; AC mask channel -> layer
LAYERS = ["Forest", "grass", "dirt_grass", "dirt", "gravel", "Rock_cliff", "BeachSand"]   # from the profile
MASK_TO_LAYER = {0: 0, 1: 1, 2: 2, 3: 3}        # AC multilayer mask R, G, B, A -> layer index
BASE_TEX = 4096                                  # terrain-wide base texture size (texture set baseTexSize)
CARVE_DEPTH = 0.2                               # metres below road surfaces
WATER_DEPTH = 0.6                               # metres below water surfaces
NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
PHYS_RE = re.compile(r"^(\d+)([A-Z]+)")
FAR_RE = re.compile(r"FAR|TERRAIN|GROUND|HILL|MOUNTAIN", re.I)   # replaced from the profile in main()
PROFILE = Profile()
SURFACES = {}


def classify_ground(mesh, file_is_horizon, uses):
    """'horizon' | 'near' | 'far' | None for one AC mesh (which meshes are ground: profile.is_natural_ground)."""
    if file_is_horizon or PROFILE.rx("horizon_re").search(mesh.name):
        return "horizon"
    if not is_natural_ground(mesh, SURFACES, PROFILE, uses):
        return None
    p = mesh.positions
    big = len(p) and max(np.ptp(p[:, 0]), np.ptp(p[:, 2])) > 3000
    if mesh.material.shader.startswith("ksMultilayer"):
        return "far" if big else "near"      # a multilayer mesh kilometres across is a background landscape
    if phys_key(mesh.name, SURFACES):
        return "near"
    return "far"


def ac_to_bng(p):
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1)


class Grid:
    def __init__(self, x0, y0, sq, n):
        self.x0, self.y0, self.sq, self.n = x0, y0, sq, n

    def raster(self, tris, attrs, out, priority=None, reduce=None):
        """Rasterise triangles (N,3,3 world) onto grid points.
        attrs: (N,3,K) per-vertex values, interpolated; out: list of K arrays (n,n) indexed [y, x].
        reduce='min' keeps the minimum of attr 0 instead of overwriting."""
        sq, n = self.sq, self.n
        gx = (tris[:, :, 0] - self.x0) / sq
        gy = (tris[:, :, 1] - self.y0) / sq
        x_lo = np.ceil(gx.min(1)).astype(np.int64); x_hi = np.floor(gx.max(1)).astype(np.int64)
        y_lo = np.ceil(gy.min(1)).astype(np.int64); y_hi = np.floor(gy.max(1)).astype(np.int64)
        w = np.maximum(x_hi - x_lo + 1, 0); h = np.maximum(y_hi - y_lo + 1, 0)
        span = np.maximum(w, h)
        has = (w > 0) & (h > 0) & (x_hi >= 0) & (y_hi >= 0) & (x_lo < n) & (y_lo < n)
        for b in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512):
            sel = np.flatnonzero(has & (span <= b) & (span > b // 2 if b > 1 else True))
            for c0 in range(0, len(sel), max(1, 2_000_000 // (b * b))):
                ids = sel[c0:c0 + max(1, 2_000_000 // (b * b))]
                ox, oy = np.meshgrid(np.arange(b), np.arange(b))
                px = x_lo[ids, None] + ox.ravel()[None]; py = y_lo[ids, None] + oy.ravel()[None]
                ax, ay = gx[ids, 0, None], gy[ids, 0, None]
                bx, by = gx[ids, 1, None], gy[ids, 1, None]
                cx, cy = gx[ids, 2, None], gy[ids, 2, None]
                d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
                with np.errstate(divide="ignore", invalid="ignore"):
                    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
                    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                l3 = 1 - l1 - l2
                ok = (l1 >= -1e-6) & (l2 >= -1e-6) & (l3 >= -1e-6) & (np.abs(d) > 1e-12) & \
                     (px >= 0) & (py >= 0) & (px < n) & (py < n)
                r, cidx = np.nonzero(ok)
                if not len(r):
                    continue
                tid = ids[r]
                L1, L2, L3 = l1[r, cidx], l2[r, cidx], l3[r, cidx]
                X, Y = px[r, cidx], py[r, cidx]
                for k, arr in enumerate(out):
                    v = attrs[tid, 0, k] * L1 + attrs[tid, 1, k] * L2 + attrs[tid, 2, k] * L3
                    if reduce == "min" and k == 0:
                        np.minimum.at(arr, (Y, X), v.astype(arr.dtype))
                    elif reduce == "max" and k == 0:
                        np.maximum.at(arr, (Y, X), v.astype(arr.dtype))
                    else:
                        arr[Y, X] = v
                if priority is not None:
                    priority[Y, X] = True


def pyramid_fill(A):
    """Fill NaNs in A from a coarse-to-fine pyramid of the known values (smooth extrapolation)."""
    import warnings
    levels = [A]
    while levels[-1].shape[0] > 8:
        a_ = levels[-1]; m_ = a_.shape[0] // 2
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            levels.append(np.nanmean(a_[:m_ * 2, :m_ * 2].reshape(m_, 2, m_, 2), axis=(1, 3)))
    filled = levels[-1]
    filled[np.isnan(filled)] = np.nanmin(A) if np.isfinite(A).any() else 0
    for lv in reversed(levels[:-1]):
        up = np.asarray(Image.fromarray(filled.astype(np.float32), "F").resize(lv.shape[::-1], Image.BILINEAR))
        filled = np.where(np.isnan(lv), up, lv)
    return filled.astype(np.float32)


def resize_f(A, shape):
    return np.asarray(Image.fromarray(A.astype(np.float32), "F").resize(shape[::-1], Image.BILINEAR))


def terrain_min_h(H, x0, y0, sq, n, x, y):
    """Lowest of the two possible cell triangulations at (x, y)."""
    gx = np.clip((x - x0) / sq, 0, n - 1.0001); gy = np.clip((y - y0) / sq, 0, n - 1.0001)
    ix, iy = gx.astype(np.int64), gy.astype(np.int64); fx, fy = gx - ix, gy - iy
    a, b, c, d = H[iy, ix], H[iy, ix + 1], H[iy + 1, ix], H[iy + 1, ix + 1]
    s1 = np.where(fx > fy, a + (b - a) * fx + (d - b) * fy, a + (c - a) * fy + (d - c) * fx)
    s2 = np.where(fx + fy < 1, a + (b - a) * fx + (c - a) * fy, d + (c - d) * (1 - fx) + (b - d) * (1 - fy))
    return np.minimum(s1, s2)


def build_skirts(tris, H, x0, y0, sq, n, min_gap=0.05, below=0.4, max_seg=1.0):
    """Quads (p_top, q_top, q_bot, p_bot) hanging from the boundary edges of the driving surfaces."""
    # weld vertices across meshes (5 cm) so road/shoulder seams don't count as boundaries
    V = tris.reshape(-1, 3)
    key = np.round(V * 20).astype(np.int64)
    _, vid = np.unique(key, axis=0, return_inverse=True)
    vid = vid.reshape(-1, 3)
    E = np.concatenate([vid[:, [0, 1]], vid[:, [1, 2]], vid[:, [2, 0]]])
    Ep = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    Ei = np.concatenate([tris[:, 2], tris[:, 0], tris[:, 1]])        # the triangle's third (inner) vertex
    ek = np.sort(E, 1)
    ek = ek[:, 0] * (vid.max() + 1) + ek[:, 1]
    u, inv, cnt = np.unique(ek, return_inverse=True, return_counts=True)
    bnd = cnt[inv] == 1
    bnd &= E[:, 0] != E[:, 1]
    P, Q, I = Ep[bnd, 0], Ep[bnd, 1], Ei[bnd]
    # outward = perpendicular to the edge, pointing away from the surface
    e = Q[:, :2] - P[:, :2]
    nrm = np.stack([-e[:, 1], e[:, 0]], 1) / np.maximum(np.linalg.norm(e, axis=1), 1e-9)[:, None]
    flip = ((I[:, :2] - P[:, :2]) * nrm).sum(1) > 0
    nrm[flip] *= -1
    # split long edges so the skirt bottom follows the terrain
    L = np.linalg.norm(Q[:, :2] - P[:, :2], axis=1)
    k = np.maximum(1, np.ceil(L / max_seg)).astype(int)
    segs_p, segs_q, segs_n = [], [], []
    for m in range(int(k.max()) if len(k) else 0):
        sel = k > m
        f0 = (m / k[sel])[:, None]; f1 = ((m + 1) / k[sel])[:, None]
        segs_p.append(P[sel] + (Q[sel] - P[sel]) * f0); segs_q.append(P[sel] + (Q[sel] - P[sel]) * f1)
        segs_n.append(nrm[sel])
    if not segs_p:
        return np.zeros((0, 4, 3))
    P, Q, N = np.concatenate(segs_p), np.concatenate(segs_q), np.concatenate(segs_n)
    hp = terrain_min_h(H, x0, y0, sq, n, P[:, 0], P[:, 1])
    hq = terrain_min_h(H, x0, y0, sq, n, Q[:, 0], Q[:, 1])
    gap = np.maximum(P[:, 2] - hp, Q[:, 2] - hq)
    sel = gap > min_gap + 0.2           # terrain already sits CARVE_DEPTH below everywhere
    P, Q, N, hp, hq = P[sel], Q[sel], N[sel], hp[sel], hq[sel]
    # slope the curtain outwards like an embankment (~1:1.5), meeting the terrain a little below its surface
    def bottom(T, h):
        run = np.clip((T[:, 2] - h) * 1.5, 0.3, 4.0)
        B = T.copy(); B[:, :2] += N * run[:, None]
        hb = terrain_min_h(H, x0, y0, sq, n, B[:, 0], B[:, 1])
        B[:, 2] = np.minimum(T[:, 2] - 0.05, hb - 0.15)
        return B
    return np.stack([P, Q, bottom(Q, hq), bottom(P, hp)], 1)


def write_skirts(level, lvl, quads, cell=500.0, material=None):
    """Write skirt quads as visual-only DAE shapes, one per grid cell, plus their scene group."""
    from .level import write_dae
    out = []
    if not len(quads):
        return out
    c = quads[:, :2, :2].mean(1)
    cx = np.floor(c[:, 0] / cell).astype(int); cy = np.floor(c[:, 1] / cell).astype(int)
    for kx, ky in sorted(set(zip(cx, cy))):
        q = quads[(cx == kx) & (cy == ky)]
        nq = len(q)
        pos = q.reshape(-1, 3).astype(np.float32)
        top = q[:, 0, :2] - q[:, 1, :2]
        L = np.linalg.norm(top, axis=1)
        u0 = np.zeros(nq); u1 = L / 3.0
        h0 = np.zeros(nq); hp = (q[:, 0, 2] - q[:, 3, 2]) / 3.0; hq = (q[:, 1, 2] - q[:, 2, 2]) / 3.0
        uv = np.stack([np.stack([u0, h0], 1), np.stack([u1, h0], 1), np.stack([u1, hq], 1), np.stack([u0, hp], 1)], 1)
        uv = uv.reshape(-1, 2).astype(np.float32)
        # normals: horizontal, perpendicular to the edge
        nrm = np.stack([-top[:, 1], top[:, 0], np.zeros(nq)], 1) / np.maximum(L, 1e-6)[:, None]
        nrm = np.repeat(nrm, 4, 0).astype(np.float32)
        uv[:, 1] = 1 - uv[:, 1]          # write_dae expects DirectX-style v (it flips)
        chunks = []
        for s0 in range(0, nq, 16000):     # 16000 quads = 64000 vertices per mesh
            e0 = min(nq, s0 + 16000)
            base = np.arange(e0 - s0)[:, None] * 4
            ii = np.concatenate([base + [0, 1, 2], base + [0, 2, 3]], 1).reshape(-1).astype(np.uint32)
            chunks.append((material, pos[s0 * 4:e0 * 4], nrm[s0 * 4:e0 * 4], uv[s0 * 4:e0 * 4], ii))
        origin = np.array([(kx + 0.5) * cell, (ky + 0.5) * cell, 0.0])
        name = f"x{kx:+04d}_y{ky:+04d}_skirt".replace("+", "p").replace("-", "m")
        write_dae(level / "art" / "shapes" / "skirts" / f"{name}.dae", chunks, origin)
        out.append({"class": "TSStatic", "name": f"skirt_{name}", "__parent": "road_skirts",
                    "shapeName": f"/levels/{lvl}/art/shapes/skirts/{name}.dae",
                    "position": [round(float(v), 3) for v in origin], "collisionType": "None", "decalType": "None"})
    return out


def load_col_tris(level):
    """Remaining collision triangles in the converted level (road, shoulders, lots, cuts...)."""
    rel = "/levels/" + level.name + "/"
    mats = {}
    for mf in level.rglob("*.materials.json"):
        try:
            mats.update(json.loads(mf.read_text()))
        except ValueError:
            pass
    def is_ground(mname):      # leftover AC ground (outside the terrain) must not shape the terrain
        m = mats.get(mname, {})
        return bool(m.get("kn5_ground")) or m.get("ac_shader", "").startswith("ksMultilayer")
    tris = []
    for f in (level / "main").rglob("items.level.json"):
        for line in f.read_text().splitlines():
            o = json.loads(line) if line.strip() else {}
            if o.get("class") != "TSStatic" or o.get("collisionType", "None") == "None":
                continue
            r = ET.parse(level / o["shapeName"][len(rel):]).getroot()
            for g in r.iterfind(".//c:geometry", NS):
                if is_ground(g.find(".//c:triangles", NS).get("material")):
                    continue
                fa = g.find(f".//c:source[@id='{g.get('id')}-p']/c:float_array", NS)
                p = np.array(fa.text.split(), np.float64).reshape(-1, 3) + o["position"]
                idx = np.array(g.find(".//c:triangles/c:p", NS).text.split(), np.int64).reshape(-1, 3)
                tris.append(p[idx])
    t = np.concatenate(tris)
    nz = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    up = np.abs(nz[:, 2]) / np.maximum(np.linalg.norm(nz, axis=1), 1e-9)
    keep = up > 0.3             # leave walls out
    return t[keep], up[keep] > 0.9   # flat = driving surfaces (road, shoulders, lots)


MAX_TEX = 4096      # textures are only sampled; larger ones are downscaled first (keeps memory bounded)


def tex_img(k, name):
    """Texture as a uint8 RGBA array (at most MAX_TEX per side). sample() returns 0..1 floats."""
    t = next((t for t in k.textures if t.name == name), None)
    if t is None:
        return None
    try:
        im = Image.open(io.BytesIO(k.texture_bytes(t)))
        im.load()
    except Exception:
        return None
    if max(im.size) > MAX_TEX:
        s = MAX_TEX / max(im.size)
        im = im.resize((max(1, int(im.size[0] * s)), max(1, int(im.size[1] * s))), Image.BILINEAR)
    return np.asarray(im.convert("RGBA"), np.uint8)


def classify_colour(c, RI):
    """Terrain role per colour (N,3 in 0..1), leaning green: green or colourless mid-tones -> grass (dark ->
    forest floor), warm tan -> dry grass, very bright and colourless -> rock. Bare dirt is left to the verge."""
    c = np.asarray(c, np.float32)
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = c.max(1) - c.min(1)
    green = (g > r * 1.03) & (g > b * 1.15)
    out = np.full(len(c), RI["dry_grass"], np.uint8)
    out[sat < 0.08] = RI["grass"]                         # greys: green grass rather than bare ground
    out[(lum > 0.62) & (sat < 0.04)] = RI["rock"]
    out[(lum < 0.36) & (r > b * 1.3)] = RI["forest"]
    out[lum < 0.22] = RI["forest"]
    out[green] = RI["grass"]
    out[green & (lum < 0.26)] = RI["forest"]
    return out


_LAYER_MEANS = {}


def layer_mean(k, name):
    """Average colour (0..1) of a detail texture; a greenish default when it's missing or unreadable."""
    key = (k.path, name)
    if key not in _LAYER_MEANS:
        img = tex_img(k, name) if name else None
        _LAYER_MEANS[key] = (img[:, :, :3].reshape(-1, 3).mean(0) / 255.0) if img is not None else \
            np.array([0.30, 0.34, 0.22])
    return _LAYER_MEANS[key]


def sample(img, u, v):
    h, w = img.shape[:2]
    x = np.clip((u % 1.0) * w, 0, w - 1).astype(np.int64)
    y = np.clip((v % 1.0) * h, 0, h - 1).astype(np.int64)
    return img[y, x].astype(np.float32) / 255.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track_dir")
    ap.add_argument("level_dir")
    ap.add_argument("--beamng", required=True, help="BeamNG.drive install folder, or its content\\levels folder")
    ap.add_argument("--no-groundcover", action="store_true")
    ap.add_argument("--donor", default=None, help="stock BeamNG level whose terrain materials and grass to use")
    ap.add_argument("--profile", default=None, help="track profile JSON (see kn5port/profile.py)")
    ap.add_argument("--near-gain", type=float, default=0, help="brightness multiplier for the near-road ground "
                    "(default: auto, matched to the open distant terrain)")
    ap.add_argument("--ao", type=float, default=0.35, help="strength of the terrain materials' ambient occlusion "
                    "(1 = as in West Coast USA; default 0.35, lighter)")
    a = ap.parse_args(argv)
    t0 = time.time()
    track, level = Path(a.track_dir), Path(a.level_dir)
    global PROFILE, SURFACES, FAR_RE, LAYERS, MASK_TO_LAYER, CARVE_DEPTH
    PROFILE = Profile.load(a.profile, track)
    SURFACES = read_surfaces(track)
    FAR_RE = PROFILE.rx("ground_name_re")
    from . import donor as donor_mod
    DONOR_LEVEL = a.donor or PROFILE.donor_level
    pal = donor_mod.analyze(a.beamng, DONOR_LEVEL)
    if not pal["terrain"]:
        raise SystemExit(f"{DONOR_LEVEL} has no terrain materials to borrow; pick another palette")
    # terrain layers: one per distinct donor material used by a role, plus a verge layer no grass grows on
    LAYERS = []
    for r_ in donor_mod.ROLES:
        if pal["terrain"][r_] not in LAYERS:
            LAYERS.append(pal["terrain"][r_])
    LAYERS.append("kn5_verge")
    RI = {r_: LAYERS.index(pal["terrain"][r_]) for r_ in donor_mod.ROLES}
    RI["verge"] = LAYERS.index("kn5_verge")
    MASK_TO_LAYER = {i: RI[r_] for i, r_ in enumerate(PROFILE.mask_roles)}
    roles_txt = ", ".join(f"{r_}={pal['terrain'][r_]}" for r_ in donor_mod.ROLES)
    print(f"terrain palette: {DONOR_LEVEL} ({roles_txt})")
    CARVE_DEPTH = PROFILE.carve_depth
    if a.ao == 0.35:
        a.ao = PROFILE.terrain_ao
    port = json.loads((level / "kn5_port.json").read_text())
    if not port.get("terrain"):
        raise SystemExit("run kn5port convert with --terrain SIZE first")
    off = np.array(port["offset_bng"])
    n, sq = port["terrain"]["size"], port["terrain"]["square"]
    x0, y0 = port["terrain"]["rect"][0], port["terrain"]["rect"][1]
    grid = Grid(x0, y0, sq, n)
    lvl = port.get("level", level.name)
    print(f"terrain {n}x{n} at {sq} m = {n * sq / 1000:.1f} km, corner ({x0:.0f}, {y0:.0f})")

    H = np.full((n, n), np.nan, np.float32)
    U = np.zeros((n, n), np.float32); V = np.zeros((n, n), np.float32)
    M = np.full((n, n), -1, np.int16)                  # source material id per point
    sources = []                                       # (kn5 path, material, kind)

    # ---- 1. rasterise AC ground, lowest priority first: horizon backdrop, distant terrain (coarse LOD,
    #         then full), near multilayer tiles (coarse LOD, then full). Later passes overwrite earlier ones.
    order = ("horizon", "far_lod", "far", "near_lod", "near")
    layer_of = {"horizon": "horizon", "far_lod": "far", "far": "far", "near_lod": "near", "near": "near"}
    HL = {k_: np.full((n, n), np.nan, np.float32) for k_ in ("horizon", "far", "near")}
    sand_mats = set()
    water_tris = []
    water_re = PROFILE.rx("water_re")
    groups = {kd: [] for kd in order}
    for f in sorted(track.glob("*.kn5")):
        is_horizon = bool(PROFILE.rx("horizon_re").search(f.stem))
        try:
            k = KN5(str(f))
        except KN5Error as e:
            print(f"  ! skipping {f.name}: {e}")
            continue
        with k:
            uses = material_uses(k, SURFACES, PROFILE)
            for m in k.meshes():
                if not len(m.indices):
                    continue
                if not PHYS_RE.match(m.name) and water_re.search(m.name + " " + m.material.name) and \
                        mostly_flat(m, 0.9):
                    pw = ac_to_bng(m.positions.astype(np.float64)) - off
                    water_tris.append(pw[m.indices.reshape(-1, 3)])
                    continue
                kind = classify_ground(m, is_horizon, uses)
                if kind is None:
                    continue
                if kind == "near" and not m.material.shader.startswith("ksMultilayer"):
                    key = phys_key(m.name, SURFACES)
                    if key and PROFILE.ground_type(key, SURFACES.get(key)) == "SAND":
                        sand_mats.add((str(f), m.material.name))
                key = (str(f), m.material.name, kind)
                if key not in sources:
                    sources.append(key)
                mid = sources.index(key)
                p = ac_to_bng(m.positions.astype(np.float64)) - off
                t = m.indices.reshape(-1, 3)
                at = np.concatenate([p[:, 2:3], m.uvs, np.full((len(p), 1), mid + 1)], 1)  # 0 = no hit
                lod = "_lod" if m.lod_in > 0 and kind != "horizon" else ""
                groups[kind + lod].append((p[t].astype(np.float32), at[t].astype(np.float32)))
    for kind in order:
        if not groups[kind]:
            continue
        tris = np.concatenate([g[0] for g in groups[kind]]).astype(np.float64)
        att = np.concatenate([g[1] for g in groups[kind]]).astype(np.float64)
        Mf = np.zeros((n, n), np.float32)
        grid.raster(tris, att, [HL[layer_of[kind]], U, V, Mf])
        covered = Mf > 0.5          # material ids are constant per triangle, so interpolation is exact
        M[covered] = np.rint(Mf[covered]).astype(np.int16) - 1
        print(f"  {kind:8s}: {len(tris):,} triangles")
        del tris, att
    # Merge the layers. Each AC layer is offset from the one below it where they overlap (the distant mesh is
    # sunk under the near tiles, the horizon sits hundreds of metres lower), so the lower layer is bent to
    # meet the upper one: the overlap offset is extended outwards and faded out over FADE metres.
    def blend(base, top, fade):
        cov = ~np.isnan(top)
        if not cov.any():
            return base
        delta = np.where(cov & ~np.isnan(base), top - base, np.nan)
        if np.isnan(delta).all():
            return np.where(cov, top, base)
        ext = pyramid_fill(delta)
        cs = 8
        cm = resize_f(cov.astype(np.float32), (n // cs, n // cs)) > 0.01
        steps = int(math.ceil(fade / (cs * sq)))
        dist = np.full(cm.shape, steps + 1, np.float32); cur = cm.copy(); dist[cur] = 0
        for i in range(1, steps + 1):
            nxt = cur.copy()
            for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nxt |= np.roll(np.roll(cur, dy, 0), dx, 1)
            dist[nxt & ~cur] = i; cur = nxt
        f = np.clip(1 - resize_f(dist, (n, n)) * cs * sq / fade, 0, 1)
        return np.where(cov, top, base + ext * f).astype(np.float32)

    H = HL["horizon"]
    H = blend(H, HL["far"], PROFILE.fade_horizon)
    H = blend(H, HL["near"], PROFILE.fade_far)
    del HL
    missing = np.isnan(H)
    print(f"rasterised + blended in {time.time() - t0:.0f}s; uncovered points: {missing.mean() * 100:.2f}%")
    if missing.any():
        H = pyramid_fill(H)
        M[missing] = -1

    # the outermost samples are only partly covered by the raster; extend the edge inwards-out
    H[:3] = H[3]; H[-3:] = H[-4]; H[:, :3] = H[:, 3:4]; H[:, -3:] = H[:, -4:-3]

    # ---- 2. fit the terrain to the remaining meshes (road, shoulders, lots, embankment skirts, rock cuts)
    #   * under any mesh, the terrain hugs just below the TOPMOST surface, so the sloped skirts AC hangs off
    #     the shoulders are filled underneath instead of left floating
    #   * flat driving surfaces cap it from above (also in every grid cell they touch), so it never pokes through
    ts = time.time()
    col, flat = load_col_tris(level)

    def splat(tris, reduce):
        """Conservative per-cell extreme: every cell a triangle touches gets its corners updated."""
        T = np.full((n, n), np.inf if reduce == "min" else -np.inf, np.float32)
        pts = [tris[:, i] for i in range(3)] + [tris.mean(1)]
        for a_, b_ in ((0, 1), (1, 2), (2, 0)):
            L_ = np.linalg.norm(tris[:, b_, :2] - tris[:, a_, :2], axis=1)
            steps = np.maximum(1, np.ceil(L_ / (sq / 2))).astype(int)
            for k_ in range(1, int(steps.max()) if len(steps) else 1):
                sel = steps > k_
                pts.append(tris[sel, a_] + (tris[sel, b_] - tris[sel, a_]) * (k_ / steps[sel])[:, None])
        P_ = np.concatenate(pts)
        ix = np.floor((P_[:, 0] - x0) / sq).astype(np.int64); iy = np.floor((P_[:, 1] - y0) / sq).astype(np.int64)
        op = np.minimum.at if reduce == "min" else np.maximum.at
        for dx in (0, 1):
            for dy in (0, 1):
                X, Y = ix + dx, iy + dy
                ok = (X >= 0) & (Y >= 0) & (X < n) & (Y < n)
                op(T, (Y[ok], X[ok]), P_[ok, 2].astype(np.float32))
        return T

    S_top = np.full((n, n), -np.inf, np.float32)          # topmost surface at each point (all meshes)
    grid.raster(col, col[:, :, 2:3], [S_top], reduce="max")
    C_flat = np.full((n, n), np.inf, np.float32)          # lowest driving surface at each point
    grid.raster(col[flat], col[flat][:, :, 2:3], [C_flat], reduce="min")
    under = np.isfinite(S_top)
    before = H.copy()
    H = np.where(under, S_top - CARVE_DEPTH, H)                       # start just under the top surface
    # corners of every cell a mesh touches start as high as that mesh, so the terrain rises to meet shoulder
    # edges and skirts instead of falling away under them; the fit below lowers them only where needed
    T_top = splat(col, "max")
    ring = np.isfinite(T_top) & ~under
    H = np.where(ring, np.maximum(H, T_top - CARVE_DEPTH), H)
    H = np.where(np.isfinite(C_flat), np.minimum(H, C_flat - CARVE_DEPTH), H)

    # constraint samples on the driving surfaces: vertices, centroids and edge points every ~1.25 m
    ft = col[flat]
    pts = [ft[:, i] for i in range(3)] + [ft.mean(1)]
    for a_, b_ in ((0, 1), (1, 2), (2, 0)):
        L_ = np.linalg.norm(ft[:, b_, :2] - ft[:, a_, :2], axis=1)
        steps = np.maximum(1, np.ceil(L_ / (sq / 4))).astype(int)
        for k_ in range(1, int(steps.max()) if len(steps) else 1):
            sel = steps > k_
            pts.append(ft[sel, a_] + (ft[sel, b_] - ft[sel, a_]) * (k_ / steps[sel])[:, None])
    S = np.concatenate(pts)
    gx = (S[:, 0] - x0) / sq; gy = (S[:, 1] - y0) / sq
    ok = (gx >= 0) & (gy >= 0) & (gx < n - 1) & (gy < n - 1)
    S, gx, gy = S[ok], gx[ok], gy[ok]
    ix, iy = gx.astype(np.int64), gy.astype(np.int64); fx, fy = gx - ix, gy - iy
    lim = (S[:, 2] - CARVE_DEPTH).astype(np.float64)
    # corners a=(ix,iy) b=(ix+1,iy) c=(ix,iy+1) d=(ix+1,iy+1); both possible diagonal splits are constrained
    A_ = iy * n + ix; B_ = A_ + 1; C_ = A_ + n; D_ = A_ + n + 1
    up_ = fx > fy
    split1 = (np.where(up_, A_, A_), np.where(up_, B_, C_), D_,
              np.where(up_, 1 - fx, 1 - fy), np.where(up_, fx - fy, fy - fx), np.where(up_, fy, fx))
    lo_ = fx + fy < 1
    split2 = (np.where(lo_, A_, D_), B_, C_,
              np.where(lo_, 1 - fx - fy, fx + fy - 1), np.where(lo_, fx, 1 - fy), np.where(lo_, fy, 1 - fx))
    Hf = H.ravel().astype(np.float64)
    for it in range(200):
        worst = 0.0
        dec = np.zeros_like(Hf)
        for (i1, i2, i3, w1, w2, w3) in (split1, split2):
            v = w1 * Hf[i1] + w2 * Hf[i2] + w3 * Hf[i3] - lim
            bad = v > 0.005
            if not bad.any():
                continue
            worst = max(worst, float(v[bad].max()))
            ww = w1 ** 2 + w2 ** 2 + w3 ** 2
            for ii, w in ((i1, w1), (i2, w2), (i3, w3)):
                np.maximum.at(dec, ii[bad], v[bad] * w[bad] / ww[bad])
        if worst == 0.0:
            break
        # damped: half steps settle onto the tightest fit instead of overshooting; finish exactly
        Hf -= dec * (0.5 if it < 150 else 1.05)
    H = Hf.reshape(n, n).astype(np.float32)
    n_constraints = len(S)
    print(f"fitted to {len(col):,} mesh triangles ({flat.sum():,} driving surface, {n_constraints:,} constraint points, "
          f"{it + 1} passes, worst remaining {worst:.3f} m): {(H > before + 0.01).sum():,} points raised, "
          f"{(H < before - 0.01).sum():,} lowered in {time.time() - ts:.0f}s")
    del S, gx, gy, ix, iy, fx, fy, lim, A_, B_, C_, D_, up_, lo_, split1, split2, Hf, dec, before
    import gc
    gc.collect()
    road = np.isfinite(C_flat)
    # no groundcover under or right beside the driving surfaces: every corner of every cell they touch, plus
    # one more ring of cells, gets a layer that no GroundCover type uses
    no_cover = np.isfinite(splat(ft, "min"))
    for _ in range(1):
        nb = no_cover.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            nb |= np.roll(np.roll(no_cover, dy, 0), dx, 1)
        no_cover = nb

    # ---- 2a. water: AC lake beds are often coarse and poke through the water surface; keep the terrain at
    # least WATER_DEPTH under every water mesh (and grow no grass there, see layers below)
    under_water = np.zeros((n, n), bool)
    if water_tris:
        WT = np.concatenate(water_tris)
        W = np.full((n, n), np.inf, np.float32)
        grid.raster(WT, WT[:, :, 2:3], [W], reduce="min")
        Wd = W.copy()                                   # one cell of margin around the shoreline
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            Wd = np.minimum(Wd, np.roll(np.roll(W, dy, 0), dx, 1))
        under_water = np.isfinite(Wd)
        lowered = (H > Wd - WATER_DEPTH) & under_water
        H = np.where(under_water, np.minimum(H, Wd - WATER_DEPTH), H)
        print(f"water: {len(WT):,} triangles, {under_water.sum():,} terrain points kept under water "
              f"({lowered.sum():,} lowered)")
        del W, Wd, WT

    # ---- 2b. skirts: AC hangs sloped curtains off its road edges into the ground; a 5 m terrain can't follow
    # a shoulder edge closely, so curtains are generated along the outer edges of the driving surfaces,
    # down to just below the terrain wherever the terrain falls away from the edge.
    ts = time.time()
    skirt_quads = build_skirts(ft, H, x0, y0, sq, n)
    del S_top, C_flat, T_top, col, ft
    gc.collect()
    print(f"skirts: {len(skirt_quads):,} edge segments with a gap under them, in {time.time() - ts:.0f}s")

    # ---- 3. layers + base colour from AC textures
    ts = time.time()
    layer = np.full((n, n), RI["forest"], np.uint8)
    color = np.zeros((n, n, 3), np.float32) + [0.30, 0.34, 0.22]   # greenish where no source
    near_px = np.zeros((n, n), bool)
    by_kn5 = {}
    color_f = color.reshape(-1, 3)          # flat views: sources are processed in chunks of points to keep
    layer_f = layer.reshape(-1)             # memory bounded on large terrains
    near_f = near_px.reshape(-1)
    U_f, V_f, M_f = U.reshape(-1), V.reshape(-1), M.reshape(-1)
    CHUNK = 1_500_000
    for mid, (fp, matname, kind) in enumerate(sources):
        idx = np.flatnonzero(M_f == mid)
        if not len(idx):
            continue
        if fp not in by_kn5:
            by_kn5[fp] = KN5(fp)
        k = by_kn5[fp]
        mat = next(mm for mm in k.materials if mm.name == matname)
        if kind == "near" and "txMask" not in mat.samplers:
            # ordinary textured ground (e.g. a circuit's grass runoff): the texture tiles every few metres,
            # so use its average colour instead of point samples
            img = tex_img(k, mat.samplers.get("txDiffuse", ""))
            c = (img[:, :, :3].reshape(-1, 3).mean(0) / 255.0) if img is not None else np.array([0.30, 0.34, 0.22])
            color_f[idx] = c
            near_f[idx] = True
            layer_f[idx] = RI["sand"] if (fp, matname) in sand_mats else classify_colour(c[None, :], RI)[0]
            continue
        ml = "txMask" in mat.samplers
        img = tex_img(k, mat.samplers.get("txDiffuse", ""))
        mask = tex_img(k, mat.samplers["txMask"]) if ml else None
        cols = np.stack([layer_mean(k, mat.samplers.get(f"txDetail{c_}", "")) for c_ in "RGBA"]).astype(np.float32) \
            if ml else None
        mm = (img[:, :, :3].reshape(-1, 3).mean(0) / 255.0) if img is not None else None
        # AC multilayer comes in two styles: a flat near-white base texture with the colour in the four
        # mask-blended detail layers (e.g. Skyline), or a real colour map as the base texture with the detail
        # layers only shading it (e.g. FM-style conversions). Use whichever carries the colour.
        white_base = mm is not None and mm.mean() > 0.47 and np.ptp(mm) < 0.06
        if ml and mask is not None:      # the material's average layer mix, to normalise the shading
            wm = mask.reshape(-1, 4).astype(np.float64).mean(0)
            mix_avg = float((wm / max(wm.sum(), 1e-3)) @ cols.mean(1))
        else:
            mix_avg = float(cols.mean()) if ml else 1.0
        for s0 in range(0, len(idx), CHUNK):
            ii = idx[s0:s0 + CHUNK]
            u, v = U_f[ii], V_f[ii]
            if ml:
                if mask is not None:
                    w = sample(mask, u, v)
                    w /= np.maximum(w.sum(1, keepdims=True), 1e-3)
                    mix = w @ cols
                else:
                    mix = np.full((len(ii), 3), 0.5, np.float32)
                if img is None:
                    col = mix
                elif white_base:
                    col = mix * (sample(img, u, v)[:, :3] / max(float(mm.mean()), 0.05))
                else:
                    col = sample(img, u, v)[:, :3] * (mix.mean(1, keepdims=True) / max(mix_avg, 0.05))
            elif img is not None:
                col = sample(img, u, v)[:, :3]
            else:
                continue
            col = np.clip(col, 0, 1)
            color_f[ii] = col
            layer_f[ii] = classify_colour(col, RI)
            if kind == "near":
                near_f[ii] = True
    report = []
    hit = M_f >= 0
    if hit.any():
        cnt = np.bincount(M_f[hit], minlength=len(sources))
        sums = np.stack([np.bincount(M_f[hit], weights=color_f[hit, c_], minlength=len(sources)) for c_ in range(3)], 1)
        for mid_, (fp_, mn_, kd_) in enumerate(sources):
            if cnt[mid_]:
                report.append((int(cnt[mid_]), mn_, kd_, sums[mid_] / cnt[mid_]))
    report.sort(key=lambda r_: -r_[0])
    print("ground sources: " + "; ".join(f"{mn_} ({kd_}) {cnt / M.size * 100:.0f}% rgb "
                                        f"{(c_ * 255).round().astype(int).tolist()}" for cnt, mn_, kd_, c_ in report[:6]))
    for k in by_kn5.values():
        k.close()
    # AC's near-road ground is much darker than its distant terrain (in AC, GrassFX and its lighting hide that).
    # Match the near tiles' average brightness to the open distant terrain's.
    lum = lambda c: c @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    far_open = ~near_px & (layer == RI["dry_grass"])
    if near_px.any() and far_open.any():
        gain = float(np.clip(lum(color[far_open]).mean() / max(lum(color[near_px]).mean(), 1e-3), 1.0, 1.8))
        if a.near_gain:
            gain = a.near_gain
        color[near_px] = np.clip(color[near_px] * gain, 0, 1)
        print(f"near-road ground brightened x{gain:.2f} to match the open distant terrain")
    # under/next to the road meshes the colour came from AC's hidden (sunk) ground: take it from the
    # surrounding visible ground instead, so the road verge blends in
    hidden = no_cover | road
    for ch in range(3):
        color[:, :, ch] = np.where(hidden, pyramid_fill(np.where(hidden, np.nan, color[:, :, ch])), color[:, :, ch])
    layer[no_cover | under_water] = RI["verge"]
    layer[road] = RI["verge"]
    print(f"layers + colours in {time.time() - ts:.0f}s: " +
          ", ".join(f"{LAYERS[i]} {np.mean(layer == i) * 100:.1f}%" for i in range(len(LAYERS))))

    # ---- 4. write .ter (v9): u8 version, u32 size, u16 heights[y][x], u8 layers[y][x], materials
    zmin = float(H.min()) - 1.0
    zmax = float(H.max()) + 1.0
    maxh = zmax - zmin
    q = np.clip(np.rint((H - zmin) / maxh * 65535), 0, 65535).astype("<u2")
    with open(level / "theTerrain.ter", "wb") as f:
        f.write(bytes([9])); f.write(np.uint32(n).tobytes())
        f.write(q.tobytes()); f.write(layer.tobytes())
        f.write(np.uint32(len(LAYERS)).tobytes())
        for name in LAYERS:
            b = name.encode(); f.write(bytes([len(b)])); f.write(b)
    print(f"wrote theTerrain.ter ({(level / 'theTerrain.ter').stat().st_size / 1e6:.0f} MB), "
          f"height {zmin:.1f}..{zmax:.1f} m, step {maxh / 65535 * 100:.2f} cm")

    # ---- 5. terrain textures + materials
    tdir = level / "art" / "terrains"
    tdir.mkdir(parents=True, exist_ok=True)
    base = Image.fromarray((np.clip(color, 0, 1)[::-1] * 255).astype(np.uint8), "RGB")   # image row 0 = north
    if base.size != (BASE_TEX, BASE_TEX):
        base = base.resize((BASE_TEX, BASE_TEX), Image.BILINEAR)
    base.save(tdir / "t_terrain_base_b.color.png")
    flat = {"t_terrain_base_nm.normal.png": (128, 128, 255), "t_terrain_base_ao.data.png": (255, 255, 255),
            "t_terrain_base_r.data.png": (230, 230, 230), "t_terrain_base_h.data.png": (128, 128, 128)}
    # every base texture must match the texture set's declared baseTexSize exactly, or BeamNG rejects it
    # (and the close-up shading falls back to near-black)
    for fn, c in flat.items():
        Image.new("RGB", (BASE_TEX, BASE_TEX), c).save(tdir / fn, optimize=True)
    rel = f"/levels/{lvl}/art/terrains/"
    world = n * sq

    import zipfile as _zf
    zpath = donor_mod.levels_dir(a.beamng) / f"{DONOR_LEVEL}.zip"
    z = _zf.ZipFile(zpath)
    tset = f"{lvl}TerrainMaterialTextureSet"
    tmats = {tset: {"name": tset, "class": "TerrainMaterialTextureSet",
                    "baseTexSize": [BASE_TEX, BASE_TEX], "detailTexSize": [1024, 1024], "macroTexSize": [1024, 1024]}}
    base_names = {"baseColorBaseTex": "t_terrain_base_b.color.png", "normalBaseTex": "t_terrain_base_nm.normal.png",
                  "aoBaseTex": "t_terrain_base_ao.data.png", "roughnessBaseTex": "t_terrain_base_r.data.png",
                  "heightBaseTex": "t_terrain_base_h.data.png"}

    def adapt(v, internal):
        v = dict(v); v.pop("persistentId", None); v.pop("name", None)
        v["internalName"] = internal
        for kk in list(v):
            if kk.endswith("BaseTex"):
                v[kk] = rel + base_names.get(kk, "t_terrain_base_h.data.png")
            if kk.endswith("BaseTexSize"):
                v[kk] = world
        for kk in ("aoDetailStrength", "aoMacroStrength"):
            cur = v.get(kk, [1, 0] if kk == "aoDetailStrength" else [1, 1])
            v[kk] = [round(x * a.ao, 3) for x in cur]
        return v

    for name in LAYERS[:-1]:
        tmats[f"{name}_{lvl}"] = adapt(pal["terrain_defs"][name], name)
    # the verge: the palette's dirt under its own name, which no GroundCover type references
    tmats[f"kn5_verge_{lvl}"] = adapt(pal["terrain_defs"][pal["terrain"]["dirt"]], "kn5_verge")
    (tdir / "main.materials.json").write_text(json.dumps(tmats, indent=2))

    # ---- 6. GroundCover from the donor, limited to our layers (never the verge)
    mg_dir = level / "main" / "MissionGroup"
    gc_objs, gc_mats, gc_shapes = [], set(), set()
    if not a.no_groundcover:
        for nme in z.namelist():
            if not nme.endswith("items.level.json"):
                continue
            for line in z.read(nme).decode(errors="replace").splitlines():
                if '"GroundCover"' not in line:
                    continue
                o = json.loads(line)
                types = [t_ for t_ in o.get("Types", []) if t_.get("layer") in LAYERS[:-1]]
                if not types:
                    continue
                for t_ in types:
                    if t_.get("shapeFilename"):
                        sf = t_["shapeFilename"]
                        gc_shapes.add(sf if sf.startswith("/") else "/" + sf)
                o["Types"] = types
                o.pop("persistentId", None)
                o["__parent"] = "groundcover"
                o["position"] = [0, 0, 0]
                gc_objs.append(o); gc_mats.add(o.get("material"))
        donor_gc_mats = {}
        for nme in z.namelist():
            if nme.endswith(".materials.json"):
                try:
                    d = json.loads(z.read(nme))
                except ValueError:
                    continue
                for kk, v in d.items():
                    if isinstance(v, dict) and (v.get("mapTo") or kk) in gc_mats:
                        v = dict(v); v.pop("persistentId", None); donor_gc_mats[v.get("mapTo") or kk] = v
        if gc_shapes:      # shape-based groundcover needs its shapes' materials too
            from .forest import donor_materials
            donor_gc_mats.update(donor_materials(z, sorted(gc_shapes)))
        (level / "art" / "forest").mkdir(parents=True, exist_ok=True)
        (level / "art" / "forest" / "donor_groundcover.materials.json").write_text(json.dumps(donor_gc_mats, indent=2))
        (mg_dir / "groundcover").mkdir(exist_ok=True)
        (mg_dir / "groundcover" / "items.level.json").write_text("".join(json.dumps(o) + "\n" for o in gc_objs))
        print(f"groundcover: {len(gc_objs)} layers ({', '.join(o['name'] for o in gc_objs)}), "
              f"{len(donor_gc_mats)} materials")

    # ---- 6b. skirt shapes + material (a dirt-like surface of the track itself)
    mats_all = {}
    for mf in level.rglob("*.materials.json"):
        if "skirts" in mf.parts or "terrains" in mf.parts:
            continue
        try:
            mats_all.update(json.loads(mf.read_text()))
        except ValueError:
            pass

    def skirt_rank(item):
        k_, v = item
        if not isinstance(v, dict) or not v.get("Stages") or not v["Stages"][0].get("baseColorMap"):
            return 9
        name = (v.get("ac_material") or k_).upper()
        if "DIRT" in name or "SOIL" in name or "EARTH" in name:
            return 0
        gt = v.get("groundType")
        return {"DIRT": 1, "GRAVEL": 2, "SAND": 3, "GRASS": 4}.get(gt, 5 if v.get("kn5_ground") else 9)
    ranked = sorted(mats_all.items(), key=skirt_rank)
    skirt_src = ranked[0][0] if ranked and skirt_rank(ranked[0]) < 9 else None
    skirt_mat = f"{lvl}_kn5_skirt"
    sm = dict(mats_all.get(skirt_src, {"Stages": [{"baseColorFactor": [0.32, 0.29, 0.24, 1]}, {}, {}, {}]}))
    sm.update({"name": skirt_mat, "mapTo": skirt_mat, "doubleSided": True})
    for drop in ("groundType", "kn5_ground", "alphaTest", "translucent"):
        sm.pop(drop, None)
    (level / "art" / "shapes" / "skirts").mkdir(parents=True, exist_ok=True)
    (level / "art" / "shapes" / "skirts" / "skirts.materials.json").write_text(json.dumps({skirt_mat: sm}, indent=2))
    skirt_items = write_skirts(level, lvl, skirt_quads, material=skirt_mat)
    (mg_dir / "road_skirts").mkdir(parents=True, exist_ok=True)
    (mg_dir / "road_skirts" / "items.level.json").write_text("".join(json.dumps(o) + "\n" for o in skirt_items))
    print(f"skirt shapes: {len(skirt_items)} (material from {skirt_src})")

    # ---- 7. TerrainBlock (+ group) in the scene
    mgf = mg_dir / "items.level.json"
    lines = [l for l in mgf.read_text().splitlines()
             if l.strip() and '"TerrainBlock"' not in l and '"name": "groundcover"' not in l
             and '"name": "road_skirts"' not in l]
    lines.append(json.dumps({"name": "road_skirts", "class": "SimGroup", "__parent": "MissionGroup"}))
    lines.append(json.dumps({"name": "theTerrain", "class": "TerrainBlock", "__parent": "MissionGroup",
                             "position": [x0, y0, zmin], "squareSize": sq, "maxHeight": maxh,
                             "baseTexSize": 2048, "materialTextureSet": tset,
                             "terrainFile": f"/levels/{lvl}/theTerrain.ter"}))
    if gc_objs:
        lines.append(json.dumps({"name": "groundcover", "class": "SimGroup", "__parent": "MissionGroup"}))
    mgf.write_text("\n".join(lines) + "\n")

    # ---- 8. re-snap forest items onto the terrain surface
    def terrain_h(x, y):
        gx = np.clip((x - x0) / sq, 0, n - 1.001); gy = np.clip((y - y0) / sq, 0, n - 1.001)
        ix, iy = gx.astype(int), gy.astype(int); fx, fy = gx - ix, gy - iy
        Hq = q.astype(np.float64) / 65535 * maxh + zmin
        return (Hq[iy, ix] * (1 - fx) * (1 - fy) + Hq[iy, ix + 1] * fx * (1 - fy) +
                Hq[iy + 1, ix] * (1 - fx) * fy + Hq[iy + 1, ix + 1] * fx * fy)
    sink = {"grassclump_large": 0.02}
    moved = 0
    for ff in (level / "forest").glob("*.forest4.json"):
        rows = [json.loads(l) for l in ff.read_text().splitlines() if l.strip()]
        if not rows:
            continue
        P = np.array([r["pos"] for r in rows])
        inside = (P[:, 0] > x0) & (P[:, 0] < x0 + (n - 1) * sq) & (P[:, 1] > y0) & (P[:, 1] < y0 + (n - 1) * sq)
        hz = terrain_h(P[:, 0], P[:, 1])
        s_ = sink.get(ff.stem.replace(".forest4", ""), 0.1 if "shrub" in ff.name else 0.25)
        for r, ins, h in zip(rows, inside, hz):
            if ins:
                r["pos"][2] = round(float(h - s_), 3); moved += 1
        ff.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"re-snapped {moved:,} forest items to the terrain")

    port["terrain"].update({"zmin": zmin, "maxHeight": maxh})
    (level / "kn5_port.json").write_text(json.dumps(port, indent=2))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
