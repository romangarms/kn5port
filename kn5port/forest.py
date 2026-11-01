"""Replace AC's card trees with BeamNG forest items (West Coast USA's redwoods, pines, oaks, shrubs, grass).

    python kn5port (forest step) "<AC track folder>" "<mod folder>\\levels\\<level>" ^
        --beamng "C:\\Program Files (x86)\\Steam\\steamapps\\common\\BeamNG.drive"

Run it after kn5port convert (it reads the origin offset from kn5_port.json).

How trees are found:
  * conifers  : AC's tall crossed cards (H9_TREES_CONIFER*) -> one tree per card pair; species from the
                nearest trunk mesh (redwood bark -> redwoods, fir bark -> pines/redwoods by height)
  * broadleaf : oak/madrone trunk meshes -> one tree each, height from the crown cards around it;
                crown cards with no trunk are filled on a grid
  * shrubs    : H9_TREES_CLUSTER foliage blobs, filled on a grid
  * grass     : SKY_GROUNDCOVER / *_SHADE cards, filled on a grid (BeamNG's GroundCover needs a terrain,
                so grass is placed as forest items instead)
Each tree is scaled so its height matches the AC tree. Output:
  forest/<item>.forest4.json, art/forest/managedItemData.json, and a Forest object in the scene.
"""
import argparse
import json
import math
import re
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import numpy as np

from .kn5 import KN5, KN5Error
from .profile import Profile

# BeamNG item -> reference height (m), measured from West Coast USA's models
PROFILE = Profile()      # replaced in main()
SINK = {"grass": 0.02, "shrub": 0.1}   # metres pushed into the ground (default 0.25)
PHYS_RE = re.compile(r"^(\d+)([A-Z]+)")
TALL = 6.0                # elements at least this tall are whole trees (cards or 3D models)


def is_veg_material(m):
    if PROFILE.rx("trunk_re").search(m.name):
        return True
    if not PROFILE.rx("vegetation_re").search(m.name) and m.shader != "ksTree":
        return False
    return m.shader == "ksTree" or m.alpha_tested or m.alpha_blend > 0 or "AT" in m.shader.replace("MultiMap", "")


def ac_to_bng(p):
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1)


def components(idx, n):
    """Connected components of a triangle mesh (label propagation, vectorised)."""
    lab = np.arange(n)
    t = idx.reshape(-1, 3)
    while True:
        m = np.minimum(np.minimum(lab[t[:, 0]], lab[t[:, 1]]), lab[t[:, 2]])
        new = lab.copy()
        for c in range(3):
            np.minimum.at(new, t[:, c], m)
        new = new[new]
        if np.array_equal(new, lab):
            return lab
        lab = new


def cluster(xy, r):
    """Single-linkage-ish clustering on a grid of cell size r (neighbouring occupied cells merge)."""
    cell = np.floor(xy / r).astype(np.int64)
    keys, inv = np.unique(cell, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    index = {tuple(k): i for i, k in enumerate(keys)}
    par = list(range(len(keys)))

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for i, (cx, cy) in enumerate(keys):
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            j = index.get((cx + dx, cy + dy))
            if j is not None:
                a, b = find(i), find(j)
                if a != b: par[b] = a
    root = np.array([find(i) for i in range(len(keys))])
    return root[inv]


def grid_fill(q, cell, min_count):
    """One instance per grid cell holding >= min_count cards: (x, y, base, top)."""
    c = np.floor(q[:, :2] / cell).astype(np.int64)
    keys, inv, counts = np.unique(c, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    out = []
    order = np.argsort(inv)
    starts = np.r_[0, np.cumsum(counts)[:-1]]
    for k, (s, n) in enumerate(zip(starts, counts)):
        if n < min_count:
            continue
        g = q[order[s:s + n]]
        out.append((g[:, 0].mean(), g[:, 1].mean(), g[:, 2].min(), g[:, 3].max()))
    return out


PAVED = {"ASPHALT", "GRAVEL", "DIRT", "SAND", "KERB", "ASPHALT_WET", "DIRT_DUSTY"}


def sample_tris(T, step=1.0):
    """Points covering triangles (N,3,3) at roughly `step` spacing, xy only."""
    out = [T[:, i, :2] for i in range(3)]
    for a_, b_ in ((0, 1), (1, 2), (2, 0)):
        L = np.linalg.norm(T[:, b_, :2] - T[:, a_, :2], axis=1)
        k = np.maximum(1, np.ceil(L / step)).astype(int)
        for m in range(1, int(k.max())):
            s = k > m
            out.append(T[s, a_, :2] + (T[s, b_, :2] - T[s, a_, :2]) * (m / k[s])[:, None])
    # interior lattice for larger triangles
    e1, e2 = T[:, 1, :2] - T[:, 0, :2], T[:, 2, :2] - T[:, 0, :2]
    area = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])     # 2D cross (np.cross dropped 2D in NumPy 2)
    big = area > step * step
    if big.any():
        Tb = T[big]
        k = np.ceil(np.sqrt(area[big]) / step * 1.5).astype(int)
        for kk in np.unique(k):
            s = k == kk
            g = np.array([(i, j) for i in range(kk + 1) for j in range(kk + 1 - i)], float) / kk
            w = np.stack([1 - g.sum(1), g[:, 0], g[:, 1]], 1)           # barycentric lattice
            out.append(np.einsum("pk,nkd->npd", w, Tb[s][:, :, :2]).reshape(-1, 2))
    return np.concatenate(out)


