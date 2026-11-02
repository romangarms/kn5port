"""FH1's night lightmaps as glowing overlays: a copy of each lit ground triangle, lifted a little, with the lightmap as an additive emissive map.
Stock core_environment turns them on at night: the TSStatics carry nightEmissive and the materials instanceEmissive, as Italy's lamps do.
Spot lights at FH1's lamp heads and unclaimed pool centres go in fh1_lamps, pointing down, and stock core_environment turns them all on at night (nightLight).
Every town, or one with TOWN. Run after build3.py, which wipes the level: python nightlights.py [fh1_colorado]. Safe to run twice. Needs no rebuild.
Layout of the lightmaps is in docs/formats.md: slot 7, a top-down view over the box of the ground that shares it."""
import json, os, pickle, re, struct, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ni
from scipy.spatial import cKDTree
import dds, rmb, tex
from daewrite import write_lod
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ = {'fh1_colorado': (-300.0, 500.0), 'fh1_redrock': (-6000.0, 4250.0)}[LVL]
# None for the whole map, or (x, y, radius) in BeamNG metres to limit it, as the Carson prototype did at (-3670, 1378, 1500). The user confirmed Carson in-game
TOWN = None
LM_SLOT = 7; LM = re.compile(r'_lm(\d|_|$)')
# A lightmap counts as lit above LIT of 255. FIT: the share of lit texels within 2 texels of the ground, below which the projection is wrong (buildings)
LIT = 8; FIT = .6
# Chosen by eye: how bright a full-white lightmap texel glows (20 looked a little dim in-game), how far above the ground the overlay sits, and how far it draws
NITS = 40; LIFT = .04; FAR = 600.0; PX = 1250.0
# Pool centres: local maxima at least POOL_GAP metres apart and brighter than POOL_MIN of 255, used to place and colour the lamps' lights
POOL_GAP = 6.0; POOL_MIN = 40
# Lights, so cars, walls and props are lit too. FH1's lamp props carry one at the head: LAMPS gives each shape's head in its own axes, measured
# from the built .dae (m6533 OBJ_CLRD_LampPost, light at the end of its arm; m6559 OBJ_CLRD_StreetLight). A pool within MATCH metres of a head colours it;
# pools with no lamp near (lamps on walls, or meshes not listed) get a light POOL_HEIGHT above the ground. Placement rotation is applied transposed:
# that put lamp-post heads a median 3.4 m from pool centres, against 4.0 m the other way and 3.8 m for the bases
LAMPS = {'m6533': (0.0, 2.0, 7.9), 'm6559': (0.0, 0.0, 4.2)}
MATCH = 10.0; POOL_HEIGHT = 6.0
# Built like West Coast's street lamps (lightemitters_pl_double, _single, _lightcity): a SpotLight at the head pointing straight down (DOWN, the matrix
# they all use), inner angle 25, outer 150-160, range 45-70 m, intensity in lumens with brightness = intensity / 5000, and stock nightLight, so
# core_environment switches them all and the clustered renderer culls and budgets them. Point lights switched nearest-first by fh1Extras.lua showed
# only right by the camera. BRIGHTNESS is what the user approved on those point lights (intensity 12000 / 20000 pi), scaled by the pool's peak.
# Range and outer angle grow with the head's height, from the 4.2 m street light to the 7.9 m lamp post. Only lamp-post heads cast shadows:
# pool-only lights may be in the open, where a shadow from nothing would look wrong
BRIGHTNESS = 12000.0 / (20000 * np.pi); PEAK = .72; LUMENS_PER_BRIGHTNESS = 5000.0
RANGE = (45.0, 70.0); OUTER = (150.0, 160.0); INNER = 25.0; HEIGHTS = (4.2, 7.9); DOWN = [1, 0, 0, 0, 0, -1, 0, 1, 0]
F = pickle.load(open('../cat2.pkl', 'rb')); MT = pickle.load(open('../meshtab.pkl', 'rb')); IDX, SH = pickle.load(open('../texshader.pkl', 'rb'))


