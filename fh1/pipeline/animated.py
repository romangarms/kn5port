"""FH1's animated objects: windmills, wind turbines, tractors, combines and the crop duster. The fan is cut out of the built shape and given an "ambient" animation, which BeamNG plays by itself,
as West Coast USA's pump jacks do. Vehicles follow the route in their ANIM_*.pgeo the same way. Moving parts have no collision.
Run after build3.py, which wipes the level: python animated.py [fh1_colorado]. Safe to run twice. Needs no rebuild."""
import json, os, pickle, re, struct, sys
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from daewrite import FARCUT, write_lod
import gamezip, granny, mission, rmb
from animsets import records
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0), 'fh1_clearsprings': (1700.0, 2000.0, 2048.0)}[LVL]
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); S = np.diag([1., 1., -1.])
# shape: the built shape for this mesh. group: the anim_proc set that places it. hub and axis: the Fan bone from its ANIM_*.pgeo, checked against the mesh, in BeamNG axes.
# radius and depth: parts wholly inside this disc are the fan. origin: the point of the shape that a set's position means, where the ANIM model is centred differently.
# turns: seconds per turn, chosen by eye as FH1's curves aren't decoded. Each speed needs its own shape, as the animation lives in the file.
# tower_lod and fan_lod: detail sizes, from build3.py's detail_sizes for the object's radius
OBJECTS = {'metal': dict(shape='m6625', group='fx_windmillsmetal', hub=(-.507, -1.174, 9.931), radius=1.75, depth=.6, turns=(2.4, 2.9, 3.5), tower_lod=(25, 9, 3), fan_lod=(20, 6, 1)),
           'wood': dict(shape='m6627', group='fx_windmillswood', hub=(-.02, -1.55, 9.94), radius=1.75, depth=.5, turns=(2.4, 2.9, 3.5), tower_lod=(25, 9, 3), fan_lod=(20, 6, 1)),
           'turbine': dict(shape='m6429', group='fx_maintown', hub=(7.42, -4.6, 82.17), radius=56, depth=1.6, origin=(7.5, -1.35, 0), turns=(5, 6, 7.5), tower_lod=(211, 88, 45), fan_lod=(190, 70, 1))}
AXIS = (0, 1, 0)
# mesh: the source meshes for LOD0 and LOD1. model: the file holding the route. group: the anim_proc set that places it. lod: detail sizes
# front: which way along y the mesh faces, given for vehicles whose route is a quantised curve. key: seconds between keys.
# parts: meshes that spin about the vehicle's x axis through hub, in BeamNG axes, once per `seconds`. The combine's reel speed is chosen by eye
VEHICLES = {'tractor': dict(mesh=(6680, 6681), model='ANIM_Tractor_001.pgeo', group='clrd_festival_tractor', lod=(40, 14, 1)),
            'combine': dict(mesh=(6621, 6622), model='ANIM_CombineHarvester_001.pgeo', group='clrd_festival_combineharvester', lod=(40, 14, 1), front=-1, key=.25,
                            parts={'reel': dict(mesh=6623, hub=(0, -5.607, .992), seconds=1.5)})}
KEY_SECONDS = 1.0; STOPPED = .05; AXLES = 1.4; TRACK = 1.0
# Planes come from Granny meshes, which have no decoded UVs, so colour is the median of their texture. The route is level in FH1 once its tilted frame is removed
FLYERS = {'cropduster': dict(model='ANIM_CropDuster_Zone_05_001.pgeo', group='cropduster_zone_05_001', colour=(117, 16, 10), lod=(60, 20, 1))}
FLY_KEY_SECONDS = .5; CLEARANCE = 25.0; GROUND_RADIUS = 80.0; MAX_BANK = 55.0; GRAVITY = 9.81