def load_ground(level):
    """Collision triangles of the converted level, as (N,3,3) BeamNG coordinates."""
    import xml.etree.ElementTree as ET
    NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    mats = {}
    for mf in level.rglob("*.materials.json"):
        mats.update(json.loads(mf.read_text()))
    rel = "/levels/" + level.name + "/"
    tris, asphalt = [], []
    for f in (level / "main").rglob("items.level.json"):
        for line in f.read_text().splitlines():
            o = json.loads(line) if line.strip() else {}
            if o.get("class") != "TSStatic" or o.get("collisionType", "None") == "None":
                continue
            r = ET.parse(level / o["shapeName"][len(rel):]).getroot()
            for g in r.iterfind(".//c:geometry", NS):
                tri = g.find(".//c:triangles", NS)
                fa = g.find(f".//c:source[@id='{g.get('id')}-p']/c:float_array", NS)
                p = np.array(fa.text.split(), np.float64).reshape(-1, 3) + o["position"]
                gt = mats.get(tri.get("material"), {}).get("groundType")
                if gt in PAVED:
                    # never plant on roads, shoulders or lots: sample the triangles densely (vertices, edges
                    # every metre, interior on a ~1 m lattice) so their whole area is covered
                    idx = np.array(tri.find("c:p", NS).text.split(), np.int64).reshape(-1, 3)
                    asphalt.append(sample_tris(p[idx]))
                    continue
                idx = np.array(tri.find("c:p", NS).text.split(), np.int64).reshape(-1, 3)
                tris.append(p[idx])
    return (np.concatenate(tris) if tris else np.zeros((0, 3, 3)),
            np.concatenate(asphalt) if asphalt else np.zeros((0, 2)))


class TerrainHeight:
    """Bilinear height lookup on theTerrain.ter written by kn5port (terrain step)."""
    def __init__(self, level):
        port = json.loads((level / "kn5_port.json").read_text())
        t = port.get("terrain") or {}
        ter = level / "theTerrain.ter"
        self.ok = ter.exists() and "maxHeight" in t
        if not self.ok:
            return
        b = ter.read_bytes()
        self.n = int.from_bytes(b[1:5], "little")
        self.h = (np.frombuffer(b, "<u2", self.n * self.n, 5).reshape(self.n, self.n).astype(np.float32)
                  / 65535 * t["maxHeight"] + t["zmin"])
        self.x0, self.y0, self.sq = t["rect"][0], t["rect"][1], t["square"]

    def heights(self, x, y):
        """Vectorised: NaN outside the terrain."""
        out = np.full(len(x), np.nan)
        if not self.ok:
            return out
        gx, gy = (x - self.x0) / self.sq, (y - self.y0) / self.sq
        ok = (gx >= 0) & (gy >= 0) & (gx < self.n - 1) & (gy < self.n - 1)
        ix, iy = gx[ok].astype(int), gy[ok].astype(int); fx, fy = gx[ok] - ix, gy[ok] - iy
        h = self.h
        out[ok] = (h[iy, ix] * (1 - fx) * (1 - fy) + h[iy, ix + 1] * fx * (1 - fy) +
                   h[iy + 1, ix] * (1 - fx) * fy + h[iy + 1, ix + 1] * fx * fy)
        return out

    def height(self, x, y):
        if not self.ok:
            return None
        gx, gy = (x - self.x0) / self.sq, (y - self.y0) / self.sq
        if not (0 <= gx < self.n - 1 and 0 <= gy < self.n - 1):
            return None
        ix, iy = int(gx), int(gy); fx, fy = gx - ix, gy - iy
        h = self.h
        return float(h[iy, ix] * (1 - fx) * (1 - fy) + h[iy, ix + 1] * fx * (1 - fy) +
                     h[iy + 1, ix] * (1 - fx) * fy + h[iy + 1, ix + 1] * fx * fy)


