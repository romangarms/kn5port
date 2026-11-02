"""FH1's animated objects, starting with the metal windmills: the fan is cut out of the built shape and fh1Extras.lua spins it. Fans have no collision.
Run after build3.py, which wipes the level: python animated.py [fh1_colorado]. Safe to run twice. Needs no rebuild."""
import glob, json, math, os, re, struct, sys
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from daewrite import FARCUT, write_lod
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0)}[LVL]
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); S = np.diag([1., 1., -1.])
# The shape built for this mesh, the anim_proc set that places it, and the Fan bone from ANIM_FARM_WindmillMetal.pgeo in BeamNG axes.
# Spin speed isn't read from FH1's curves yet. Parts within FAN_RADIUS of the hub and FAN_DEPTH along its axis are the fan
WINDMILLS = {'metal': dict(shape='m6625', group='fx_windmillsmetal', hub=(-.507, -1.174, 9.931), axis=(0, 1, 0), radius=1.75, depth=.6, speed=2.2)}
SPEED_SPREAD = .2


def arrays(text, gid):
    def get(name, n):
        m = re.search(rf'<float_array id="{gid}-{name}-a"[^>]*>([^<]*)<', text)
        return np.array(m.group(1).split(), 'f8').reshape(-1, n)
    tri = re.search(rf'<geometry id="{gid}".*?<p>([^<]*)</p>', text, re.S).group(1)
    t = get('t', 2); u = get('u', 2)
    return get('p', 3), get('n', 3), np.c_[t[:, 0], 1 - t[:, 1]], np.array(tri.split(), 'i8').reshape(-1, 3), np.c_[u[:, 0], 1 - u[:, 1]]


def split(p, tri, hub, axis, radius, depth):
    """Triangles of the parts that lie wholly inside the fan's disc. Vertices at the same place are joined, because UV seams duplicate them."""
    _, weld = np.unique(np.round(p, 3), axis=0, return_inverse=True); weld = weld.ravel()
    t = weld[tri]; n = weld.max() + 1
    g = coo_matrix((np.ones(tri.size), (t.ravel(), np.roll(t, 1, axis=1).ravel())), shape=(n, n))
    _, part = connected_components(g, directed=False)
    d = p - hub; along = d @ axis; off = np.linalg.norm(d - np.outer(along, axis), axis=1)
    inside = np.ones(part.max() + 1, bool)
    np.logical_and.at(inside, part[weld], (off < radius) & (np.abs(along) < depth))
    return inside[part[weld[tri[:, 0]]]]


def piece(a, keep):
    """Geometry for the kept triangles only, with unused vertices dropped."""
    p, n, uv, tri, u2 = a
    t = tri[keep]; used, inv = np.unique(t, return_inverse=True)
    return p[used], n[used], uv[used], inv.ravel(), u2[used]


def records(group):
    """(rotation rows, position) of each object in an anim_proc set. Layout is in docs/formats.md."""
    out = {}
    for f in glob.glob('../x/__R00G*.pgeo'):
        b = open(f, 'rb').read()
        m = re.search(rb'anim_proc_' + group.encode() + rb'_\d', b[:0x200])
        if b[:4] != b'OEGP' or not m:
            continue
        n = struct.unpack_from('>I', b, 0x44)[0]
        for i in range(n):
            o = len(b) - 0x50 - 128 * (n - 1 - i)
            r = np.array(struct.unpack_from('>16f', b, o - 48), 'f8')
            out[tuple(np.round(r[12:15], 1))] = r
    return list(out.values())