def channel(node, target, param, times, values):
    """One animation curve, in the form Collada loaders expect."""
    a = f"{node}_{re.sub('[^A-Za-z]', '', target)}"; n = len(times)
    t = ' '.join(f'{v:.4f}' for v in times); o = ' '.join(f'{v:.4f}' for v in values)
    return (f'<animation id="{a}">'
            f'<source id="{a}-in"><float_array id="{a}-in-a" count="{n}">{t}</float_array><technique_common><accessor source="#{a}-in-a" count="{n}" stride="1"><param name="TIME" type="float"/></accessor></technique_common></source>'
            f'<source id="{a}-out"><float_array id="{a}-out-a" count="{n}">{o}</float_array><technique_common><accessor source="#{a}-out-a" count="{n}" stride="1"><param name="{param}" type="float"/></accessor></technique_common></source>'
            f'<source id="{a}-int"><Name_array id="{a}-int-a" count="{n}">{" ".join(["LINEAR"] * n)}</Name_array><technique_common><accessor source="#{a}-int-a" count="{n}" stride="1"><param name="INTERPOLATION" type="name"/></accessor></technique_common></source>'
            f'<sampler id="{a}-s"><input semantic="INPUT" source="#{a}-in"/><input semantic="OUTPUT" source="#{a}-out"/><input semantic="INTERPOLATION" source="#{a}-int"/></sampler>'
            f'<channel source="#{a}-s" target="{node}/{target}"/></animation>\n')


def animate(path, curves, extra=''):
    """Give each LOD node the separate transforms that animation channels address, as in the stock pump jacks. curves: (target, param, times, values)."""
    text = path.read_text(); anims = []
    for node in re.findall(r'<node id="(o\d+_a\d+)"', text):
        m = re.search(rf'(<node id="{node}"[^>]*>\s*)<matrix sid="transform">[^<]*</matrix>', text)
        text = text[:m.start()] + m.group(1) + ('<translate sid="location">0 0 0</translate><rotate sid="rotationZ">0 0 1 0</rotate><rotate sid="rotationY">0 1 0 0</rotate>'
                                                '<rotate sid="rotationX">1 0 0 0</rotate><scale sid="scale">1 1 1</scale>') + text[m.end():]
        anims += [channel(node, *c) for c in curves]
    if not anims:
        raise ValueError(f'no LOD nodes in {path}')
    i = text.index('<library_visual_scenes')
    text = text[:i] + '<library_animations>\n' + ''.join(anims) + '</library_animations>\n' + text[i:]
    if extra:
        text = text.replace('</library_geometries>', extra[0] + '</library_geometries>', 1)
        text = re.sub(r'(<visual_scene [^>]*>)', lambda m: m.group(1) + extra[1], text, 1)
    path.write_text(text)


def spin(seconds, keys=8):
    times = [seconds * i / keys for i in range(keys + 1)]
    return [('rotationY.ANGLE', 'ANGLE', times, [360 * i / keys for i in range(keys + 1)])]


