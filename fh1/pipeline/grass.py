"""FH1's ground clutter from its Grass_Ungrouped sets: grass, reeds, flowers and corn, as clumps of FH1's sprites placed as forest items.
FH1 scatters sprites over each set's patch of ground at runtime, density x triangle area x vertex weight (pgrass.py, docs/formats.md). One item per sprite
would be 10M items, so each group of layers becomes a few clump shapes of about 8 m2, placed at that density. Crowd sprite sets are left out.
Run after build3.py, which wipes the level: python grass.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import hashlib, json, os, pickle, re, shutil, subprocess, sys
from pathlib import Path
import numpy as np
from scipy import ndimage as ni
from scipy.spatial import cKDTree
import dds, pgrass, tex
from daewrite import write_lod
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0), 'fh1_clearsprings': (1700.0, 2000.0, 2048.0)}[LVL]
CROWD, CORN = 20491, 20489           # texture ids: festival crowd people, and corn
HEIGHT = 1.3                         # GrassHeightScale in TrackSettings.xml
TILE = (8.0, 16.0); MIN_SPRITES = 3  # clump area in m2: 8, grown so sparse layers still get 3 sprites a clump
EDGE_R = .75                         # near a patch's edge, clumps of this radius whose centres stay that far inside, so grass keeps off the asphalt
VARIANTS = 4; EDGE_VARIANTS = 2; FAR_SHARE = 1 / 3       # sprites kept in the far detail
# FH1 fades its layers out from 40 to 50 or 60 m. Shorter here for fps: full clumps to NEAR, a third of their sprites to FAR, then nothing. Corn stays to 240 m
NEAR, FAR, CORN_NEAR, CORN_FAR = 25.0, 45.0, 120.0, 240.0
PX = 1250.0                          # as build3.py: detail size = PX x radius / (distance + radius)
TILT = 25.0; SLOPE = (30.0, 70.0)    # clumps lean with the ground up to TILT degrees. Density falls from 1 at SLOPE[0] to 0 at SLOPE[1]
SINK = .03; TEXMAX = 1024; CACHE = Path('../ddscache'); SEED = 1

level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL; rel = f'/levels/{LVL}'
if not (level / 'art' / 'forest' / 'managedItemData.json').exists():
    sys.exit(f'{level} has no forest: run build3.py first')
IDX, _ = pickle.load(open('../texshader.pkl', 'rb'))
gdir = level / 'art' / 'forest' / 'grass'; fdir = level / 'forest'; tdir = level / 'art' / 'textures'
shutil.rmtree(gdir, ignore_errors=True); gdir.mkdir(parents=True)
for f in fdir.glob('fh1_grass_*.forest4.json'):
    f.unlink()
managed = {k: v for k, v in json.loads((level / 'art' / 'forest' / 'managedItemData.json').read_text()).items() if not k.startswith('fh1_grass_')}
rng = np.random.default_rng(SEED)


def material(tid):
    """alpha-tested, double-sided card material that casts no shadow, for fps. Colour bleeds into the cut-out texels, so mips don't fringe the blades with the atlas background"""
    key = f'fh1_grass_{tid:X}'
    if key in MATS:
        return key
    a = tex.load(tid)[0]; al = a[..., 3].astype('f4') / 255; rgb = a[..., :3].astype('f4'); out = rgb.copy(); need = al < .5
    for s_ in (1, 2, 4, 8, 16, 32):
        w_ = ni.gaussian_filter(al, s_); c_ = np.stack([ni.gaussian_filter(rgb[..., i] * al, s_) for i in range(3)], -1) / np.maximum(w_[..., None], 1e-6)
        fill = need & (w_ > 1e-3); out[fill] = c_[fill]; need &= ~fill
    for nm_, arr in ((f'g{tid:X}.color.dds', np.clip(out, 0, 255).astype('u1')), (f'g{tid:X}_o.data.dds', a[..., 3])):
        c_ = CACHE / nm_
        if not c_.exists():
            CACHE.mkdir(exist_ok=True); dds.save(c_, arr, None, TEXMAX)
        shutil.copyfile(c_, tdir / nm_)
    MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'materialTag0': 'beamng', 'alphaTest': True, 'alphaRef': 90, 'doubleSided': True, 'castShadows': False,
                 'Stages': [{'baseColorMap': f'{rel}/art/textures/g{tid:X}.color.dds', 'opacityMap': f'{rel}/art/textures/g{tid:X}_o.data.dds', 'roughnessFactor': .9, 'metallicFactor': 0}, {}, {}, {}]}
    return key


