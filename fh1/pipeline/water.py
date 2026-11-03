"""FH1's lakes and rivers as BeamNG River objects, which bring real water: buoyancy, depth colour, ripples and a splash.
build3.py draws the lake_anim meshes as 'wat' shapes. This reads them back, follows each body of water's centre line, and replaces the shapes
with Rivers whose nodes carry FH1's surface height, width and depth. Ripples are FH1's lake normal maps.
Run after sky.py, which makes the reflection cubemap: python water.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, math, os, re, sys
from pathlib import Path
import numpy as np
from numba import njit
from scipy import ndimage as ni
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
import dds, mission, tex
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL; rel = f'/levels/{LVL}'
GRID = 3.0          # metres per raster cell
MIN_AREA = 150.0    # m2: smaller pieces of water are dropped
SPACING = 40.0      # most metres between River nodes
MARGIN = 4.0        # metres added to the width, so the ribbon reaches under the banks
LAKE_NORMALS = (0x1295, 0x1296)
COLOUR = (18, 41, 51)       # build3.py's water colour, 0.07, 0.16, 0.2


@njit(cache=True)
def raster(H, T, top):
    """Highest (top) or lowest triangle height over each cell. T: triangles in cell units."""
    ny, nx = H.shape
    for t in range(T.shape[0]):
        ax, ay, az = T[t, 0]; bx, by, bz = T[t, 1]; cx, cy, cz = T[t, 2]
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        for j in range(max(int(math.floor(min(ay, by, cy))), 0), min(int(math.ceil(max(ay, by, cy))) + 1, ny)):
            for i in range(max(int(math.floor(min(ax, bx, cx))), 0), min(int(math.ceil(max(ax, bx, cx))) + 1, nx)):
                w0 = ((by - cy) * (i - cx) + (cx - bx) * (j - cy)) / d
                w1 = ((cy - ay) * (i - cx) + (ax - cx) * (j - cy)) / d
                w2 = 1 - w0 - w1
                if w0 < -.01 or w1 < -.01 or w2 < -.01:
                    continue
                z = w0 * az + w1 * bz + w2 * cz
                if H[j, i] != H[j, i] or (z > H[j, i]) == top:
                    H[j, i] = z


def triangles(o, finest):
    """World-space triangles of a cell shape. finest: only the highest detail, as LOD copies would double up."""
    text = (level / 'art' / 'shapes' / 'fh1' / Path(o['shapeName']).name).read_text()
    use = None
    if finest:
        nodes = re.findall(r'<node id="o\d+_a(\d+)"[^>]*>.*?url="#([^"]+)"', text)
        if nodes:
            top = max(int(a) for a, _ in nodes); use = {g for a, g in nodes if int(a) == top}
    R = np.array(o.get('rotationMatrix', np.eye(3).ravel())).reshape(3, 3); q = np.array(o['position']); out = []
    for geo in text.split('<geometry ')[1:]:
        gid = re.match(r'id="([^"]+)"', geo).group(1)
        if use is not None and gid not in use:
            continue
        V = np.array(re.search(r'-p-a"[^>]*>([^<]*)<', geo).group(1).split(), 'f8').reshape(-1, 3) @ R + q
        for inputs, p in re.findall(r'<triangles[^>]*>(.*?)<p>([^<]*)</p>', geo, re.S):
            step = max(map(int, re.findall(r'offset="(\d+)"', inputs))) + 1
            out.append(V[np.array(p.split(), 'i8')[::step].reshape(-1, 3)])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def centre_line(comp, D):
    """Cells along the middle of one piece of water, end to end: the two cells farthest apart through the water,
    joined by the path that keeps furthest from the banks"""
    ys, xs = np.nonzero(comp); n = len(ys); at = -np.ones(comp.shape, np.int64); at[ys, xs] = np.arange(n)
    r, c, w, wc = [], [], [], []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        y2, x2 = ys + dy, xs + dx; ok = (y2 < comp.shape[0]) & (x2 >= 0) & (x2 < comp.shape[1])
        ok[ok] = comp[y2[ok], x2[ok]]; a_ = np.arange(n)[ok]; b_ = at[y2[ok], x2[ok]]; L = math.hypot(dy, dx)
        r.append(a_); c.append(b_); w.append(np.full(len(a_), L)); wc.append(L / (np.minimum(D[ys[a_], xs[a_]], D[ys[b_], xs[b_]]) + .5) ** 2)
    r, c = np.concatenate(r), np.concatenate(c)
    G = coo_matrix((np.concatenate(w), (r, c)), (n, n)).tocsr(); Gc = coo_matrix((np.concatenate(wc), (r, c)), (n, n)).tocsr()
    a = int(np.argmax(dijkstra(G, directed=False, indices=0)))
    da = dijkstra(G, directed=False, indices=a); b = int(np.argmax(np.where(np.isfinite(da), da, -1)))
    _, pred = dijkstra(Gc, directed=False, indices=a, return_predecessors=True)
    path = [b]
    while path[-1] != a and pred[path[-1]] >= 0:
        path.append(int(pred[path[-1]]))
    return np.c_[ys[path], xs[path]]


def simplify(P, tol):
    """Douglas-Peucker on rows of P, judged by distance in the first two columns"""
    keep = np.zeros(len(P), bool); keep[[0, -1]] = True; stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i, :2], P[j, :2]; d = b - a; L = np.hypot(*d)
        q = P[i + 1:j, :2] - a
        e = np.abs(q[:, 0] * d[1] - q[:, 1] * d[0]) / L if L > 1e-9 else np.hypot(q[:, 0], q[:, 1])
        k = int(np.argmax(e))
        if e[k] > tol or np.hypot(*(P[j, :2] - P[i, :2])) > SPACING:
            keep[i + 1 + k] = True; stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return P[keep]


def unfold(P):
    """Nodes that can't fold over themselves. The centre line steps cell by cell, and a ribbon wider than twice its turn radius folds back:
    30% of nodes did, which showed as missing water and flat pale slabs across Clear Springs' reservoir. So the line is smoothed in proportion
    to the width, simplified, then each node is narrowed to fit the turn it sits on. Rows: x, y, z, width"""
    if len(P) > 4:
        sg = max(2.0, float(np.median(P[:, 3])) / GRID / 4)
        P = P.copy(); P[:, :2] = ni.gaussian_filter1d(P[:, :2], sg, axis=0, mode='nearest')
    P = simplify(P, 2.0)
    if len(P) > 2:
        d = np.diff(P[:, :2], axis=0); h = np.arctan2(d[:, 1], d[:, 0]); turn = np.abs((np.diff(h) + np.pi) % (2 * np.pi) - np.pi)
        seg = np.hypot(d[:, 0], d[:, 1]); R = (seg[:-1] + seg[1:]) / 2 / np.maximum(turn, 1e-6)
        P[1:-1, 3] = np.minimum(P[1:-1, 3], np.maximum(1.8 * R, 2 * MARGIN))
    return P


items_path = level / 'main' / 'MissionGroup' / 'fh1' / 'items.level.json'
items = [json.loads(l) for l in items_path.read_text().splitlines() if l.strip()]
wdir = level / 'main' / 'MissionGroup' / 'fh1_water'; wdir.mkdir(parents=True, exist_ok=True); saved = wdir / 'meshwater.json'
wat = [o for o in items if re.search(r'_watf?\.dae$', o.get('shapeName', ''))]
if wat:
    saved.write_text(json.dumps(wat))     # the TSStatics this replaces, kept so a rerun can read them again
elif saved.exists():
    wat = json.loads(saved.read_text())
else:
    print('no water shapes: run build3.py first, or this level has no water')      # Red Rock has none, and the build carries on
    sys.exit(0)
W = np.concatenate([triangles(o, True) for o in wat])
lo = W[:, :, :2].reshape(-1, 2).min(0) - 4 * GRID; hi = W[:, :, :2].reshape(-1, 2).max(0) + 4 * GRID
shape = tuple((np.ceil((hi - lo) / GRID).astype(int) + 1)[::-1])
cells = lambda T: np.dstack([(T[..., 0] - lo[0]) / GRID, (T[..., 1] - lo[1]) / GRID, T[..., 2]])
Hw = np.full(shape, np.nan); raster(Hw, cells(W), True)
# riverbed: the lowest ground under the water, so bridges over it don't count
Hg = np.full(shape, np.nan)
for o in items:
    if re.search(r'_colf?\.dae$', o.get('shapeName', '')):
        q = np.array(o['position'][:2])
        if (q > lo - 1200).all() and (q < hi + 1200).all():
            T = triangles(o, False)
            if len(T):
                T = T[(T[:, :, :2].max(1) >= lo).all(1) & (T[:, :, :2].min(1) <= hi).all(1)]
                raster(Hg, cells(T), False)
mask = np.isfinite(Hw); depth = np.clip(np.nan_to_num(Hw - Hg, nan=2.0), .5, 12.0)
print('water cells', int(mask.sum()), 'area km2', round(mask.sum() * GRID * GRID / 1e6, 2))

ripple = []
for tid in LAKE_NORMALS:     # River ripples want an RGB normal map, as core/art/water/ripple_nm.normal.dds is
    p_ = level / 'art' / 'textures' / f'w{tid:X}.normal.dds'
    rg = tex.normal(tid)[0].astype('f8') / 127.5 - 1; z = np.sqrt(np.clip(1 - (rg ** 2).sum(2), 0, 1))
    dds.save(p_, np.clip(np.dstack([rg, z]) * 127.5 + 127.5, 0, 255).astype('u1')); ripple.append(f'{rel}/art/textures/{p_.name}')

rivers = []; lab, n = ni.label(mask, np.ones((3, 3)))
for k in range(1, n + 1):
    comp = lab == k
    if comp.sum() * GRID * GRID < MIN_AREA:
        continue
    left = comp.copy()
    while left.sum() * GRID * GRID >= MIN_AREA:       # the main stem first, then branches the stem's ribbon misses
        sub, m_ = ni.label(left, np.ones((3, 3))); big = np.argmax(np.bincount(sub.ravel())[1:]) + 1; part = sub == big
        if part.sum() * GRID * GRID < MIN_AREA:
            break
        D = ni.distance_transform_edt(part) * GRID
        path = centre_line(part, D)
        width = ni.median_filter(np.array([D[max(y - 2, 0):y + 3, max(x - 2, 0):x + 3].max() for y, x in path]) * 2 + MARGIN, 9, mode='nearest')
        z = ni.median_filter(Hw[path[:, 0], path[:, 1]], 5, mode='nearest')
        P = np.c_[lo[0] + path[:, 1] * GRID, lo[1] + path[:, 0] * GRID, z, width]
        # a drop of metres between neighbouring cells is a fall, or a pool beside lower water: the ribbon stops there
        for P_ in np.split(P, np.flatnonzero(np.abs(np.diff(z)) > 4) + 1):
            if len(P_) < 3:
                continue
            nodes = []
            for x, y, zz, wd in unfold(P_):
                j, i = int(round((y - lo[1]) / GRID)), int(round((x - lo[0]) / GRID)); r_ = max(int(wd / 2 / GRID), 1)
                nodes.append([round(x, 2), round(y, 2), round(zz, 2), round(wd, 1), round(float(np.median(depth[max(j - r_, 0):j + r_ + 1, max(i - r_, 0):i + r_ + 1])), 1), 0, 0, 1])
            rivers.append(nodes)
        yy, xx = np.nonzero(part); covered = np.zeros_like(left)
        for (y, x), wd in zip(path, width):
            r_ = int(wd / 2 / GRID) + 1; covered[max(y - r_, 0):y + r_ + 1, max(x - r_, 0):x + r_ + 1] = True
        left &= ~covered
        left = ni.binary_opening(left, iterations=1)      # slivers along the ribbon's edge aren't branches
print('rivers', len(rivers), 'nodes', sum(len(r) for r in rivers))

RIVER = {'class': 'River', '__parent': 'fh1_water', 'annotation': 'WATER', 'baseColor': [*COLOUR, 255], 'cubemap': 'fh1_sky_cubemap', 'fullReflect': False,
         'rippleTex': ripple[0], 'foamTex': '/core/art/water/foam.dds', 'overallFoamOpacity': 0, 'depthGradientTex': '/core/art/water/river_depthcolor_ramp.png',
         'depthGradientMax': 10, 'clarity': .3, 'fresnelBias': .1, 'fresnelPower': 4, 'flowMagnitudePhysics': 0, 'overallWaveMagnitude': .2,
         'Ripples (texture animation)': [{'rippleDir': [0, 1], 'rippleMagnitude': .8, 'rippleSpeed': .06, 'rippleTexScale': [6, 6]},
                                         {'rippleDir': [.7, .7], 'rippleMagnitude': .5, 'rippleSpeed': .03, 'rippleTexScale': [16, 16]},
                                         {'rippleDir': [-.5, .86], 'rippleMagnitude': .3, 'rippleSpeed': .01, 'rippleTexScale': [40, 40]}],
         'Waves (vertex undulation)': [{'waveDir': [0, 1], 'waveMagnitude': .1, 'waveSpeed': 1}, {'waveDir': [.7, .7], 'waveMagnitude': .1, 'waveSpeed': 1},
                                       {'waveDir': [.5, .86], 'waveMagnitude': .1, 'waveSpeed': 1}]}
items_path.write_text(''.join(json.dumps(o) + '\n' for o in items if o not in wat))
(wdir / 'items.level.json').write_text(''.join(json.dumps(dict(RIVER, name=f'fh1_river_{i}', position=r[0][:3], nodes=r)) + '\n' for i, r in enumerate(rivers)))
mission.add_groups(level / 'main' / 'MissionGroup' / 'items.level.json', 'fh1_water')
print('removed', len(wat), 'water shapes')