def bounds(lo, hi):
    """BeamNG culls a shape by its resting bounds, so a vehicle would vanish partway along its route. A node named bounds sets them."""
    x = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]), (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]), (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
    tri = '0 2 1 0 3 2 4 5 6 4 6 7 0 1 5 0 5 4 1 2 6 1 6 5 2 3 7 2 7 6 3 0 4 3 4 7'
    p = ' '.join(f'{v:.3f}' for q in x for v in q)
    return (f'<geometry id="bounds_g" name="bounds_g"><mesh><source id="bounds_p"><float_array id="bounds_a" count="24">{p}</float_array><technique_common><accessor source="#bounds_a" count="8" stride="3">'
            '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>'
            f'<vertices id="bounds_v"><input semantic="POSITION" source="#bounds_p"/></vertices><triangles count="12"><input semantic="VERTEX" source="#bounds_v" offset="0"/><p>{tri}</p></triangles></mesh></geometry>\n',
            '<node id="bounds" name="bounds" type="NODE"><instance_geometry url="#bounds_g"/></node>')


def route(name):
    """Times and positions of an ANIM model's root track, the longest move among its raw float curves. These are a run of rising knots followed by three floats per knot."""
    b = gamezip.read('animatedobjects.zip', name); F = np.frombuffer(b[:len(b) // 4 * 4], '>f4'); best = None; i = 0x100 // 4
    while i < len(F) - 8:
        j = i
        while j + 1 < len(F) and 0 <= F[j] < F[j + 1] < 2000 and F[j + 1] - F[j] < 60:
            j += 1
        n = j - i + 1
        if n >= 7 and j + 1 + 3 * n <= len(F):
            c = F[j + 1:j + 1 + 3 * n].astype('f8').reshape(-1, 3)
            if np.isfinite(c).all() and np.abs(c).max() < 5000 and (best is None or np.ptp(c, axis=0).max() > np.ptp(best[1], axis=0).max()):
                best = (F[i:j + 1].astype('f8'), c)
            i = j + 1
        else:
            i += 1
    return best


def level_route(c):
    """Route in BeamNG axes with the tilt of its best-fit plane removed. FH1's frame for it is tilted, but the flight is flat in that plane. Compass direction is kept as far as a plain tilt keeps it."""
    n = np.linalg.svd(c - c.mean(0), full_matrices=False)[2][2]; n = -n if n[1] < 0 else n
    v = np.cross(n, (0, 1, 0)); k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    w = c @ (np.eye(3) + k + k @ k / (1 + n[1])).T
    return np.c_[w[:, 0], w[:, 2], w[:, 1]]


def quantised_route(b):
    """Times and positions of a D3K16uC16u curve, in Granny axes. Its header is format 10, degree, the top half of 1 / knot scale as a float, 3 scales, 3 offsets, then the count of
    u16 values. The values sit elsewhere, as knots rising from 0 and then 3 controls per knot, so the run that reads as the smoothest path is taken."""
    U = np.frombuffer(b[:len(b) // 2 * 2], '>u2').astype('i8'); rising = np.r_[np.diff(U) > 0, False]
    for i in range(len(b) - 32):
        if b[i] != 10 or b[i + 1] > 3:
            continue
        f = np.array(struct.unpack('>6f', b[i + 4:i + 28])); cnt = struct.unpack('>I', b[i + 28:i + 32])[0]
        if not np.isfinite(f).all() or np.abs(f).max() > 1e4 or not 0 < cnt < 100000 or cnt % 4 or not np.abs(f[:3]).max() > 1e-6:
            continue
        per = struct.unpack('>f', b[i + 2:i + 4] + b'\0\0')[0]; n = cnt // 4; best = None
        for s in np.flatnonzero(U == 0):
            if s + cnt <= len(U) and rising[s:s + n - 1].all():
                c = U[s + n:s + cnt].reshape(-1, 3) * f[:3] + f[3:]; step = np.median(np.abs(np.diff(c, axis=0)).max(1))
                if best is None or step < best[0]:
                    best = (step, U[s:s + n] / per, c)
        if best:
            return best[1:]
    raise ValueError('no D3K16uC16u curve')


def fit_box(u, body, box):
    """Offset that centres a route, with the body's footprint turning along it, in the bounding box of the model's header (min x, y, z, pad, max x, y, z)."""
    d = np.gradient(u, axis=0); a = np.arctan2(d[:, 0], d[:, 1]); c, s = np.cos(a)[:, None], np.sin(a)[:, None]
    fx, fy = np.ptp(body[:, 0]) / 2, np.ptp(body[:, 1]) / 2
    pts = np.concatenate([u + np.c_[c * x + s * y, -s * x + c * y] for x in (-fx, fx) for y in (-fy, fy)])
    lo, hi = np.array([box[0], box[2]]), np.array([box[4], box[6]])
    print('  route box', np.round(np.ptp(pts, 0), 1), 'header box', np.round(hi - lo, 1))
    return (lo + hi) / 2 - (pts.min(0) + pts.max(0)) / 2


def turn(yaw, roll, pitch):
    """The rotation of a node with rotationZ, rotationY and rotationX in that order, in degrees."""
    z, y, x = np.radians([yaw, roll, pitch])
    return (np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]]) @ np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
            @ np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]]))


def flight(t, q):
    """Keys through a route, with the plane's heading, pitch and bank taken from its motion, as FH1's rotation curves aren't decoded."""
    times = np.arange(0, t[-1], FLY_KEY_SECONDS); p = np.c_[[np.interp(times, t, q[:, i]) for i in range(3)]].T
    times = np.r_[times, t[-1]]; p = np.r_[p, p[:1]]
    d = np.gradient(p, times, axis=0); flat = np.hypot(d[:, 0], d[:, 1])
    yaw = np.unwrap(np.arctan2(-d[:, 0], d[:, 1]))
    rate = np.convolve(np.gradient(yaw, times), np.ones(7) / 7, 'same')
    bank = np.clip(np.degrees(np.arctan(flat * rate / GRAVITY)), -MAX_BANK, MAX_BANK)
    return times, p, np.degrees(yaw), np.degrees(np.arctan2(d[:, 2], flat)), -bank


