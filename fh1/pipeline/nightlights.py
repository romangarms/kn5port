"""FH1's night lightmaps as glowing overlays: a copy of each lit ground triangle, lifted a little, with the lightmap as an additive emissive map.
Stock core_environment turns them on at night: the TSStatics carry nightEmissive and the materials instanceEmissive, as Italy's lamps do.
Every town, or one with TOWN. Run after build3.py, which wipes the level: python nightlights.py [fh1_colorado]. Safe to run twice. Needs no rebuild.
Layout of the lightmaps is in docs/formats.md: slot 7, a top-down view over the box of the ground that shares it."""
import json, os, pickle, re, struct, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ni
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
mats = {}; rows = []; stats = defaultdict(int)
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
top = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
if not any(o.get('name') == 'fh1_night' for o in top):
    top.append({'name': 'fh1_night', 'class': 'SimGroup', '__parent': 'MissionGroup'})
    items.write_text(''.join(json.dumps(o) + '\n' for o in top))
print('done', dict(stats), 'lightmaps', len(geo), '->', level)