def clump(sig, area, radius, n_):
    """one clump shape: sprites as crossed quads standing on z = 0, inside a square of `area` or a disc of `radius`. Normals point up, so both
    sides of a card light alike. Returns (write_lod groups, sprite count, bounding radius)"""
    sp_ = []
    for dens, sprites in sig:
        e_ = dens * (area if radius is None else np.pi * radius ** 2)
        sp_ += [sprites[i] for i in rng.integers(0, len(sprites), int(e_) + (rng.random() < e_ % 1))]
    if not sp_:
        sp_ = [max(sig, key=lambda l_: l_[0])[1][0]]
    G = {}; far = rng.random(len(sp_)) < FAR_SHARE; far[rng.integers(len(sp_))] = True
    for k_, (w0, w1, h0, h1, u0, u1, vb, vt, tid) in enumerate(sp_):
        if radius is None:
            c = (rng.random(2) - .5) * np.sqrt(area)
        else:
            r_, a_ = radius * np.sqrt(rng.random()), rng.random() * 2 * np.pi; c = r_ * np.array([np.cos(a_), np.sin(a_)])
        w_ = rng.uniform(min(w0, w1), max(w0, w1)); h_ = rng.uniform(min(h0, h1), max(h0, h1)) * HEIGHT; yaw = rng.random() * np.pi
        g_ = G.setdefault((material(tid), (0, 1) if far[k_] else (0,)), [[], [], []])
        for d_ in (yaw, yaw + np.pi / 2):
            e = np.array([np.cos(d_), np.sin(d_)]) * w_ / 2; o_ = len(g_[0])
            g_[0] += [[*(c - e), 0], [*(c + e), 0], [*(c + e), h_], [*(c - e), h_]]; g_[1] += [[u0, vb], [u1, vb], [u1, vt], [u0, vt]]
            g_[2] += [o_, o_ + 1, o_ + 2, o_, o_ + 2, o_ + 3]
    groups = []; allp = []
    for (mt, lv), (p_, uv, ix) in G.items():
        p_ = np.array(p_); allp.append(p_)
        groups.append(((mt, lv), p_, np.tile([0., 0, 1], (len(p_), 1)), np.array(uv), np.array(ix)))
    allp = np.concatenate(allp)
    return groups, len(sp_), float(np.linalg.norm(allp.max(0) - allp.min(0)) / 2)


def kind(sig):
    """clump shapes for a signature, made once: (normal variants, edge variants, tile area). Each variant: (item name, sprites, expected sprites)"""
    if sig in KINDS:
        return KINDS[sig]
    h_ = hashlib.sha1(repr(sig).encode()).hexdigest()[:8]; dens = sum(d_ for d_, _ in sig)
    area = float(np.clip(MIN_SPRITES / dens, *TILE)); corn = any(s_[-1] == CORN for _, sp in sig for s_ in sp)
    near, far = (CORN_NEAR, CORN_FAR) if corn else (NEAR, FAR); out = ([], [])
    for e_, (n_, radius) in enumerate(((VARIANTS, None), (EDGE_VARIANTS, EDGE_R))):
        for v_ in range(n_):
            groups, ns, R_ = clump(sig, area, radius, n_)
            s1 = max(int(R_ / (far + R_) * PX), 1); s0 = max(int(R_ / (near + R_) * PX), s1 + 1)
            item = f'fh1_grass_{h_}_{"e" if e_ else "c"}{v_}'
            write_lod(gdir / f'{item}.dae', groups, np.zeros(3), (s0, s1), None)
            managed[item] = {'name': item, 'internalName': item, 'class': 'TSForestItemData', 'annotation': 'NATURE', 'branchAmp': 0, 'detailAmp': 0, 'detailFreq': 0,
                             'mass': 1, 'rigidity': 1, 'shapeFile': f'{rel}/art/forest/grass/{item}.dae', 'windScale': 0}
            out[e_].append((item, ns, dens * (area if radius is None else np.pi * radius ** 2)))
            STATS['shapes'] += 1; STATS['shape tris'] += 4 * ns
    KINDS[sig] = out + (area,)
    return KINDS[sig]