class PointIndex:
    def __init__(self, pts, cell=4.0):
        self.cell = cell
        g = defaultdict(list)
        for i, c in enumerate(map(tuple, np.floor(pts / cell).astype(np.int64))):
            g[c].append(i)
        self.g = {k: pts[v] for k, v in g.items()}

    def within(self, x, y, r):
        cx, cy = int(math.floor(x / self.cell)), int(math.floor(y / self.cell))
        n = int(math.ceil(r / self.cell))
        for a in range(-n, n + 1):
            for b in range(-n, n + 1):
                arr = self.g.get((cx + a, cy + b))
                if arr is not None and (((arr[:, 0] - x) ** 2 + (arr[:, 1] - y) ** 2) < r * r).any():
                    return True
        return False


class GroundIndex:
    """Find the ground height under (x, y): small triangles in a 10 m grid, big ones in a 200 m grid."""
    def __init__(self, tris):
        self.t = tris
        mn, mx = tris[:, :, :2].min(1), tris[:, :, :2].max(1)
        big = (mx - mn).max(1) > 10
        self.grids = []
        for cell, sel in ((10.0, ~big), (200.0, big)):
            ids = np.flatnonzero(sel)
            g = defaultdict(list)
            a = np.floor(mn[ids] / cell).astype(np.int64); b = np.floor(mx[ids] / cell).astype(np.int64)
            for i, (ax, ay), (bx, by) in zip(ids, a, b):
                for x in range(ax, bx + 1):
                    for y in range(ay, by + 1):
                        g[(x, y)].append(i)
            self.grids.append((cell, {k: np.array(v) for k, v in g.items()}))

    def height(self, x, y, z_hint):
        cand = [g.get((int(math.floor(x / c)), int(math.floor(y / c)))) for c, g in self.grids]
        cand = [c for c in cand if c is not None]
        if not cand:
            return None
        ids = np.concatenate(cand)
        T = self.t[ids]
        A, B, C = T[:, 0], T[:, 1], T[:, 2]
        v0, v1 = B[:, :2] - A[:, :2], C[:, :2] - A[:, :2]
        d = v0[:, 0] * v1[:, 1] - v0[:, 1] * v1[:, 0]
        qx, qy = x - A[:, 0], y - A[:, 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (qx * v1[:, 1] - qy * v1[:, 0]) / d
            v = (v0[:, 0] * qy - v0[:, 1] * qx) / d
        ok = (np.abs(d) > 1e-9) & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
        if not ok.any():
            return None
        z = A[ok, 2] + u[ok] * (B[ok, 2] - A[ok, 2]) + v[ok] * (C[ok, 2] - A[ok, 2])
        return float(z[np.argmin(np.abs(z - z_hint))])


def donor_materials(z, shape_files):
    """Material definitions used by the donor shapes, with any relative texture paths made absolute."""
    import xml.etree.ElementTree as ET
    import posixpath
    NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    need = set()
    for sf in shape_files:
        root = ET.fromstring(z.read(sf.lstrip("/")))
        for m in root.iterfind(".//c:library_materials/c:material", NS):
            need.add(m.get("name") or m.get("id"))
        for im in root.iterfind(".//c:instance_material", NS):
            need.add(im.get("symbol"))
    need.discard(None)
    found = {}
    files = [n for n in z.namelist() if n.endswith(".materials.json")]
    # search the shapes' own folders first, then the rest of the level
    dirs = {posixpath.dirname(sf.lstrip("/")) for sf in shape_files}
    files.sort(key=lambda n: (not any(n.startswith(d) for d in dirs), n))
    for n in files:
        try:
            d = json.loads(z.read(n))
        except ValueError:
            continue
        base = "/" + posixpath.dirname(n)
        for key, v in d.items():
            if not isinstance(v, dict):
                continue
            name = v.get("mapTo") or v.get("name") or key
            if name not in need or name in found:
                continue
            v = json.loads(json.dumps(v))
            for st in v.get("Stages", []):
                for k2, val in list(st.items()):
                    if isinstance(val, str) and k2.endswith("Map") and val and not val.startswith("/"):
                        st[k2] = posixpath.normpath(posixpath.join(base, val)) if "/" not in val[:7] else "/" + val
            v.pop("persistentId", None)
            found[name] = v
    missing = need - set(found)
    if missing:
        print(f"  ! no donor material found for: {', '.join(sorted(missing))}")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track_dir")
    ap.add_argument("level_dir")
    ap.add_argument("--beamng", required=True, help="BeamNG.drive install folder, or its content\\levels folder")
    ap.add_argument("--kn5", nargs="*", default=None, help="kn5 files to take vegetation from (default: all)")
    ap.add_argument("--broad-cell", type=float, default=6.0, help="grid for trunkless broadleaf crowns (m)")
    ap.add_argument("--shrub-cell", type=float, default=4.0)
    ap.add_argument("--grass-cell", type=float, default=2.5)
    ap.add_argument("--no-grass", action="store_true")
    ap.add_argument("--no-shrubs", action="store_true")
    ap.add_argument("--profile", default=None, help="track profile JSON (see kn5port/profile.py)")
    ap.add_argument("--tree-density", type=float, default=None)
    ap.add_argument("--donor", default=None, help="stock BeamNG level whose vegetation to use (default: profile, "
                    "west_coast_usa)")
    a = ap.parse_args(argv)
    t0 = time.time()
    track, level = Path(a.track_dir), Path(a.level_dir)
    global PROFILE
    PROFILE = Profile.load(a.profile, track)
    if a.tree_density is not None:
        PROFILE.tree_density = a.tree_density
    DONOR = a.donor or PROFILE.donor_level
    from . import donor as donor_mod
    # West Coast USA keeps its hand-tuned mapping; every other level gets an analysed palette
    curated = DONOR == "west_coast_usa" and bool(PROFILE.tree_items)
    pal = None if curated else donor_mod.analyze(a.beamng, DONOR)
    if pal is not None:
        print(f"vegetation palette: {DONOR} ({donor_mod.summary(pal)})")
    ITEMS = PROFILE.item_heights
    CHOICES = {k: [tuple(o) for o in v] for k, v in PROFILE.tree_items.items()}
    SCALE_CLAMP = {k: tuple(v) for k, v in PROFILE.scale_clamp.items()}
    CLEARANCE = {sp: PROFILE.clearance.get(sp, PROFILE.clearance.get("tree", 1.5)) for sp in CHOICES}
    TREE_SP = [sp for sp in CHOICES if sp not in ("shrub", "grass")]
    port = json.loads((level / "kn5_port.json").read_text())
    off = np.array(port["offset_bng"])
    lo = np.array(port["road_lo"]) - port["crop_margin"]; hi = np.array(port["road_hi"]) + port["crop_margin"]

    files = [track / f"{n}.kn5" for n in a.kn5] if a.kn5 else sorted(track.glob("*.kn5"))
    elements = defaultdict(list)   # species -> (x, y, zmin, zmax) per card / connected part
    trunks = []                    # (species, x, y, zbase, ztop)
    for f in files:
        try:
            k = KN5(str(f))
        except KN5Error as e:
            print(f"  ! skipping {f.name}: {e}")
            continue
        with k:
            vmats = {m.name for m in k.materials if is_veg_material(m)}
            if not vmats:
                continue
            for m in k.meshes():
                if m.material.name not in vmats or m.lod_in > 0 or not len(m.indices) or PHYS_RE.match(m.name):
                    continue
                sp = PROFILE.species_of(m.material.name)
                p = ac_to_bng(m.positions.astype(np.float64)) - off
                if PROFILE.rx("trunk_re").search(m.material.name):
                    sp = sp if sp in TREE_SP else PROFILE.default_tree_species
                    lab = components(m.indices, len(p))
                    for l in np.unique(lab):
                        g = p[lab == l]
                        if len(g) < 30 or np.ptp(g[:, 2]) < 2.0:
                            continue   # branches / stubs
                        low = g[g[:, 2] <= g[:, 2].min() + 0.5]
                        trunks.append((sp, low[:, 0].mean(), low[:, 1].mean(), g[:, 2].min(), g[:, 2].max()))
                    continue
                if len(p) % 4 == 0 and len(m.indices) == len(p) // 4 * 6:
                    q = p.reshape(-1, 4, 3)          # one quad (card) per 4 vertices
                    c = q.mean(1)
                    rows = np.column_stack([c[:, 0], c[:, 1], q[:, :, 2].min(1), q[:, :, 2].max(1)])
                else:                                # 3D vegetation: one element per connected part
                    lab = components(m.indices, len(p))
                    order = np.argsort(lab, kind="stable"); ls = lab[order]; po = p[order]
                    st = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1]])
                    rows = np.column_stack([np.add.reduceat(po[:, 0], st) / np.diff(np.r_[st, len(ls)]),
                                            np.add.reduceat(po[:, 1], st) / np.diff(np.r_[st, len(ls)]),
                                            np.minimum.reduceat(po[:, 2], st), np.maximum.reduceat(po[:, 2], st)])
                elements[sp].append(rows)
    E = {sp: np.concatenate(v) for sp, v in elements.items()}
    print(f"read vegetation in {time.time() - t0:.1f}s: " +
          ", ".join(f"{sp} {len(v):,} elements" for sp, v in E.items()) + f", {len(trunks):,} trunk pieces")
    if not E and not trunks:
        print("no vegetation found; nothing to do")
        return

    # ground under each element: a whole tree (card or 3D model) stands on the ground, while the leaf cards of
    # a crown float above it, so height alone can't tell them apart
    ts = time.time()
    ground, asphalt = load_ground(level)
    gi = GroundIndex(ground) if len(ground) else None
    th = TerrainHeight(level)

    def ground_at(x, y, zhint):
        g = th.heights(x, y)
        if gi is not None:
            for i in np.flatnonzero(np.isnan(g)):
                v = gi.height(x[i], y[i], zhint[i])
                g[i] = np.nan if v is None else v
        return g

    grounded = {}
    for sp in TREE_SP:
        if sp in E:
            q = E[sp]
            tall = (q[:, 3] - q[:, 2]) >= TALL
            g = np.full(len(q), np.nan)
            g[tall] = ground_at(q[tall, 0], q[tall, 1], q[tall, 2])
            grounded[sp] = tall & (np.isnan(g) | (q[:, 2] - g < 1.5))
    print(f"ground lookup in {time.time() - ts:.0f}s: " + ", ".join(
        f"{sp} {int(v.sum()):,} whole-tree elements" for sp, v in grounded.items()))

    # dedupe trunk pieces belonging to the same tree
    tree_trunks = []
    if trunks:
        T = np.array([t[1:] for t in trunks]); sp_ = np.array([t[0] for t in trunks])
        lab = cluster(T[:, :2], 1.0)
        for l in np.unique(lab):
            sel = lab == l
            g = T[sel]; i = np.argmin(g[:, 2])
            tree_trunks.append((sp_[sel][i], g[i, 0], g[i, 1], g[:, 2].min(), g[:, 3].max()))
    print(f"trunks: {len(tree_trunks):,}")
    tidx = defaultdict(list)
    for i, t in enumerate(tree_trunks):
        tidx[(int(t[1] // 8), int(t[2] // 8))].append(i)

    def near_trunk(x, y, r):
        best, bd = None, r * r
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in tidx.get((int(x // 8) + dx, int(y // 8) + dy), ()):
                    t = tree_trunks[i]
                    d = (t[1] - x) ** 2 + (t[2] - y) ** 2
                    if d < bd:
                        best, bd = i, d
        return best

    instances = []   # (species, x, y, zbase, height)
    used_trunk = set()

    # whole trees: tall cards (crossed cards share a centre) and tall 3D parts
    for sp in TREE_SP:
        if sp not in E:
            continue
        q = E[sp][grounded[sp]]
        if not len(q):
            continue
        lab = cluster(q[:, :2], 0.75)
        order = np.argsort(lab); ls = lab[order]
        bounds = np.flatnonzero(np.r_[True, ls[1:] != ls[:-1], True])
        for s0, e0 in zip(bounds[:-1], bounds[1:]):
            g = q[order[s0:e0]]
            x, y, base, top = g[:, 0].mean(), g[:, 1].mean(), g[:, 2].min(), g[:, 3].max()
            ti = near_trunk(x, y, 4.0)
            species = sp
            if ti is not None:
                used_trunk.add(ti); base = min(base, tree_trunks[ti][3]); species = tree_trunks[ti][0]
            instances.append((species, x, y, base, top - base))

    n_whole = len(instances)
    # crowns made of small leaf cards: claimed by a trunk underneath, otherwise one tree per grid cell
    small = {sp: E[sp][~grounded[sp]] for sp in TREE_SP if sp in E}
    allsmall = np.concatenate([v for v in small.values()]) if small else np.zeros((0, 4))
    small_sp = np.concatenate([[sp] * len(v) for sp, v in small.items()]) if small else np.array([])
    bidx = defaultdict(list)
    for i, (x, y, _, _) in enumerate(allsmall):
        bidx[(int(x // 6), int(y // 6))].append(i)
    claimed = np.zeros(len(allsmall), bool)
    for ti, t in enumerate(tree_trunks):
        if ti in used_trunk:
            continue
        sp_t, x, y, zb, zt = t
        top = zt
        cx, cy = int(x // 6), int(y // 6)
        ids = [i for dx in (-1, 0, 1) for dy in (-1, 0, 1) for i in bidx.get((cx + dx, cy + dy), ())]
        if ids:
            ids = np.array(ids)
            d2 = (allsmall[ids, 0] - x) ** 2 + (allsmall[ids, 1] - y) ** 2
            ids = ids[d2 < 36]
            if len(ids):
                top = max(top, allsmall[ids, 3].max()); claimed[ids] = True
        if top == zt:
            top = zt + (zt - zb) * 0.3          # bare trunk mesh: crown is a separate (tall) card
        instances.append((sp_t, x, y, zb, top - zb))
    n_trunk = len(instances)
    stage = {"whole trees (tall cards / 3D)": n_whole, "trunks (+ crowns)": n_trunk - n_whole}
    for sp in small:
        sel = (small_sp == sp) & ~claimed
        for x, y, b, t in grid_fill(allsmall[sel], a.broad_cell, 3):
            instances.append((sp, x, y, b, t - b))
    stage["crown grid fill"] = len(instances) - n_trunk
    if not a.no_shrubs and "shrub" in E:
        for x, y, b, t in grid_fill(E["shrub"], a.shrub_cell, 4):
            instances.append(("shrub", x, y, b, max(t - b, 0.8)))
    if not a.no_grass and "grass" in E:
        for x, y, b, t in grid_fill(E["grass"], a.grass_cell, 1):
            instances.append(("grass", x, y, b, max(t - b, 0.4)))
    stage["shrubs + grass"] = len(instances) - sum(stage.values())
    print("instances by source: " + ", ".join(f"{k} {v:,}" for k, v in stage.items()))
    for sp in small:
        print(f"  small {sp} elements: {len(small[sp]):,} (claimed by trunks {int(claimed[small_sp == sp].sum()):,})")

    # optional thinning (performance)
    if PROFILE.tree_density < 1 or PROFILE.grass_density < 1:
        rng_t = np.random.default_rng(7)
        keep = [rng_t.random() < (PROFILE.grass_density if i[0] == "grass" else PROFILE.tree_density) for i in instances]
        instances = [i for i, k_ in zip(instances, keep) if k_]
        print(f"thinned to {len(instances):,} instances (tree_density {PROFILE.tree_density}, "
              f"grass_density {PROFILE.grass_density})")

    # crop to the converted area
    instances = [i for i in instances if lo[0] <= i[1] <= hi[0] and lo[1] <= i[2] <= hi[1]]

    # snap to the ground surface (AC sinks its cards well below ground on slopes)
    ts = time.time()
    if th.ok:
        print("  snapping to the terrain heightmap (plus remaining ground meshes)")
    road = PointIndex(asphalt)
    snapped, dropped, cleared = [], 0, 0
    for species, x, y, zb, h in instances:
        clear = CLEARANCE.get(species, PROFILE.clearance.get("tree", 1.5))
        if clear and road.within(x, y, clear):
            cleared += 1; continue
        z = gi.height(x, y, zb + 1.0) if gi else None
        zt = th.height(x, y)
        if zt is not None and (z is None or abs(zt - zb) < abs(z - zb)):
            z = zt
        if z is None:
            dropped += 1; continue
        if abs(z - zb) > 15:      # card bottom nowhere near any ground: likely a bad match, skip
            dropped += 1; continue
        snapped.append((species, x, y, z, h + (zb - z) * (species != "grass")))
    print(f"snapped {len(snapped):,} instances to ground in {time.time() - ts:.0f}s "
          f"({dropped:,} with no ground and {cleared:,} too close to asphalt dropped)")
    instances = snapped

    # choose items, write forest4 files
    by_item = defaultdict(list)
    rng = np.random.default_rng(1234)
    for species, x, y, zb, h in instances:
        if pal is None:
            opts = [o for o in CHOICES[species] if o[1] <= h < o[2]] or CHOICES[species]
            item = opts[int(rng.integers(len(opts)))][0]
            ref_h = ITEMS[item]
        else:
            got = donor_mod.choose(pal, species, h, rng)
            if got is None:
                continue
            item, ref_h = got
        lo_s, hi_s = SCALE_CLAMP.get(species, (0.5, 2.0))
        scale = float(np.clip(h / ref_h, lo_s, hi_s))
        yaw = float(rng.uniform(0, 2 * math.pi)); c, s = math.cos(yaw), math.sin(yaw)
        z = zb - SINK.get(species, 0.25)
        by_item[item].append({"ctxid": 0, "pos": [round(float(x), 3), round(float(y), 3), round(float(z), 3)],
                              "rotationMatrix": [round(c, 6), round(-s, 6), 0, round(s, 6), round(c, 6), 0, 0, 0, 1],
                              "scale": round(scale, 3), "type": item})
    fdir = level / "forest"
    fdir.mkdir(exist_ok=True)
    for old in fdir.glob("*.forest4.json"):
        old.unlink()
    for item, rows in by_item.items():
        (fdir / f"{item}.forest4.json").write_text("".join(json.dumps(r) + "\n" for r in rows))

    # item definitions copied from the donor level
    bp = Path(a.beamng)
    zpath = next((p for p in (bp / "content" / "levels" / f"{DONOR}.zip", bp / "levels" / f"{DONOR}.zip",
                              bp / f"{DONOR}.zip") if p.exists()), None)
    if zpath is None:
        raise SystemExit(f"can't find {DONOR}.zip under {bp} (pass the BeamNG.drive install folder)")
    defs = {}
    if pal is None:
        with zipfile.ZipFile(zpath) as z:
            donor = json.loads(z.read(f"levels/{DONOR}/art/forest/managedItemData.json"))
        for item in by_item:
            d = dict(donor[item])
            d.pop("persistentId", None)
            sf = d["shapeFile"]
            d["shapeFile"] = sf if sf.startswith("/") else "/" + sf
            defs[item] = d
    else:
        defs = {item: pal["vegetation"][item]["def"] for item in by_item}
    with zipfile.ZipFile(zpath) as z:
        donor_mats = donor_materials(z, [d["shapeFile"] for d in defs.values()])
    (level / "art" / "forest").mkdir(parents=True, exist_ok=True)
    (level / "art" / "forest" / "managedItemData.json").write_text(json.dumps(defs, indent=2))
    # BeamNG only loads materials from the current level, so the donor trees' materials are copied in.
    (level / "art" / "forest" / "donor_trees.materials.json").write_text(json.dumps(donor_mats, indent=2))
    print(f"copied {len(donor_mats)} tree materials from {DONOR}")

    # Forest object in the scene
    mg = level / "main" / "MissionGroup" / "items.level.json"
    lines = [l for l in mg.read_text().splitlines() if l.strip() and '"class": "Forest"' not in l]
    lines.append(json.dumps({"name": "theForest", "class": "Forest", "__parent": "MissionGroup", "position": [0, 0, 0]}))
    mg.write_text("\n".join(lines) + "\n")

    counts = defaultdict(int)
    for sp_, *_ in instances: counts[sp_] += 1
    print(f"trees from trunks: {n_trunk:,}; total instances: {len(instances):,} -> {dict(counts)}")
    print("per item: " + ", ".join(f"{k} {len(v):,}" for k, v in sorted(by_item.items())))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