def ground_triangles(lo, hi):
    """Ground triangles in a box, from the collision shapes of the map cells, in world space."""
    out = []
    for o in world:
        name = Path(o['shapeName']).name; q = np.array(o['position'])
        if not name.endswith(('_col.dae', '_colf.dae')) or (q[:2] < lo - 600).any() or (q[:2] > hi + 600).any():
            continue
        R = np.array(o.get('rotationMatrix', np.eye(3).ravel())).reshape(3, 3)
        for geo in (level / 'art' / 'shapes' / 'fh1' / name).read_text().split('<geometry ')[1:]:
            v = re.search(r'-p-a"[^>]*>([^<]*)<', geo)
            V = np.array(v.group(1).split(), 'f8').reshape(-1, 3) @ R + q
            for inputs, p in re.findall(r'<triangles[^>]*>(.*?)<p>([^<]*)</p>', geo, re.S):
                step = max(map(int, re.findall(r'offset="(\d+)"', inputs))) + 1
                out.append(V[np.array(p.split(), 'i8')[::step].reshape(-1, 3)])
    T = np.concatenate(out); box = (T[:, :, :2].max(1) >= lo).all(1) & (T[:, :, :2].min(1) <= hi).all(1)
    return T[box]


def height(T, xy):
    """Height of the highest triangle over each point."""
    lo, hi = T[:, :, :2].min(1), T[:, :, :2].max(1); out = np.zeros(len(xy))
    for k, q in enumerate(xy):
        A, B_, C = T[(lo <= q).all(1) & (hi >= q).all(1)].transpose(1, 0, 2)
        e, f = B_ - A, C - A; g = q - A[:, :2]; d = e[:, 0] * f[:, 1] - e[:, 1] * f[:, 0]
        u = (g[:, 0] * f[:, 1] - g[:, 1] * f[:, 0]) / d; v = (e[:, 0] * g[:, 1] - e[:, 1] * g[:, 0]) / d
        inside = (u >= -1e-3) & (v >= -1e-3) & (u + v <= 1.001)
        out[k] = (A[:, 2] + u * e[:, 2] + v * f[:, 2])[inside].max()
    return out


def on_ground(T, xy, yaw):
    """Height, pitch and roll of a vehicle on one plane fitted to the ground under its route, so FH1's flat route keeps its shape and only tilts.
    Points far off the plane, such as objects on the ground, are dropped before the second fit."""
    A = np.c_[xy, np.ones(len(xy))]; h = height(T, xy); keep = np.ones(len(xy), bool)
    for _ in range(2):
        coef = np.linalg.lstsq(A[keep], h[keep], rcond=None)[0]; keep = np.abs(h - A @ coef) < 1
    plane = lambda q: np.c_[q, np.ones(len(q))] @ coef
    a = np.radians(yaw)[:, None]; ahead = np.c_[-np.sin(a), np.cos(a)]; right = np.c_[np.cos(a), np.sin(a)]
    f, b_ = plane(xy + AXLES * ahead), plane(xy - AXLES * ahead); r, l = plane(xy + TRACK * right), plane(xy - TRACK * right)
    return (f + b_ + r + l) / 4, np.degrees(np.arctan2(f - b_, 2 * AXLES)), np.degrees(np.arctan2(l - r, 2 * TRACK))


def ground_samples():
    """Points on the ground. The level has no TerrainBlock, as FH1's meshes are the ground, so this uses what stands on them: forest items and road waypoints."""
    pts = [o['pos'] for f in (level / 'forest').glob('*.json') for o in map(json.loads, filter(str.strip, f.read_text().splitlines()))]
    pts += [o['position'] for o in map(json.loads, filter(str.strip, (mg / 'fh1_nav' / 'items.level.json').read_text().splitlines())) if 'position' in o]
    return np.array(pts, 'f8')