def put(item, p, n, ns):
    """a forest item at p on ground of normal n: random yaw, leaning with the ground up to TILT. Forest files list a matrix's axes as rows,
    measured on stock Italy, Utah and West Coast items against their terrain"""
    t_ = np.degrees(np.arccos(np.clip(n[2], -1, 1))); k_ = np.cross([0, 0, 1.], n); s_ = np.linalg.norm(k_)
    a_ = np.radians(min(t_, TILT)); R = np.eye(3)
    if s_ > 1e-6:
        k_ /= s_; K = np.array([[0, -k_[2], k_[1]], [k_[2], 0, -k_[0]], [-k_[1], k_[0], 0]]); R = np.eye(3) + np.sin(a_) * K + (1 - np.cos(a_)) * K @ K
    y_ = rng.random() * 2 * np.pi; Rz = np.array([[np.cos(y_), -np.sin(y_), 0], [np.sin(y_), np.cos(y_), 0], [0, 0, 1]])
    M = R @ Rz; sc = float(rng.uniform(.9, 1.1)); z_ = SINK + .5 * np.tan(np.radians(max(t_ - TILT, 0)))      # leaning less than the ground leaves the low side above it
    ROWS.setdefault(item, []).append({'ctxid': 0, 'pos': [round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2] - z_), 3)],
                                      'rotationMatrix': [round(float(q), 5) for q in M.T.ravel()], 'scale': round(sc, 3), 'type': item})
    STATS['items'] += 1; STATS['sprites'] += ns; STATS['tris'] += 4 * ns