def yaw_matrix(r):
    """As the 96-byte placed sets in build3.py."""
    Rg = np.c_[r[0:3], r[4:7], r[8:11]]; Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
    gr = S @ Rg if np.linalg.det(Rg) < 0 else S @ Rg @ S
    Rm = B @ gr @ B.T
    if np.linalg.det(Rm) < 0:
        Rm[:, 0] *= -1
    return Rm


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
out = level / 'art' / 'shapes' / 'fh1_anim'; out.mkdir(parents=True, exist_ok=True)
rel = f'/levels/{LVL}/art/shapes/fh1_anim'
rows = []; spins = []; world = [json.loads(l) for l in (mg / 'fh1' / 'items.level.json').read_text().splitlines() if l.strip()]
for kind, w in WINDMILLS.items():
    src = level / 'art' / 'shapes' / 'fh1' / f"{w['shape']}.dae"
    text = src.read_text(); mat = re.search(r'<material id="([^"]+)"', text).group(1)
    hub = np.array(w['hub']); axis = np.array(w['axis'], 'f8')
    lods = [arrays(text, 'g0'), arrays(text, 'g1')]
    masks = [split(a[0], a[3], hub, axis, w['radius'], w['depth']) for a in lods]
    print(kind, 'fan triangles', [int(m.sum()) for m in masks], 'of', [len(a[3]) for a in lods])
    tower = [piece(a, ~m) for a, m in zip(lods, masks)]; fan = [piece(a, m) for a, m in zip(lods, masks)]
    write_lod(out / f'{kind}_tower.dae', [((mat, (0,)),) + tower[0], ((mat, (1, FARCUT)),) + tower[1]], np.zeros(3), (25, 9, 3), 2)
    places = []
    for r in records(w['group']):
        places.append((np.array([r[12] - CX, r[14] + CZ, r[13]]), yaw_matrix(r)))
    for o in world:
        if o.get('shapeName', '').endswith(f"/{w['shape']}.dae") or o.get('shapeName', '').endswith(f'/{kind}_tower.dae'):
            o['shapeName'] = f'{rel}/{kind}_tower.dae'
            places.append((None, np.array(o['rotationMatrix']).reshape(3, 3).T, np.array(o['position'])))
    rng = np.random.default_rng(1)
    for i, place in enumerate(places):
        Rm = place[1]; pos = place[0] if place[0] is not None else place[2]
        if place[0] is not None:
            rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_tower_{i}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}_tower.dae', 'position': [round(float(v), 3) for v in pos],
                         'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'Collision Mesh', 'decalType': 'Collision Mesh'})
        # The facing is baked into each fan, so only a world axis is left to spin about
        def turned(g):
            p, n, uv, tri, u2 = g
            return (p - hub) @ Rm.T, n @ Rm.T, uv, tri, u2
        write_lod(out / f'{kind}_fan_{i}.dae', [((mat, (0,)),) + turned(fan[0]), ((mat, (1,)),) + turned(fan[1])], np.zeros(3), (20, 6, 1), None)
        # Fans aren't level objects: a static TSStatic never redraws when moved, so fh1Extras.lua creates them the way stock code makes moving markers
        at = pos + Rm @ hub; ax = Rm @ axis
        spins.append([f'{rel}/{kind}_fan_{i}.dae'] + [round(float(v), 3) for v in at] + [round(float(v), 5) for v in ax] + [round(float(w['speed'] * (1 + SPEED_SPREAD * rng.uniform(-1, 1))), 3), round(float(rng.uniform(0, math.tau)), 3)])
    print(kind, len(places), 'placed', [[round(float(v)) for v in (p[0] if p[0] is not None else p[2])] for p in places][:8])
(mg / 'fh1' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in world))
(mg / 'fh1_anim').mkdir(exist_ok=True)
(mg / 'fh1_anim' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))
top = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
if not any(o.get('name') == 'fh1_anim' for o in top):
    top.append({'name': 'fh1_anim', 'class': 'SimGroup', '__parent': 'MissionGroup'})
    items.write_text(''.join(json.dumps(o) + '\n' for o in top))
(level / 'fh1_animated.json').write_text(json.dumps(spins))
print('done', len(rows), 'objects', len(spins), 'spinners ->', level)