def mesh_groups(nos):
    """Geometry straight from the source meshes, for objects the build merged into map cells. Naming follows Textures and box_uv in build3.py."""
    MT = pickle.load(open('../meshtab.pkl', 'rb')); IDX = pickle.load(open('../texshader.pkl', 'rb'))[0]
    mats = json.loads((level / 'art' / 'shapes' / f'{LVL}.materials.json').read_text()); out = []
    for lv, no in enumerate(nos):
        m = rmb.parse(open(f'../x/coloradoout.{no:05d}.rmb.bin', 'rb').read())['meshes'][0]
        p, nr, _ = rmb.verts(m); a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
        r0 = np.frombuffer(a[:, 16:20].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535
        pb = np.c_[p[:, 0], -p[:, 2], p[:, 1]]; nb = np.c_[nr[:, 0], -nr[:, 2], nr[:, 1]]
        for s_ in m['secs']:
            T = rmb.tris(s_['ib'], m['nv'], s_['ints'][1])
            names = [f'fh1_d{IDX[i][0]:X}' for i in MT[no][0] if i in IDX]
            mat = next((n for n in names if n in mats), None)
            if mat is None:
                sys.exit(f'no built material for mesh {no}: {names}')
            uv = np.array(s_['fl'][4:6]) + r0 * np.array(s_['fl'][6:8])
            out.append(((mat, (lv,)), pb, nb, uv, T.ravel(), uv))
    return out


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
rows = []; world = mission.rows(mg / 'fh1' / 'items.level.json'); was = {o.get('name'): o.get('shapeName') for o in world}
for kind, w in OBJECTS.items():
    src = level / 'art' / 'shapes' / 'fh1' / f"{w['shape']}.dae"
    if not src.exists():          # a chunk such as Red Rock has none of this kind
        print(kind, 'not in this level')
        continue
    text = src.read_text(); mat = re.search(r'<material id="([^"]+)"', text).group(1)
    hub = np.array(w['hub']); axis = np.array(AXIS, 'f8'); origin = np.array(w.get('origin', (0, 0, 0)), 'f8')
    lods = [arrays(text, 'g0'), arrays(text, 'g1' if '<geometry id="g1"' in text else 'g0')]     # some shapes have one LOD
    masks = [split(a[0], a[3], hub, axis, w['radius'], w['depth']) for a in lods]
    print(kind, 'fan triangles', [int(m.sum()) for m in masks], 'of', [len(a[3]) for a in lods])
    tower = [piece(a, ~m) for a, m in zip(lods, masks)]; fan = [piece(a, m) for a, m in zip(lods, masks)]
    write_lod(out / f'{kind}_tower.dae', [((mat, (0,)),) + tower[0], ((mat, (1, FARCUT)),) + tower[1]], np.zeros(3), w['tower_lod'], 2)
    # The fan shape is centred on the hub, with its axis along Y
    def centred(g):
        p, n, uv, tri, u2 = g
        return p - hub, n, uv, tri, u2
    for v, seconds in enumerate(w['turns']):
        path = out / f'{kind}_fan_{v}.dae'
        write_lod(path, [((mat, (0,)),) + centred(fan[0]), ((mat, (1,)),) + centred(fan[1])], np.zeros(3), w['fan_lod'], None)
        animate(path, spin(seconds))
    places = []
    for r in records(w['group']):
        Rm = yaw_matrix(r); places.append((np.array([r[12] - CX, r[14] + CZ, r[13]]) - Rm @ origin, Rm))
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
        rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_fan_{i}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}_fan_{int(rng.integers(len(w['turns'])))}.dae', 'position': [round(float(v), 3) for v in at],
                     'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
    print(kind, len(places), 'placed', [[round(float(v)) for v in (p[0] if p[0] is not None else p[2])] for p in places][:8])
def inside(r):
    """whether a placement record stands in this level's square"""
    return abs(r[12] - CX) < HALF and abs(r[14] + CZ) < HALF