def locate(q, P3, T, tree):
    """the triangle under each 2D point (-1 if none) and the height there"""
    k_ = min(8, len(T)); _, cand = tree.query(q, k_); cand = cand.reshape(len(q), -1)
    hit = np.full(len(q), -1); z = np.zeros(len(q))
    for j in range(cand.shape[1]):
        t = T[cand[:, j]]; A, Bv, C = P3[t[:, 0]], P3[t[:, 1]], P3[t[:, 2]]
        d = (Bv[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - Bv[:, 0]) * (A[:, 1] - C[:, 1]); d = np.where(np.abs(d) < 1e-12, 1e-12, d)
        l1 = ((Bv[:, 1] - C[:, 1]) * (q[:, 0] - C[:, 0]) + (C[:, 0] - Bv[:, 0]) * (q[:, 1] - C[:, 1])) / d
        l2 = ((C[:, 1] - A[:, 1]) * (q[:, 0] - C[:, 0]) + (A[:, 0] - C[:, 0]) * (q[:, 1] - C[:, 1])) / d; l3 = 1 - l1 - l2
        ins = (hit < 0) & (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        hit[ins] = cand[ins, j]; z[ins] = (l1 * A[:, 2] + l2 * Bv[:, 2] + l3 * C[:, 2])[ins]
    return hit, z


MATS = {}; KINDS = {}; ROWS = {}; STATS = {k: 0 for k in ('sets', 'crowd sets', 'shapes', 'shape tris', 'items', 'edge items', 'sprites', 'tris')}
names = [n for n, _ in json.load(open('../list.json')) if n.endswith('.pgeo') and not n.endswith('.lc')]
for n in names:
    b0 = open('../x/' + n, 'rb').read(0x90)
    if b'Grass_Ungrouped' not in b0[0x50:0x90]:
        continue
    g = pgrass.parse(n)
    if g is None or not len(g['T']):
        continue
    P = g['P']; Pb = np.stack([P[:, 0] - CX, P[:, 2] + CZ, P[:, 1]], 1)       # .pgeo z is minus mesh z, so g2b's -(z - CZ) is z + CZ here
    if Pb[:, 0].max() < -HALF or Pb[:, 0].min() > HALF or Pb[:, 1].max() < -HALF or Pb[:, 1].min() > HALF:
        continue
    tb = pgrass.tables(g); tids = [IDX[i][0] for i in g['tex']]
    if CROWD in tids:
        STATS['crowd sets'] += 1; continue
    STATS['sets'] += 1; T = g['T']
    N_ = np.cross(Pb[T[:, 1]] - Pb[T[:, 0]], Pb[T[:, 2]] - Pb[T[:, 0]]); A = np.linalg.norm(N_, axis=1) / 2
    N_ = N_ / np.maximum(2 * A[:, None], 1e-12); N_[N_[:, 2] < 0] *= -1
    slope = np.degrees(np.arccos(np.clip(N_[:, 2], -1, 1)))
    keep = A * g['wt'][T].mean(1) * np.clip((SLOPE[1] - slope) / (SLOPE[1] - SLOPE[0]), 0, 1)
    e_ = np.sort(np.r_[T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]], 1); u_, c_ = np.unique(e_, axis=0, return_counts=True); edges = u_[c_ == 1]      # the patch outline
    pts = [Pb[a_, :2] + (Pb[b_, :2] - Pb[a_, :2]) * t_ for a_, b_ in edges for t_ in np.linspace(0, 1, max(int(np.linalg.norm(Pb[b_, :2] - Pb[a_, :2]) / .2), 1) + 1)]
    outline = cKDTree(np.array(pts)); gid = g['gid'][T[:, 0]]
    for gi, lids in enumerate(tb['groups']):
        m = np.where((gid == gi) & (keep > 0))[0]
        if not len(m):
            continue
        sig = tuple(sorted((round(tb['layers'][l][0], 3), tuple(tuple(round(x, 3) for x in tb['sprites'][s_][:8]) + (tids[tb['sprites'][s_][10]],) for s_ in tb['layers'][l][8])) for l in lids))
        norm, edge, area = kind(sig)
        cnt = rng.poisson(keep[m] / area); ti = np.repeat(m, cnt)
        if not len(ti):
            continue
        r1, r2 = rng.random(len(ti)), rng.random(len(ti)); s_ = np.sqrt(r1)
        q = Pb[T[ti, 0]] * (1 - s_)[:, None] + Pb[T[ti, 1]] * (s_ * (1 - r2))[:, None] + Pb[T[ti, 2]] * (s_ * r2)[:, None]
        d_, _ = outline.query(q[:, :2]); inner = d_ >= .6 * np.sqrt(area)
        for i in np.where(inner)[0]:
            if np.abs(q[i, :2]).max() < HALF - 1:
                it, ns, _ = norm[rng.integers(len(norm))]; put(it, q[i], N_[ti[i]], ns)
        # near the outline, each clump becomes small ones over its square, kept only where their centres are EDGE_R inside the patch
        ce = q[~inner]
        if not len(ce):
            continue
        k_ = int(round(area / (np.pi * EDGE_R ** 2))); sub = (np.repeat(ce[:, :2], k_, 0) + (rng.random((len(ce) * k_, 2)) - .5) * np.sqrt(area))
        sub = sub[outline.query(sub)[0] >= EDGE_R]
        if not len(sub):
            continue
        Tg = T[m]; tree = cKDTree(Pb[Tg].mean(1)[:, :2]); hit, z = locate(sub, Pb, Tg, tree)
        for j in np.where(hit >= 0)[0]:
            it, ns, ex = edge[rng.integers(len(edge))]
            if np.abs(sub[j]).max() < HALF - 1 and rng.random() < ex / max(ns, 1):       # an edge clump rounded up to a whole sprite is placed less often
                put(it, np.r_[sub[j], z[j]], N_[m[hit[j]]], ns); STATS['edge items'] += 1

for item, r_ in ROWS.items():
    (fdir / f'{item}.forest4.json').write_text(''.join(json.dumps(o) + '\n' for o in r_))
for item in [k for k in managed if k.startswith('fh1_grass_') and k not in ROWS]:      # shapes no item uses
    del managed[item]; (gdir / f'{item}.dae').unlink(missing_ok=True)
(level / 'art' / 'forest' / 'managedItemData.json').write_text(json.dumps(managed, indent=2))
(gdir / 'main.materials.json').write_text(json.dumps(MATS, indent=1))
size = sum(f.stat().st_size for f in fdir.glob('fh1_grass_*.forest4.json')) + sum(f.stat().st_size for f in gdir.iterdir()) + sum(f.stat().st_size for f in tdir.glob('g*.dds') if re.match(r'g[0-9A-F]+(_o)?\.', f.name))
print(LVL, STATS, 'item types', len(ROWS), 'materials', len(MATS), 'sprites per item', round(STATS['sprites'] / max(STATS['items'], 1), 1), f'on disk {size / 1e6:.1f} MB')
if (level / 'art' / 'textures_ungraded').exists():       # grade.py already ran: grade the new textures too. It grades from ungraded copies, so rerunning is safe
    subprocess.run([sys.executable, 'grade.py', LVL], check=True)