def materials(b, p):
    """[(shader index, [slots])] for a file, by material index. Same reading as build3.py"""
    e = p['end']; q = b.find(b'shaders', e)
    if q < 0:
        return []
    k = (q - 4 - e) // 4
    w = struct.unpack_from('>%dI' % k, b, e); out = []
    for i in range(k - 8):
        if w[i] == 3 and w[i + 1] < 32 and w[i + 2:i + 6] == (0, 1, 0, 1) and w[i + 6] < 32:
            for j in range(i + 7, min(i + 70, k - 2)):
                n = w[j + 1]
                if w[j] == 1 and 1 <= n <= 12 and j + 2 + n <= k and all(x < 96 or x >= 0xfffffff0 for x in w[j + 2:j + 2 + n]):
                    out.append((w[i + 1], list(w[j + 2:j + 2 + n])))
                    break
    return out


def g2b(p):
    return np.stack([p[:, 0] - CX, -(p[:, 2] - CZ), p[:, 1]], 1)


def pools(lum, img, lo, span):
    """Light pools in one lightmap: (BeamNG x, y, peak 0-1, linear rgb, radius in metres) at each local maximum above POOL_MIN.
    The radius is that of the area above half the peak, around the maximum."""
    h, w = lum.shape; texel = float(np.mean(span / (w, h))); sm = ni.gaussian_filter(lum, 1)
    win = max(3, int(round(POOL_GAP / texel)) | 1)
    out = []
    for r, c in np.argwhere((sm == ni.maximum_filter(sm, win)) & (sm > POOL_MIN)):
        v = sm[r, c]; lab, _ = ni.label(sm > v / 2); area = lab == lab[r, c]
        rgb = (img[area].astype('f4') / 255) ** 2.2
        fx = lo[0] + (c + .5) / w * span[0]; fz = lo[1] + (r + .5) / h * span[1]
        out.append((fx - CX, -(fz - CZ), float(v) / 255, rgb.mean(0) / max(rgb.mean(0).max(), 1e-3), float(np.sqrt(area.sum() / np.pi)) * texel))
    return out


def n2b(n):
    return np.stack([n[:, 0], -n[:, 2], n[:, 1]], 1)


# Ground sections that use each lightmap, LOD0 only, inside the town. Objects (buildings, signs) use another mapping, so only road and blend shaders
geo = defaultdict(list)
for f in sorted({c['f'] for c in F}):
    no = int(f.split('.')[1])
    if no >= len(MT) or not any(s < len(SH) and LM.search(SH[s]) and SH[s].startswith(('h_road', 'h_blnd', 'h_vblnd')) for s in MT[no][1]):
        continue
    b = open('../x/' + f, 'rb').read(); P = rmb.parse(b); M = materials(b, P); ids = MT[no][0]
    for m in P['meshes']:
        if m['sd'] < 20 or re.search(r'LOD0[1-9]', m['name']):
            continue
        p, nr, _ = rmb.verts(m); q = g2b(p)
        if TOWN and np.hypot(*(q[:, :2].mean(0) - TOWN[:2])) > TOWN[2]:
            continue
        for s in m['secs']:
            mi = s['ints'][2]
            if mi >= len(M):
                continue
            si, slots = M[mi]
            if si >= len(MT[no][1]) or not (LM.search(SH[MT[no][1][si]]) and SH[MT[no][1][si]].startswith(('h_road', 'h_blnd', 'h_vblnd'))) or len(slots) <= LM_SLOT or slots[LM_SLOT] >= len(ids):
                continue
            T = rmb.tris(s['ib'], m['nv'], s['ints'][1])
            if len(T):
                geo[IDX[ids[slots[LM_SLOT]]][0]].append((p, nr, T))

level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
out = level / 'art' / 'shapes' / 'fh1_night'; out.mkdir(parents=True, exist_ok=True)
for old in out.glob('*'):
    old.unlink()
