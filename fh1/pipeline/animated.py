"""FH1's animated objects, starting with the metal windmills. The fan is cut out of the built shape and given an "ambient" animation, which BeamNG plays by itself,
as West Coast USA's pump jacks do. Fans have no collision.
Run after build3.py, which wipes the level: python animated.py [fh1_colorado]. Safe to run twice. Needs no rebuild."""
import glob, json, os, re, struct, sys
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
# Spin speed isn't read from FH1's curves yet. Parts within radius of the hub and depth along its axis are the fan
WINDMILLS = {'metal': dict(shape='m6625', group='fx_windmillsmetal', hub=(-.507, -1.174, 9.931), axis=(0, 1, 0), radius=1.75, depth=.6, speed=2.2)}
# Seconds per turn. Each speed needs its own shape, as the animation lives in the file
TURN_SECONDS = (2.4, 2.9, 3.5)


def animate(path, seconds):
    """Turn every LOD node of a shape about its Y axis. Nodes use the separate transforms that animation channels address, as in the stock pump jacks."""
    text = path.read_text(); anims = []
    keys = 8; times = ' '.join(f'{seconds * i / keys:.5f}' for i in range(keys + 1)); angles = ' '.join(f'{360 * i / keys:.3f}' for i in range(keys + 1))
    for node in re.findall(r'<node id="(o\d+_a\d+)"', text):
        m = re.search(rf'(<node id="{node}"[^>]*>\s*)<matrix sid="transform">[^<]*</matrix>', text)
        text = text[:m.start()] + m.group(1) + ('<translate sid="location">0 0 0</translate><rotate sid="rotationZ">0 0 1 0</rotate><rotate sid="rotationY">0 1 0 0</rotate>'
                                                '<rotate sid="rotationX">1 0 0 0</rotate><scale sid="scale">1 1 1</scale>') + text[m.end():]
        a = f'{node}_spin'
        anims.append(f'<animation id="{a}">'
                     f'<source id="{a}-in"><float_array id="{a}-in-a" count="{keys + 1}">{times}</float_array><technique_common><accessor source="#{a}-in-a" count="{keys + 1}" stride="1"><param name="TIME" type="float"/></accessor></technique_common></source>'
                     f'<source id="{a}-out"><float_array id="{a}-out-a" count="{keys + 1}">{angles}</float_array><technique_common><accessor source="#{a}-out-a" count="{keys + 1}" stride="1"><param name="ANGLE" type="float"/></accessor></technique_common></source>'
                     f'<source id="{a}-int"><Name_array id="{a}-int-a" count="{keys + 1}">{" ".join(["LINEAR"] * (keys + 1))}</Name_array><technique_common><accessor source="#{a}-int-a" count="{keys + 1}" stride="1"><param name="INTERPOLATION" type="name"/></accessor></technique_common></source>'
                     f'<sampler id="{a}-s"><input semantic="INPUT" source="#{a}-in"/><input semantic="OUTPUT" source="#{a}-out"/><input semantic="INTERPOLATION" source="#{a}-int"/></sampler>'
                     f'<channel source="#{a}-s" target="{node}/rotationY.ANGLE"/></animation>\n')
    if not anims:
        raise ValueError(f'no LOD nodes in {path}')
    i = text.index('<library_visual_scenes')
    path.write_text(text[:i] + '<library_animations>\n' + ''.join(anims) + '</library_animations>\n' + text[i:])


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
for old in out.glob('*'):
    old.unlink()
rel = f'/levels/{LVL}/art/shapes/fh1_anim'
rows = []; world = [json.loads(l) for l in (mg / 'fh1' / 'items.level.json').read_text().splitlines() if l.strip()]
for kind, w in WINDMILLS.items():
    src = level / 'art' / 'shapes' / 'fh1' / f"{w['shape']}.dae"
    text = src.read_text(); mat = re.search(r'<material id="([^"]+)"', text).group(1)
    hub = np.array(w['hub']); axis = np.array(w['axis'], 'f8')
    lods = [arrays(text, 'g0'), arrays(text, 'g1')]
    masks = [split(a[0], a[3], hub, axis, w['radius'], w['depth']) for a in lods]
    print(kind, 'fan triangles', [int(m.sum()) for m in masks], 'of', [len(a[3]) for a in lods])
    tower = [piece(a, ~m) for a, m in zip(lods, masks)]; fan = [piece(a, m) for a, m in zip(lods, masks)]
    write_lod(out / f'{kind}_tower.dae', [((mat, (0,)),) + tower[0], ((mat, (1, FARCUT)),) + tower[1]], np.zeros(3), (25, 9, 3), 2)
    # The fan shape is centred on the hub, with its axis along Y
    def centred(g):
        p, n, uv, tri, u2 = g
        return p - hub, n, uv, tri, u2
    for v, seconds in enumerate(TURN_SECONDS):
        path = out / f'{kind}_fan_{v}.dae'
        write_lod(path, [((mat, (0,)),) + centred(fan[0]), ((mat, (1,)),) + centred(fan[1])], np.zeros(3), (20, 6, 1), None)
        animate(path, seconds)
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
        at = pos + Rm @ hub
        rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_fan_{i}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}_fan_{int(rng.integers(len(TURN_SECONDS)))}.dae', 'position': [round(float(v), 3) for v in at],
                     'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
    print(kind, len(places), 'placed', [[round(float(v)) for v in (p[0] if p[0] is not None else p[2])] for p in places][:8])
(mg / 'fh1' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in world))
(mg / 'fh1_anim').mkdir(exist_ok=True)
(mg / 'fh1_anim' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))
top = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
if not any(o.get('name') == 'fh1_anim' for o in top):
    top.append({'name': 'fh1_anim', 'class': 'SimGroup', '__parent': 'MissionGroup'})
    items.write_text(''.join(json.dumps(o) + '\n' for o in top))
(level / 'fh1_animated.json').unlink(missing_ok=True)
print('done', len(rows), 'objects ->', level)