for kind, w in VEHICLES.items():
    if not any(inside(r) for r in records(w['group'])):         # a chunk such as Red Rock has none of these
        print(kind, 'not in this level')
        continue
    groups = mesh_groups(w['mesh']); p0 = groups[0][1]; b = gamezip.read('animatedobjects.zip', w['model'])
    if 'front' in w:
        forward = w['front']; t, c = quantised_route(b)
        times = np.arange(0, t[-1], w['key']); u = np.c_[np.interp(times, t, c[:, 0]), np.interp(times, t, c[:, 2])]
        # The same quarter turn as the tractor's. The offset of the route in the placement's frame is fitted to the bounding box in the model's header
        u = np.c_[-u[:, 1], u[:, 0]]; u += fit_box(u, p0, np.frombuffer(b[0x10:0x2c], '>f4').astype('f8'))
    else:
        t, c = route(w['model'])
        # Granny axes are x, up, z. The big wheels show which end of the mesh is the rear, so which way it drives
        rear = np.sign(p0[p0[:, 1] > 0, 2].size and (p0[(p0[:, 1] > .7) & (np.abs(p0[:, 0]) > .8), 2].max() - p0[(p0[:, 1] < -.7) & (np.abs(p0[:, 0]) > .8), 2].max()))
        forward = -1 if rear > 0 else 1
        # The ANIM model stands away from its origin, and the route starts from there
        a_ = granny.vertices(b)[2][:, :2]
        start = (np.nanmin(a_, 0) + np.nanmax(a_, 0)) / 2 - (p0[:, :2].min(0) + p0[:, :2].max(0)) / 2
        times = np.arange(0, t[-1], KEY_SECONDS); u = np.c_[np.interp(times, t, c[:, 0]), np.interp(times, t, c[:, 2])] + start
        # The route is a quarter turn from the placement's frame: Granny x, z becomes -z, x. This fits the bounding boxes in the sets' headers, and puts the laps on fields
        u = np.c_[-u[:, 1], u[:, 0]]
    times = np.r_[times, t[-1]]; u = np.r_[u, u[:1]]
    parts = []
    for name, part in w.get('parts', {}).items():
        hub = np.array(part['hub']); turns = round(t[-1] / part['seconds'])
        parts.append((name, hub, mesh_groups((part['mesh'],)), 360 * turns * times / t[-1]))
    # FH1's route is flat, so each placement gets its own shape, tilted to the ground under it
    for i, r in enumerate(records(w['group'])):
        pos = np.array([r[12] - CX, r[14] + CZ, r[13]])
        xy = pos[:2] + u[:, :1] * [r[0], r[2]] + u[:, 1:] * [r[8], r[10]]
        d = np.gradient(xy, times, axis=0); moving = np.hypot(*d.T) > STOPPED
        yaw = np.degrees(np.arctan2(-d[:, 0] * forward, d[:, 1] * forward))
        for k in range(len(yaw)):
            if not moving[k]:
                yaw[k] = yaw[k - 1] if k else yaw[moving][0]
        yaw = np.degrees(np.unwrap(np.radians(yaw)))
        T = ground_triangles(xy.min(0) - 8, xy.max(0) + 8); z, pitch, roll = on_ground(T, xy, yaw)
        x, y = xy.T - pos[:2, None]; z = z - pos[2]
        path = out / f'{kind}_{i}.dae'
        write_lod(path, groups, np.zeros(3), w['lod'], None)
        lo = np.array([x.min() - 6, y.min() - 6, z.min() - 1]); hi = np.array([x.max() + 6, y.max() + 6, z.max() + 6])
        animate(path, [('location.X', 'X', times, x), ('location.Y', 'Y', times, y), ('location.Z', 'Z', times, z), ('rotationZ.ANGLE', 'ANGLE', times, yaw),
                       ('rotationY.ANGLE', 'ANGLE', times, roll), ('rotationX.ANGLE', 'ANGLE', times, pitch)], bounds(lo, hi))
        shapes = [f'{kind}_{i}']
        # A spinning part turns about its own axis, the vehicle's x, so its pitch and spin add. Its node follows the hub as the vehicle moves and tilts
        for name, hub, pg, spin_ in parts:
            ppath = out / f'{kind}_{name}_{i}.dae'
            write_lod(ppath, [(g[0], g[1] - hub) + tuple(g[2:]) for g in pg], np.zeros(3), w['lod'], None)
            at = np.array([turn(a, b_, c_) @ hub for a, b_, c_ in zip(yaw, roll, pitch)])
            animate(ppath, [('location.X', 'X', times, x + at[:, 0]), ('location.Y', 'Y', times, y + at[:, 1]), ('location.Z', 'Z', times, z + at[:, 2]), ('rotationZ.ANGLE', 'ANGLE', times, yaw),
                            ('rotationY.ANGLE', 'ANGLE', times, roll), ('rotationX.ANGLE', 'ANGLE', times, pitch + spin_)], bounds(lo, hi))
            shapes.append(f'{kind}_{name}_{i}')
        for s_ in shapes:
            rows.append({'class': 'TSStatic', 'name': f'fh1_{s_}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{s_}.dae', 'position': [round(float(v), 3) for v in pos],
                         'rotationMatrix': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
        print(kind, i, [round(float(v)) for v in pos], 'ground follows', round(float(np.ptp(z)), 1), 'm, max pitch', round(float(np.abs(pitch).max()), 1), 'max roll', round(float(np.abs(roll).max()), 1))
g = ground_samples()
for kind, w in FLYERS.items():
    if not inside(records(w['group'])[0]):
        print(kind, 'not in this level')
        continue
    q, tri = granny.mesh(w['model']); t, c = route(w['model'])
    path = out / f'{kind}.dae'; mat = f'fh1_{kind}'
    write_lod(path, [((mat, (0, 1, FARCUT)), q, granny.smooth_normals(q, tri), np.zeros((len(q), 2)), tri.ravel(), np.zeros((len(q), 2)))], np.zeros(3), w['lod'], None)
    r = records(w['group'])[0]; pos = np.array([r[12] - CX, r[14] + CZ, r[13]])
    times, p, yaw, pitch, bank = flight(t, level_route(c))
    near = cKDTree(g[:, :2]).query_ball_point(pos[:2] + p[:, :2], GROUND_RADIUS)
    lift = max(g[i, 2].max() - pos[2] - p[k, 2] for k, i in enumerate(near) if i) + CLEARANCE; p[:, 2] += lift
    lo = p.min(0) - 8; hi = p.max(0) + 8
    animate(path, [('location.X', 'X', times, p[:, 0]), ('location.Y', 'Y', times, p[:, 1]), ('location.Z', 'Z', times, p[:, 2]), ('rotationZ.ANGLE', 'ANGLE', times, yaw),
                   ('rotationY.ANGLE', 'ANGLE', times, bank), ('rotationX.ANGLE', 'ANGLE', times, pitch)], bounds(lo, hi))
    (out / 'main.materials.json').write_text(json.dumps({mat: {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'doubleSided': True,
                                                               'Stages': [{'baseColorFactor': [v / 255 for v in w['colour']] + [1], 'roughnessFactor': .55, 'metallicFactor': 0}]}}))
    rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_0', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}.dae', 'position': [round(float(v), 3) for v in pos],
                 'rotationMatrix': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
    world_p = pos + p
    print(kind, len(q), 'verts', len(tri), 'tris, lift', round(float(lift), 1), 'x', np.round([world_p[:, 0].min(), world_p[:, 0].max()]).tolist(), 'y', np.round([world_p[:, 1].min(), world_p[:, 1].max()]).tolist(),
          'z', np.round([world_p[:, 2].min(), world_p[:, 2].max()]).tolist(), 'max bank', round(float(np.abs(bank).max())), 'max pitch', round(float(np.abs(pitch).max())))
moved = {o['name']: o['shapeName'] for o in world if o.get('shapeName') != was.get(o.get('name'))}
with mission.edit(mg / 'fh1' / 'items.level.json') as r_:       # water.py edits this file too, while this runs: only the shapes this swapped change
    for o in r_:
        if o.get('name') in moved:
            o['shapeName'] = moved[o['name']]
(mg / 'fh1_anim').mkdir(exist_ok=True)
(mg / 'fh1_anim' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))
mission.add_groups(items, 'fh1_anim')
(level / 'fh1_animated.json').unlink(missing_ok=True)
print('done', len(rows), 'objects ->', level)