rel = f'/levels/{LVL}/art/shapes/fh1_night'
mats = {}; rows = []; stats = defaultdict(int); found = []
for tid, parts in sorted(geo.items()):
    r = tex.load(tid)
    if not r or r[0] is None:
        stats['missing'] += 1
        continue
    img = r[0][..., :3]; lum = img.astype('f4').mean(2); h, w = lum.shape
    if (lum > LIT).mean() < .002:
        stats['dark'] += 1
        continue
    # The lightmap spans the box of all the ground sharing it, seen from above: u along FH1 x, v along FH1 z, both from the low corner
    allp = np.concatenate([p[np.unique(T)][:, [0, 2]] for p, _, T in parts]); lo = allp.min(0); span = np.maximum(allp.max(0) - lo, 1e-6)
    lit = ni.maximum_filter(lum, 3) > LIT; cover = Image.new('1', (w, h)); dc = ImageDraw.Draw(cover); keep = []
    for p, nr, T in parts:
        uv = (p[:, [0, 2]] - lo) / span
        for t in T:
            poly = [tuple(x) for x in uv[t] * (w, h)]; dc.polygon(poly, fill=1)
            # A triangle stays if any texel it covers is lit. Most of a town's ground is dark at night
            im = Image.new('1', (w, h)); ImageDraw.Draw(im).polygon(poly, fill=1); a = np.asarray(im, bool)
            if (a & lit).any():
                keep.append((p[t], nr[t], uv[t]))
    fit = (ni.binary_dilation(np.asarray(cover, bool), iterations=2) & (lum > 40)).sum() / max((lum > 40).sum(), 1)
    if fit < FIT or not keep:
        stats['unfit' if keep else 'unlit'] += 1
        continue
    P_ = g2b(np.concatenate([k[0] for k in keep])); N_ = n2b(np.concatenate([k[1] for k in keep])); UV = np.concatenate([k[2] for k in keep])
    # Each pool centre takes the height of the nearest lit ground
    near = pools(lum, img, lo, span)
    if near:
        _, j = cKDTree(P_[:, :2]).query([q[:2] for q in near]); found += [(x, y, float(P_[k, 2]), *rest) for (x, y, *rest), k in zip(near, j)]
    P_ = P_ + N_ * LIFT; ctr = (P_.min(0) + P_.max(0)) / 2; R_ = float(np.linalg.norm(P_.max(0) - P_.min(0)) / 2)
    name = f'lm{tid:X}'; mat = f'fh1_night_{name}'
    dds.save(out / f'{name}.color.dds', img)
    mats[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'materialTag0': 'beamng', 'translucent': True, 'translucentBlendOp': 'Add',
                 'translucentZWrite': False, 'castShadows': False,
                 'Stages': [{'baseColorFactor': [0, 0, 0, 1], 'roughnessFactor': 1, 'metallicFactor': 0, 'emissive': True, 'instanceEmissive': True,
                             'emissiveMap': f'{rel}/{name}.color.dds', 'emissiveFactor': [1, 1, 1], 'emissiveIntensityNits': NITS}, {}, {}, {}]}
    write_lod(out / f'{name}.dae', [((mat, (0,)), P_, N_, UV, np.arange(len(P_)))], ctr, (max(int(R_ / (FAR + R_) * PX), 2),), None)
    # Not named like its material: BeamNG refuses an object that shares a material's name. Stock core_environment sets instanceColor to nightEmissiveColor at night and to black by day
    rows.append({'class': 'TSStatic', 'name': f'fh1_nightlit_{name}', '__parent': 'fh1_night', 'shapeName': f'{rel}/{name}.dae', 'position': [round(float(v), 3) for v in ctr],
                 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True, 'instanceColor': [0, 0, 0, 1], 'nightEmissive': '1', 'nightEmissiveColor': '255 255 255'})
    stats['shapes'] += 1; stats['triangles'] += len(P_) // 3
(out / 'main.materials.json').write_text(json.dumps(mats, indent=1))
mg = items.parent; (mg / 'fh1_night').mkdir(exist_ok=True)
(mg / 'fh1_night' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))

# Pool centres from overlapping lightmaps: the brightest within POOL_GAP stands for all
order = sorted(range(len(found)), key=lambda i: -found[i][3]); taken = np.zeros(len(found), bool); pool = []
tree = cKDTree([q[:2] for q in found]) if found else None
for i in order:
    if not taken[i]:
        pool.append(found[i]); taken[tree.query_ball_point(found[i][:2], POOL_GAP)] = True
heads = []
for f in mg.rglob('items.level.json'):
    if f.parent.name in ('fh1_night', 'fh1_lamps'):
        continue
    for l in f.read_text().splitlines():
        if '"TSStatic"' in l and any(k in l for k in LAMPS):
            o = json.loads(l); k = Path(o['shapeName']).stem
            if k in LAMPS:
                R = np.array(o.get('rotationMatrix', np.eye(3).ravel())).reshape(3, 3)
                heads.append((np.array(o['position']) + R.T @ np.array(LAMPS[k]), LAMPS[k][2]))
if TOWN:
    heads = [h for h in heads if np.hypot(*(h[0][:2] - TOWN[:2])) < TOWN[2]]
WARM = np.array([1.0, .75, .45])
lights = []; used = np.zeros(len(pool), bool)
ptree = cKDTree([q[:2] for q in pool]) if pool else None
for p, rise in heads:
    d, j = ptree.query(p[:2]) if ptree else (np.inf, 0)
    if d < MATCH:
        used[j] = True; _, _, _, peak, rgb, rad = pool[j]
    else:
        peak, rgb, rad = PEAK, WARM, 6.0
    lights.append((p, peak, rgb, rise, True)); stats['lamp_matched' if d < MATCH else 'lamp_unmatched'] += 1
htree = cKDTree([h[0][:2] for h in heads]) if heads else None
for q, u in zip(pool, used):
    if u or (htree is not None and htree.query(q[:2])[0] < MATCH):
        continue
    x, y, z, peak, rgb, rad = q; lights.append((np.array([x, y, z + POOL_HEIGHT]), peak, rgb, POOL_HEIGHT, False)); stats['pool_lights'] += 1
lamp_rows = []
for i, (p, peak, rgb, rise, shadows) in enumerate(lights):
    b = BRIGHTNESS * float(np.clip(peak / PEAK, .4, 1.5)); t = float(np.clip((rise - HEIGHTS[0]) / (HEIGHTS[1] - HEIGHTS[0]), 0, 1))
    lamp_rows.append({'class': 'SpotLight', 'name': f'fh1_lamp_{i}', '__parent': 'fh1_lamps', 'position': [round(float(v), 2) for v in p], 'rotationMatrix': DOWN,
                      'color': [round(float(c), 3) for c in rgb] + [1], 'intensity': round(b * LUMENS_PER_BRIGHTNESS, 2), 'intensityUnit': 'lm', 'brightness': b,
                      'range': round(RANGE[0] + t * (RANGE[1] - RANGE[0]), 1), 'innerAngle': INNER, 'outerAngle': round(OUTER[0] + t * (OUTER[1] - OUTER[0]), 1),
                      'castShadows': shadows, 'isEnabled': False, 'nightLight': '1', 'useColorTemperature': 'false'})
    stats['shadowed' if shadows else 'unshadowed'] += 1
(mg / 'fh1_lamps').mkdir(exist_ok=True)
(mg / 'fh1_lamps' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in lamp_rows))
# fh1Extras.lua used to switch the nearest lamps from this list; stock nightLight does it now
(level / 'fh1_lamps.json').unlink(missing_ok=True)
top = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
for g in ('fh1_night', 'fh1_lamps'):
    if not any(o.get('name') == g for o in top):
        top.append({'name': g, 'class': 'SimGroup', '__parent': 'MissionGroup'})
items.write_text(''.join(json.dumps(o) + '\n' for o in top))
print('done', dict(stats), 'lightmaps', len(geo), 'pools', len(pool), 'lights', len(lights), '->', level)
