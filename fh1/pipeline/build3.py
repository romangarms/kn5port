"""Red Rock chunk, second pass: FH1's own meshes, textures and placed models in a BeamNG level."""
import sys, os, re, json, math, pickle, struct, time, glob, collections
import numpy as np
from collections import defaultdict
from pathlib import Path
from PIL import Image
from numba import njit
from scipy import ndimage as ni
import rmb, tex
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng, donor
from kn5port.level import _fmt, safe
from kn5port.terrain import pyramid_fill

Image.MAX_IMAGE_PIXELS = None
LVL = 'fh1_redrock'; N = 2048; SQ = 2.0; CX, CZ = -6000.0, 4250.0; CELL = 500.0; TEXMAX = 1024
HALF = N * SQ / 2; X0, X1, Z0, Z1 = CX - HALF, CX + HALF, CZ - HALF, CZ + HALF
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(beamng.find_mods()) / 'unpacked' / LVL
level = root / 'levels' / LVL; rel = f'/levels/{LVL}'
inst = beamng.find_install()
F = pickle.load(open('../cat2.pkl', 'rb')); MT = pickle.load(open('../meshtab.pkl', 'rb'))
IDX, SHADERS = pickle.load(open('../texshader.pkl', 'rb')); DRAW = np.load('../mt.npy')

NOUSE = re.compile(r'occluder|shadow|underground|reflect|^Ref_|proxy|collision|_CAGE', re.I)
TERR = re.compile(r'Terrain|^TERR_CLRD|PATCH|MIDDIST|UberLOD|Area\d+[AB]?_LOD', re.I)
FAR = re.compile(r'TERR_CUBE|MIDDIST|UberLOD|PATCH', re.I)      # coarser ground: only shown where nothing better covers it
FARQ = []


def lod(n):
    m = re.search(r'_LOD(\d+)', n)
    return int(m.group(1)) if m else 0


def g2b(p):
    return np.stack([p[:, 0] - CX, -(p[:, 2] - CZ), p[:, 1]], 1)


def n2b(n):
    return np.stack([n[:, 0], -n[:, 2], n[:, 1]], 1)


def materials(b, p):
    """[(shader index, [slots])] for a file, by material index"""
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


class Textures:
    def __init__(self):
        self.d = level / 'art' / 'textures'; self.d.mkdir(parents=True, exist_ok=True)
        self.info = {}; self.missing = collections.Counter()

    def get(self, tid):
        if tid in self.info:
            return self.info[tid]
        t = tex.bix(tid); r = None
        if t and t['fmt'] != 0x71:
            r = tex.load(tid)
        if r is None or r[0] is None:
            self.missing[tid] += 1; self.info[tid] = None
            return None
        a = r[0]; im = Image.fromarray(a)
        if max(im.size) > TEXMAX:
            im = im.resize((min(im.size[0], TEXMAX), min(im.size[1], TEXMAX)), Image.LANCZOS)
        name = f't{tid:X}'
        im.convert('RGB').save(self.d / f'{name}.color.png')
        al = a[..., 3]; cut = float((al < 128).mean())
        if cut > 0.002:
            im.getchannel('A').convert('RGB').save(self.d / f'{name}_o.data.png')
        rgb = a[..., :3].reshape(-1, 3).mean(0)
        self.info[tid] = dict(name=name, rgb=rgb, cut=cut, src=a)
        return self.info[tid]


TX = Textures()
MATS = {}


def material(tid, ground=None, alpha=False, translucent=False, two=False):
    t = TX.get(tid)
    if t is None:
        return None
    use_a = (alpha or translucent) and t['cut'] > 0.002
    key = f"fh1_{t['name']}" + ('_a' if use_a else '') + ('_t' if translucent else '') + (f'_{ground}' if ground else '')
    if key not in MATS:
        st = {'baseColorMap': f"{rel}/art/textures/{t['name']}.color.png", 'roughnessFactor': .9, 'metallicFactor': 0}
        m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng'}
        if use_a:
            st['opacityMap'] = f"{rel}/art/textures/{t['name']}_o.data.png"
            if translucent:
                m['translucent'] = True; m['translucentZWrite'] = False
            else:
                m['alphaTest'] = True; m['alphaRef'] = 90
        if two or use_a:
            m['doubleSided'] = True
        if ground:
            m['groundType'] = ground
        MATS[key] = m
    return key


def write_dae(path, groups, origin):
    """kn5port's writer plus a second UV set: groups are (material, pos, normal, uv, idx, uv2)."""
    mats = sorted({g[0] for g in groups}); out = []; w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
    w('<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>\n<library_effects>\n')
    for m in mats:
        w(f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>0.8 0.8 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>\n')
    w('</library_effects>\n<library_materials>\n')
    for m in mats:
        w(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>\n')
    w('</library_materials>\n<library_geometries>\n'); nodes = []
    for gi, g_ in enumerate(groups):
        mat, pos, nrm, uv, idx = g_[:5]; uv1 = g_[5] if len(g_) > 5 and g_[5] is not None else uv
        g = f'g{gi}'; n = len(pos); local = pos - origin
        w(f'<geometry id="{g}" name="{g}"><mesh>\n')
        for sid, arr, fmt, params in ((f'{g}-p', local, '%.4f', 'XYZ'), (f'{g}-n', nrm, '%.3f', 'XYZ'),
                                      (f'{g}-t', np.c_[uv[:, 0], 1 - uv[:, 1]], '%.5f', 'ST'), (f'{g}-u', np.c_[uv1[:, 0], 1 - uv1[:, 1]], '%.5f', 'ST')):
            w(f'<source id="{sid}"><float_array id="{sid}-a" count="{arr.size}">'); w(_fmt(arr, fmt))
            w(f'</float_array><technique_common><accessor source="#{sid}-a" count="{n}" stride="{len(params)}">')
            w(''.join(f'<param name="{c}" type="float"/>' for c in params)); w('</accessor></technique_common></source>\n')
        w(f'<vertices id="{g}-v"><input semantic="POSITION" source="#{g}-p"/></vertices>\n')
        w(f'<triangles material="{mat}" count="{len(idx) // 3}"><input semantic="VERTEX" source="#{g}-v" offset="0"/><input semantic="NORMAL" source="#{g}-n" offset="0"/>'
          f'<input semantic="TEXCOORD" source="#{g}-t" offset="0" set="0"/><input semantic="TEXCOORD" source="#{g}-u" offset="0" set="1"/><p>')
        w(' '.join(map(str, idx.tolist()))); w('</p></triangles>\n</mesh></geometry>\n')
        nodes.append(f'<node id="n{gi}" name="{safe(mat)}_c{gi}_mesh"><instance_geometry url="#{g}"><bind_material><technique_common>'
                     f'<instance_material symbol="{mat}" target="#{mat}"/></technique_common></bind_material></instance_geometry></node>\n')
    w('</library_geometries>\n<library_visual_scenes><visual_scene id="Scene" name="Scene">\n'); out.extend(nodes)
    w('</visual_scene></library_visual_scenes>\n<scene><instance_visual_scene url="#Scene"/></scene>\n</COLLADA>\n')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(''.join(out), encoding='utf-8')


def blend_material(tids, masktid, ground):
    """FH1 h_blnd: layer 0 plus layers masked by the red and green channels, on the second UV set"""
    key = 'fh1_b_' + '_'.join(f'{t:X}' for t in tids) + f'_m{masktid:X}' + (f'_{ground}' if ground else '')
    if key in MATS:
        return key
    info = [TX.get(t) for t in tids]; m_ = TX.get(masktid)
    if any(i is None for i in info) or m_ is None:
        return None
    for ch, nm in ((0, 'r'), (1, 'g')):
        p_ = TX.d / f"{m_['name']}_{nm}.data.png"
        if not p_.exists():
            Image.fromarray(m_['src'][..., ch]).convert('RGB').save(p_)
    st = [{'baseColorMap': f"{rel}/art/textures/{info[0]['name']}.color.png", 'roughnessFactor': .92, 'metallicFactor': 0}]
    for k_, i_ in enumerate(info[1:]):
        st.append({'baseColorMap': f"{rel}/art/textures/{i_['name']}.color.png", 'roughnessFactor': .92, 'metallicFactor': 0,
                   'opacityMap': f"{rel}/art/textures/{m_['name']}_{'rg'[k_]}.data.png", 'opacityMapUseUV': 1})
    while len(st) < 4:
        st.append({})
    m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st, 'activeLayers': len(info), 'materialTag0': 'beamng', 'translucentBlendOp': 'None'}
    if ground:
        m['groundType'] = ground
    MATS[key] = m
    return key


def slot_tex(mesh_no, slots, order=None):
    """texture ids of the material's colour slots, skipping unused, missing and normal-map entries"""
    ids = MT[mesh_no][0]; out = []
    for s in (order if order else slots):
        if s < len(ids):
            tid = IDX[ids[s]][0]; t = tex.bix(tid)
            if t and t['fmt'] != 0x71:
                out.append(tid)
    return out


def uv_sets(m):
    a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
    r0 = np.frombuffer(a[:, 16:20].tobytes(), '>i2').reshape(-1, 2).astype('f8') / 32767
    r1 = np.frombuffer(a[:, 20:24].tobytes(), '>i2').reshape(-1, 2).astype('f8') / 32767 if m['sd'] >= 24 else None
    return r0, r1


def role_ground(s):
    s = s.lower()
    if 'sediment' in s or 'rock' in s: return 'ROCK'
    if 'sand' in s: return 'SAND'
    if 'mud' in s or 'dirt' in s: return 'DIRT'
    if 'grass' in s: return 'GRASS'
    return 'DIRT'


@njit(cache=True)
def raster(H, T):
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
                if not z <= H[j, i]:
                    H[j, i] = z


def togrid(p):
    return np.stack([(p[:, 0] - X0) / SQ, (p[:, 2] - Z0) / SQ * -1 + N, p[:, 1]], 1)


cells = defaultdict(lambda: defaultdict(list)); stats = collections.Counter(); surf = collections.Counter(); start_ = t0 = time.time()


def add(cat, mat, p, n, uv, T, lift=0.0, uv1=None):
    if not len(T):
        return
    b = g2b(p); nb = n2b(n)
    if lift:
        b = b + np.array([0, 0, lift])
    c = b[T].mean(1); ins = (np.abs(c[:, :2]) < HALF - 4).all(1); T = T[ins]; c = c[ins]
    if not len(T):
        return
    key = np.floor((c[:, :2] + HALF) / CELL).astype(int); k = key[:, 0] * 1000 + key[:, 1]
    for u in np.unique(k):
        sel = T[k == u]; idx, inv = np.unique(sel, return_inverse=True)
        cells[(int(u), cat)][mat].append((b[idx], nb[idx], uv[idx], inv.reshape(-1, 3).astype(np.int64), uv1[idx] if uv1 is not None else uv[idx]))
    stats[cat] += len(T)


# ---- world meshes
sel = [c for c in F if c['mx'][0] > X0 and c['mn'][0] < X1 and c['mx'][2] > Z0 and c['mn'][2] < Z1
       and max(c['mx'][0] - c['mn'][0], c['mx'][2] - c['mn'][2]) < 6000 and not NOUSE.search(c['name'])]
best = defaultdict(lambda: 99)
for c in sel:
    k = re.sub(r'_LOD\d+', '', c['name']); best[k] = min(best[k], lod(c['name']))
print(len(sel), 'meshes in box')
Hs = [np.full((N, N), np.nan, 'f8') for _ in range(4)]; Road = np.full((N, N), np.nan, 'f8'); Cov = np.full((N, N), np.nan, 'f8'); Rock = np.full((N, N), np.nan, 'f8')
byfile = defaultdict(list)
for c in sel:
    byfile[c['f']].append(c)
for f, cs in byfile.items():
    b = open('../x/' + f, 'rb').read(); P = rmb.parse(b); M = materials(b, P); no = int(f.split('.')[1])
    if len(M) < 1 + max((s['ints'][2] for m in P['meshes'] for s in m['secs']), default=0):
        stats['file_short_mats'] += 1
    for c in cs:
        m = P['meshes'][c['mi']]; n = c['name']; l = lod(n)
        if m['sd'] < 20:
            stats['stride16'] += 1
            continue
        p, nr, _ = rmb.verts(m); r0, r1 = uv_sets(m)
        cube = bool(FAR.search(n))
        isterr = (bool(TERR.search(n)) or cube) and not re.search(r'_rocks?(_|$)|armco|barrier|_road', n, re.I)
        g = togrid(p)
        if isterr:
            tier = 3 if cube else min(l, 2)
            for s in m['secs']:
                T = rmb.tris(s['ib'], m['nv'])
                if len(T):
                    raster(Hs[tier], g[T])
            if cube:
                if 'TERR_CUBE' not in n and l <= best[re.sub(r'_LOD\d+', '', n)]:
                    FARQ.append((0 if 'PATCH' in n.upper() else 1 if 'MIDDIST' in n.upper() else 2, no, M, m, p, nr, r0, r1, g))
                continue
        if l > best[re.sub(r'_LOD\d+', '', n)]:
            stats['lod_dropped'] += 1
            continue
        for s in m['secs']:
            T = rmb.tris(s['ib'], m['nv'])
            if not len(T) or s['ints'][2] >= len(M):
                stats['sec_nomat'] += 1
                continue
            si, slots = M[s['ints'][2]]
            fx = SHADERS[MT[no][1][si]] if si < len(MT[no][1]) else ''
            f4 = s['fl'][4:8]; uv = r0 * np.array(f4[2:]) * 0.49999
            decal = 'decal' in fx; opac = bool(re.search(r'opac|mask|tree|bunting', fx)) and not decal
            road = fx.startswith('h_road') and not re.search(r'sign|barrier|wall|armco', n, re.I)
            ts = slot_tex(no, [x for x in slots if x < 96])
            if not ts:
                stats['sec_notex'] += 1
                continue
            if isterr:
                raster(Cov, g[T])
                layers = []
                ids = MT[no][0]
                for q in (0, 1, 5):
                    if q < len(slots) and slots[q] < len(ids):
                        tid = IDX[ids[slots[q]]][0]
                        if tex.bix(tid) and tex.bix(tid)['fmt'] != 0x71:
                            layers.append(tid)
                mask = None
                if fx.startswith('h_blnd') and r1 is not None and len(slots) > 2 and slots[2] < len(ids):
                    mt = tex.bix(IDX[ids[slots[2]]][0])
                    if mt and mt['fmt'] == 0x52:
                        mask = TX.get(IDX[ids[slots[2]]][0])
                gnd = role_ground(s['name'])
                if mask is not None and len(layers) > 1:
                    mname = blend_material(layers[:3], IDX[ids[slots[2]]][0], gnd)
                    add('col', mname, p, nr, uv, T, uv1=(r1 + 1) / 2); surf[f'terrain_blend:{gnd}'] += len(T)
                else:
                    add('col', material(layers[0] if layers else ts[0], gnd), p, nr, uv, T); surf[f'terrain:{gnd}'] += len(T)
                continue
            tid = ts[0]; info = TX.get(tid)
            if info is None:
                stats['sec_notex'] += 1
                continue
            if decal:
                add('vis', material(tid, translucent=True), p, nr, uv, T, lift=.02); surf['decal'] += len(T)
            elif road:
                raster(Road, g[T])
                rgb = info['rgb']; brown = rgb[0] - rgb[2] > 14
                gnd = 'DIRT' if brown else 'ASPHALT'
                add('col', material(tid, gnd), p, nr, uv, T); surf[f'road:{gnd}'] += len(T)
            elif re.search(r'cable|tether', n, re.I):
                add('vis', material(tid, alpha=opac), p, nr, uv, T); surf['cable'] += len(T)
            elif re.search(r'armco|barrier|fence|rail', n, re.I):
                add('col', material(tid, 'METAL', alpha=opac), p, nr, uv, T); surf['barrier'] += len(T)
            elif re.search(r'rock|wall|cliff', n, re.I) or 'vblnd' in fx:
                add('col', material(tid, 'ROCK', alpha=opac), p, nr, uv, T); surf['rock'] += len(T); raster(Rock, g[T])
            else:
                add('col', material(tid, alpha=opac), p, nr, uv, T); surf['prop'] += len(T)
blocked = ni.binary_dilation(np.isfinite(Road) | np.isfinite(Rock), iterations=4)
shown = np.isfinite(Cov)
BARY = np.array([[a_, b_, 8 - a_ - b_] for a_ in range(9) for b_ in range(9 - a_)], 'f8') / 8
for tier_ in (0, 1, 2):
    new_ = np.full((N, N), np.nan, 'f8')
    for t_, no, M, m, p, nr, r0, r1, g in FARQ:
        if t_ != tier_:
            continue
        ids = MT[no][0]
        for s in m['secs']:
            T = rmb.tris(s['ib'], m['nv'])
            if not len(T) or s['ints'][2] >= len(M):
                continue
            Q_ = np.einsum('ij,kjl->kil', BARY, g[T][:, :, :2])
            ci = np.clip(Q_[..., 0].astype(int), 0, N - 1); cj = np.clip(Q_[..., 1].astype(int), 0, N - 1)
            T = T[~(shown | blocked)[cj, ci].any(1)]
            if not len(T):
                continue
            si, slots = M[s['ints'][2]]; fx = SHADERS[MT[no][1][si]] if si < len(MT[no][1]) else ''
            ts = slot_tex(no, [x for x in slots if x < 96])
            if not ts:
                continue
            f4 = s['fl'][4:8]; uv = r0 * np.array(f4[2:]) * 0.49999; gnd = role_ground(s['name']); layers = []
            for q in (0, 1, 5):
                if q < len(slots) and slots[q] < len(ids):
                    tid = IDX[ids[slots[q]]][0]
                    if tex.bix(tid) and tex.bix(tid)['fmt'] != 0x71:
                        layers.append(tid)
            mask = None
            if fx.startswith('h_blnd') and r1 is not None and len(slots) > 2 and slots[2] < len(ids):
                mt_ = tex.bix(IDX[ids[slots[2]]][0])
                if mt_ and mt_['fmt'] == 0x52:
                    mask = TX.get(IDX[ids[slots[2]]][0])
            if mask is not None and len(layers) > 1:
                add('col', blend_material(layers[:3], IDX[ids[slots[2]]][0], gnd), p, nr, uv, T, uv1=(r1 + 1) / 2)
            else:
                add('col', material(layers[0] if layers else ts[0], gnd), p, nr, uv, T)
            surf[f'far{tier_}'] += len(T)
            raster(new_, g[T]); raster(Cov, g[T])
    shown = np.isfinite(Cov)
    print('far tier', tier_, 'shown now', float(shown.mean()))
print('statics', dict(stats), dict(surf), f'{time.time() - t0:.0f}s')
print('sections by road surface', {k: v for k, v in surf.items() if k.startswith('road')})

# ---- placed models (forest)
names = [n for n, _ in json.load(open('../list.json')) if n.endswith('.pgeo') and not n.endswith('.lc')]


def half(v):
    s = -1.0 if v & 0x8000 else 1.0; e = (v >> 10) & 31; m = v & 1023
    return 0.0 if e == 0 else s * (1 + m / 1024) * 2.0 ** (e - 15)


def s11(v):
    return (v - 2048 if v >= 1024 else v) / 1023.0


def s10(v):
    return (v - 1024 if v >= 512 else v) / 511.0


def axis(w):
    return np.array([s11(w & 2047), s11((w >> 11) & 2047), s10((w >> 22) & 1023)])


def groups_of(w, M, total):
    """model records (flag, k, a, n, lods): model k owns n instances, in model order; empty tail models may have no record"""
    G = []; i = 1
    while i < len(w) - 4 and len(G) < M:
        if w[i] <= 1 and w[i - 1] == 0 and w[i + 1] == len(G) and w[i + 2] < 100000 and w[i + 3] <= total and 1 <= w[i + 4] <= 8:
            G.append(w[i + 3]); i += 5
            if sum(G) == total:
                return G + [0] * (M - len(G))
        else:
            i += 1
    return None


EVENT = re.compile(r'festival|multiplayer|street|showcase|activation|crowd|_track_|testfest', re.I)
LAYERS_SEEN = collections.Counter()


def read_pgeo(n):
    b = open('../x/' + n, 'rb').read()
    nm_ = re.search(rb'(models_proc|Models_Ungrouped|AModels_Ungrouped)[A-Za-z0-9_]*', b[0x50:0xc0])
    if nm_:
        key_ = re.sub(r'_\d+(_\d+)?$', '', nm_.group().decode())
        bb_ = struct.unpack('>8f', b[0x10:0x30])
        if not (bb_[4] < X0 or bb_[0] > X1 or -bb_[2] < Z0 or -bb_[6] > Z1):
            LAYERS_SEEN[('skip ' if EVENT.search(key_) else 'keep ') + key_] += 1
        if EVENT.search(key_):
            return None
    if b[:4] != b'OEGP' or struct.unpack_from('>I', b, 4)[0] != 42 or b'odels' not in b[0x50:0xc0]:
        return None
    size, M = struct.unpack_from('>II', b, 0x50); bb = struct.unpack('>8f', b[0x10:0x30])
    if size == 0 and b'models_proc' in b[0x90:0xc0]:       # placed sets: full transforms, 96 bytes each
        if not 1 <= M <= 16:
            return None
        q_ = 0xd0 + 0x40 * M; meshes = []
        for i in range(M):
            e_ = struct.unpack_from('>6I', b, 0xd0 + 0x40 * i + 32)
            meshes.append(int(DRAW['t'][struct.unpack_from('>I', b, q_)[0]])); q_ += 4 * max(e_[1] + (e_[3] if e_[3] < 8 else 0), 1)
        w = struct.unpack_from('>%dI' % min((len(b) - q_) // 4, 200), b, q_)
        G = []
        for i in range(1, len(w) - 4):            # (k, 0, count, 0, 1) or the older (1, k, count, 0, 1)
            if len(G) == M:
                break
            if w[i - 1] == 0 and w[i] == len(G) and 0 < w[i + 1] < 100000 and w[i + 2] == 0 and w[i + 3] == 1:
                G.append(w[i + 1])
            elif w[i] == 1 and w[i + 1] == len(G) and 0 < w[i + 2] + w[i + 3] < 100000 and 1 <= w[i + 4] <= 8:
                G.append(w[i + 2] + w[i + 3])
        tot = sum(G); A = None
        start = len(b) - 96 * tot - 32 * len(G)        # every model's block ends in a 32 byte trailer
        if len(G) == M and start >= q_:
            parts_ = []; p_ = start
            for c_n in G:
                parts_.append(np.frombuffer(b, '>f4', c_n * 24, p_).reshape(-1, 24).astype('f8')); p_ += 96 * c_n + 32
            c_ = np.concatenate(parts_)
            if np.abs(c_[:, 15] - 1).max() < .01 and (np.linalg.norm(c_[:, :3], axis=1) > .01).all():
                A = c_
        if A is None:
            stats['proc_bad'] += 1
            print('proc unreadable', n, M, G)
            return None
        stats['proc_ok'] += 1
        return 'proc', A, meshes, G
    if M < 1 or M > 64 or size < 40 or size % 40 or size >= len(b):
        stats['pgeo_badhdr'] += 1
        return None
    o = 0x80 + 0x44 * M; start = len(b) - size
    w = struct.unpack_from('>%dI' % ((start - o) // 4), b, o)
    G = groups_of(w, M, size // 40)
    if G is None:
        stats['pgeo_groupmismatch'] += 1
        return None
    W = [struct.unpack_from('>f', b, 0x80 + 0x40 * i + 16)[0] - struct.unpack_from('>f', b, 0x80 + 0x40 * i)[0] for i in range(M)]
    A = np.frombuffer(b, '>u4', size // 4, start).reshape(-1, 10).astype(np.int64)
    meshes = []; q_ = 0x80 + 0x40 * M
    for i in range(M):           # models carry one or two LOD ids, as their header entry says
        e_ = struct.unpack_from('>6I', b, 0x80 + 0x40 * i + 32)
        meshes.append(int(DRAW['t'][struct.unpack_from('>I', b, q_)[0]])); q_ += 4 * max(e_[1] + (e_[3] if e_[3] < 8 else 0), 1)
    return bb, A, meshes, list(zip(G, W))


files = []
for n in names:
    b = open('../x/' + n, 'rb').read(0x30)
    if b[:4] != b'OEGP':
        continue
    bb = struct.unpack('>8f', b[0x10:0x30])
    if bb[4] < X0 or bb[0] > X1 or -bb[2] < Z0 or -bb[6] > Z1:
        continue
    q = read_pgeo(n)
    if q is not None:
        files.append(q)
print('layers', LAYERS_SEEN.most_common(30))
print('pgeo files', len(files), {k: v for k, v in stats.items() if k.startswith(('pgeo', 'proc'))}, 'instances in them', sum(len(f[1]) for f in files))

fdir = level / 'forest'; fdir.mkdir(parents=True, exist_ok=True); adir = level / 'art' / 'forest' / 'models'; adir.mkdir(parents=True, exist_ok=True)
pal = donor.analyze(inst, 'utah')
tmpl = dict(next(iter(pal['vegetation'].values()))['def']); tmpl.pop('persistentId', None)
def game_rot(Rg):
    return S_ @ Rg if np.linalg.det(Rg) < 0 else S_ @ Rg @ S_


defs = {}; rows = defaultdict(list); S_ = np.diag([1., 1., -1.]); B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]])


def model_item(no):
    """export one mesh file as a forest shape; returns (item name, height, width) or None"""
    if no in defs:
        return defs[no]
    defs[no] = None
    fs = f'coloradoout.{no:05d}.rmb.bin'
    if not os.path.exists('../x/' + fs):
        return None
    b = open('../x/' + fs, 'rb').read(); P = rmb.parse(b); M = materials(b, P); groups = []; lo = np.full(3, np.inf); hi = -lo; label = ''
    ids = MT[no][0]
    for m in P['meshes']:
        label = label or m['name']
        a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
        p = np.frombuffer(a[:, :12].reshape(-1, 3, 4)[:, :, ::-1].tobytes(), '<f4').reshape(-1, 3).astype('f8')
        card = m['sd'] < 20
        if card:
            r0 = np.frombuffer(a[:, 12:16].tobytes(), '>i2').reshape(-1, 2).astype('f8')
            nr = np.zeros_like(p)
        else:
            _, nr, _ = rmb.verts(m); r0, _ = uv_sets(m)
        pb = np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1); nb = n2b(nr)
        for s in m['secs']:
            T = rmb.tris(s['ib'], m['nv'])
            if not len(T):
                continue
            mi = s['ints'][2]; ts = slot_tex(no, [x for x in M[mi][1] if x < 96]) if mi < len(M) else []
            if not ts:
                q = IDX[ids[min(mi, len(ids) - 1)]][0] if ids else None
                ts = [q] if q is not None and tex.bix(q) and tex.bix(q)['fmt'] != 0x71 else []
            if not ts:
                continue
            f4 = s['fl'][4:8]
            mt = material(ts[0], alpha=True, two=True)
            if mt is None:
                continue
            if card:
                uv = r0 * np.array(f4[2:]) / 65535
                e1 = pb[T[:, 1]] - pb[T[:, 0]]; e2 = pb[T[:, 2]] - pb[T[:, 0]]; fn_ = np.cross(e1, e2)
                fn_ /= np.maximum(np.linalg.norm(fn_, axis=1, keepdims=True), 1e-9); nb = nb.copy(); nb[T.ravel()] = fn_.repeat(3, 0)
            else:
                uv = r0 * np.array(f4[2:]) * 0.49999
            idx, inv = np.unique(T, return_inverse=True)
            groups.append((mt, pb[idx], nb[idx], uv[idx], inv.astype(np.int64)))
            lo = np.minimum(lo, pb.min(0)); hi = np.maximum(hi, pb.max(0))
    if not groups:
        return None
    item = re.sub(r'[^A-Za-z0-9_]', '_', label) + f'_{no}'
    write_dae(adir / f'{item}.dae', groups, np.zeros(3))
    d = dict(tmpl); d.update({'name': item, 'internalName': item, 'shapeFile': f'{rel}/art/forest/models/{item}.dae', 'branchAmp': 0, 'detailAmp': 0, 'detailFreq': 0, 'windScale': 0})
    d.pop('persistentId', None)
    defs[no] = dict(item=item, h=float(hi[2] - lo[2]), w=float(hi[0] - lo[0]), d=d, groups=groups)
    return defs[no]


for bb, A, meshes, G in files:
    if isinstance(bb, str):
        o_ = 0
        for k_, cnt in enumerate(G):
            no = meshes[k_]
            for i in range(o_, o_ + cnt):
                r = A[i]; x, y, z = r[12], r[13], -r[14]
                if not (X0 < x < X1 and Z0 < z < Z1):
                    continue
                it = model_item(no)
                if it is None:
                    stats['inst_nomesh'] += 1
                    continue
                ln = np.linalg.norm(r[0:3]); Rg = np.c_[r[0:3], r[4:7], r[8:11]]
                Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6); Rm = B @ game_rot(Rg) @ B.T
                if np.linalg.det(Rm) < 0:
                    Rm[:, 0] *= -1
                sc = float(np.clip(r[16], .2, 5)) if r[16] > .01 else float(np.clip(ln, .2, 5))
                rows[it['item']].append(dict(ctxid=0, pos=[round(float(v), 3) for v in g2b(np.array([[x, y, z]]))[0]], rotationMatrix=[round(float(v), 5) for v in Rm.ravel()], scale=round(sc, 3), type=it['item']))
                stats['proc_instances'] += 1
            o_ += cnt
        continue
    a = A[:, 0]
    x = bb[0] + (a & 2047) / 2047 * (bb[4] - bb[0]); zp = bb[2] + ((a >> 11) & 2047) / 2047 * (bb[6] - bb[2]); y = bb[1] + ((a >> 22) & 1023) / 1023 * (bb[5] - bb[1])
    o_ = 0
    for k_, (cnt, W) in enumerate(G):
        no = meshes[k_]
        for i in range(o_, o_ + cnt):
            z = -zp[i]
            if not (X0 < x[i] < X1 and Z0 < z < Z1):
                continue
            it = model_item(no)
            if it is None:
                stats['inst_nomesh'] += 1
                continue
            Y = axis(int(A[i, 1])); X = axis(int(A[i, 2]))
            if np.linalg.norm(Y) < .5 or np.linalg.norm(X) < .5:
                Y = np.array([0, 1, 0.]); X = np.array([1, 0, 0.])
            Y = Y / np.linalg.norm(Y); X = X - Y * (X @ Y); X = X / max(np.linalg.norm(X), 1e-6); Z = np.cross(X, Y)
            Rm = B @ S_ @ np.c_[X, Y, Z] @ S_ @ B.T
            if np.linalg.det(Rm) < 0:
                Rm[:, 0] *= -1
            g2 = half(int(A[i, 6]) & 0xffff)
            sc = float(g2) if g2 > 0 else 0.0
            pos = g2b(np.array([[x[i], y[i], z]]))[0]
            rows[it['item']].append(dict(ctxid=0, pos=[round(float(v), 3) for v in pos], rotationMatrix=[round(float(v), 5) for v in Rm.ravel()], scale=sc, size=1, type=it['item']))
            stats['instances'] += 1
        o_ += cnt
# --- CollObjs: smashable scenery
RT0 = 'D:/Roms/Xbox 360/Xenia/FH1XeniaFix/media/tracks/colorado/Ribbon_00/'
col = open(RT0 + 'CollObjs.xml', errors='replace').read()
pc = (r'PhysicsType="([^"]+?)\.\d+\.rmb"[^>]*>\s*<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<Orientation>\s*'
      r'<XAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<YAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<ZAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>')
def norm(n):
    n = n.lower(); n = re.sub(r'_lod\d+.*$', '', n); n = re.sub(r'^(o_|co_|s_|obj_)+', '', n); n = re.sub(r'_\d+$', '', n)
    return n.strip('_')


byname = {}
for c in F:
    if lod(c['name']) == 0:
        byname.setdefault(norm(c['name']), set()).add(int(c['f'].split('.')[1]))
seen = set(); miss = collections.Counter()
for r_ in re.findall(pc, col):
    nm = r_[0]; v = list(map(float, r_[1:]))
    if 'FEST' in nm.upper():
        stats['collobjs_event'] += 1
        continue
    x, y, z = v[0], v[1], -v[2]
    if not (X0 < x < X1 and Z0 < z < Z1) or (nm, round(x, 1), round(z, 1)) in seen:
        continue
    seen.add((nm, round(x, 1), round(z, 1)))
    cand = byname.get(norm(nm))
    if not cand:
        miss[nm] += 1
        continue
    it = model_item(sorted(cand)[0])
    if it is None:
        miss[nm] += 1
        continue
    Rg = np.array(v[3:12]).reshape(3, 3).T
    Rm = B @ game_rot(Rg) @ B.T
    rows[it['item']].append(dict(ctxid=0, pos=[round(float(q), 3) for q in g2b(np.array([[x, y, z]]))[0]], rotationMatrix=[round(float(q), 5) for q in Rm.ravel()], scale=1.0, type=it['item']))
    stats['collobjs'] += 1
print('CollObjs placed', stats['collobjs'], 'unmatched', sum(miss.values()), miss.most_common(10))
for item, r_ in rows.items():
    v_ = [o['scale'] for o in r_ if o.get('size') and o['scale'] > 0]
    m_ = float(np.median(v_)) if v_ else 1.0
    for o in r_:
        if o.pop('size', None):
            o['scale'] = round(float(np.clip(o['scale'] / m_, .4, 2.5)), 3) if o['scale'] > 0 else 1.0
PLANT_RE = re.compile(r'tree|bush|shrub|pinion|sage|grass|cactus|weed|fern', re.I)
LIVE = pickle.load(open('../memobj_all.pkl', 'rb')) if os.path.exists('../memobj_all.pkl') else {}
if os.path.exists('../zonevote.pkl'):      # positions the drive missed, only where the zones agree strongly on the mesh
    have = {(round(v[0], 1), round(v[2], 1)) for v in LIVE.values()}; zadd = 0
    for p_, (top, share, votes, t_, (zx, zy, zz, zM)) in pickle.load(open('../zonevote.pkl', 'rb')).items():
        if p_ not in have and share >= .7 and votes >= 4:
            LIVE[(t_, round(zx), round(zz))] = (zx, zy, zz, zM); zadd += 1
    print('zone-only objects added', zadd)
names_ = {}
for c in F:
    names_.setdefault(int(c['f'].split('.')[1]), c['name'])
ONROAD_OK = re.compile(r'gantry|truck|semi|rv_|van|ambulance|trailer|broadcast|crane|vehicle|pole|sign|banner|bin|grandstand|advert', re.I); roadskip = collections.Counter()
KEEP = re.compile(r'REDROCKS|RedRock|Redrock|HorizonOutpost|Telegraph|Pylon|LampPost|StreetLight|SpotLight|Sign|Bin[A-Z_]|BinE|Recycling|Phone|Gate|Bench|Table|'
                  r'Outhouse|Restroom|FenceF|PROC_cars|Vehicles_|RV_|Machinery|Barnfind|Visitor|Viewpoint|Snowpole|Interstate|SHACK|Barn|BLDG|Binocular|Silo|ArmcoArrow|House', re.I)
NOTKEEP = re.compile(r'^(FEST_(?!HorizonOutpost)|OBJ_FEST|O_FEST|CO_FEST)|Vehicles_|RV_|PROC_cars|Grandstand|Screen|Advert|Tent_|Crane|Broadcast|Countdown|Flag_|Tyre|PortaPotty|Hoarding', re.I)
dropped = collections.Counter()
placed = defaultdict(list); skip = collections.Counter(); kept = collections.Counter(); uniq = {}
from scipy.spatial import cKDTree
_go = re.findall(r'GameplayID="[^"]+">\s*<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', open(RT0 + 'GameObjs.xml', errors='replace').read())
GOT = cKDTree(np.array(_go, 'f8'))      # objects sitting exactly on a gameplay node belong to that event, not the world
for (t, kx, kz), v in LIVE.items():
    nm_ = names_.get(t, '')
    if GOT.query(v[:3])[0] < 1.0 and 'HorizonOutpost' not in nm_:
        skip['on gameplay node'] += 1
        continue
    if not nm_ or NOUSE.search(nm_) or PLANT_RE.search(nm_):
        continue
    x, y, z = v[0], v[1], -v[2]
    if not (X0 < x < X1 and Z0 < z < Z1):
        continue
    if not re.search(r'HorizonOutpost|Observatory', nm_) and (NOTKEEP.search(nm_) or not KEEP.search(nm_)):
        skip['not free-roam'] += 1; dropped[re.sub(r'\d+', '#', re.sub(r'_LOD.*$', '', nm_))[:30]] += 1
        continue
    base = re.sub(r'_LOD\d+.*$', '', nm_)
    uniq.setdefault((base, round(x), round(z)), (t, nm_, x, y, z, v[3]))
for (base, kx, kz), (t, nm_, x, y, z, Rr) in uniq.items():
    l_ = lod(nm_); t0 = t
    for d_ in range(1, l_ + 1):                  # use the LOD0 sibling when the game had a lower one loaded
        if re.sub(r'_LOD\d+.*$', '', names_.get(t - d_, '')) == base and lod(names_[t - d_]) == 0:
            t0 = t - d_
            break
    ri = int((x - X0) / SQ); rj = int((Z1 - z) / SQ)
    if 0 <= ri < N and 0 <= rj < N and np.isfinite(Road[rj, ri]) and not ONROAD_OK.search(nm_):
        skip['on road'] += 1; roadskip[re.sub(r'\d+', '#', nm_)[:30]] += 1
        continue
    it = model_item(t0)
    if it is None:
        skip['no mesh'] += 1
        continue
    Rg = np.array(Rr).T; sc = float(np.linalg.norm(Rg[:, 1])); Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
    Rm = B @ game_rot(Rg) @ B.T
    if np.linalg.det(Rm) < 0:
        skip['mirrored'] += 1
    rows[it['item']].append(dict(ctxid=0, pos=[round(float(q), 3) for q in g2b(np.array([[x, y, z]]))[0]], rotationMatrix=[round(float(q), 5) for q in Rm.ravel()], scale=round(float(np.clip(sc, .2, 5)), 3), type=it['item']))
    kept[re.sub(r'\d+', '#', nm_)[:30]] += 1
print('dropped', dropped.most_common(70))
print('road-skipped', roadskip.most_common(16))
print('live objects', sum(kept.values()), 'skipped', dict(skip)); print(kept.most_common(70))
PLANT = re.compile(r'tree|bush|shrub|pinion|sage|grass|cactus|weed|fern', re.I)
solid = 0; SOLIDS = collections.Counter()
for no, it in list(defs.items()):
    if not it or PLANT.search(it['item']) or it['item'] not in rows:
        continue
    for o in rows.pop(it['item']):
        SOLIDS[it['item']] += 1
        Rm = np.array(o['rotationMatrix']).reshape(3, 3) * o['scale']; pos = np.array(o['pos'])
        for mat, pb, nb, uv, t in it['groups']:
            P = pb @ Rm.T + pos; N_ = nb @ Rm.T; N_ = N_ / np.maximum(np.linalg.norm(N_, axis=1, keepdims=True), 1e-9)
            c = P.mean(0)
            if (np.abs(c[:2]) >= HALF - 4).any():
                continue
            key = np.floor((c[:2] + HALF) / CELL).astype(int); u = int(key[0]) * 1000 + int(key[1])
            cells[(u, 'col')][mat].append((P, N_, uv, t.reshape(-1, 3), uv))
            stats['col'] += len(t) // 3
        solid += 1
print('solid placed objects', solid, 'plant instances', sum(len(v) for v in rows.values()), 'plant items', len(rows))
for item, r in rows.items():
    (fdir / f'{item}.forest4.json').write_text(''.join(json.dumps(o) + '\n' for o in r))
(level / 'art' / 'forest' / 'managedItemData.json').write_text(json.dumps({v['item']: v['d'] for v in defs.values() if v and v['item'] in rows}, indent=2))
for v in defs.values():
    if v and v['item'] not in rows:
        p_ = adir / f"{v['item']}.dae"
        if p_.exists():
            p_.unlink()
print('card meshes', stats['card_meshes'], 'mean opaque coverage %', stats['card_fit_pct'] / max(stats['card_meshes'], 1))
print('forest', len(rows), 'items', stats['instances'], 'instances; no model', stats['inst_nomodel'], 'no mesh', stats['inst_nomesh'])
print('top items', sorted(((len(v), k) for k, v in rows.items()), reverse=True)[:12])

# ---- heightfield safety floor under the game's terrain meshes
H = Hs[0].copy()
print('terrain coverage tier0', float(np.isfinite(H).mean()))
for k in (1, 2, 3):
    a = np.isnan(H); H[a] = Hs[k][a]
holes = np.isnan(H); H = pyramid_fill(H)
H[holes] = ni.gaussian_filter(H, 6)[holes]
covered = np.isfinite(Cov); print('terrain meshes shown on', float(covered.mean()), 'of the square')
road = np.isfinite(Road)
if road.any():
    RF = pyramid_fill(np.where(road, Road, np.nan)); dist = ni.distance_transform_edt(~road) * SQ
    limit = RF - .2 + np.maximum(dist - SQ, 0); cut = (dist <= 40) & (H > limit); H[cut] = limit[cut]
# the game's meshes are the ground: keep the heightfield far enough under them that it can never poke through
under = ni.binary_erosion(covered, iterations=3)
low = ni.minimum_filter(H, size=31) - 8.0
w_ = ni.gaussian_filter(under.astype('f4'), 2)
w_ = np.where(under, w_, 0.0)
H = H * (1 - w_) + low * w_
level.mkdir(parents=True, exist_ok=True); tdir = level / 'art' / 'terrains'; tdir.mkdir(parents=True, exist_ok=True)
name = pal['terrain']['dirt']; LAYERS = [name]
zmin = float(H.min()) - 1; zmax = float(H.max()) + 1; maxh = zmax - zmin
q = np.clip(np.rint((H - zmin) / maxh * 65535), 0, 65535).astype('<u2')
with open(level / 'theTerrain.ter', 'wb') as f:
    f.write(bytes([9])); f.write(np.uint32(N).tobytes()); f.write(q.tobytes()); f.write(np.zeros((N, N), 'u1').tobytes())
    f.write(np.uint32(1).tobytes()); e = name.encode(); f.write(bytes([len(e)])); f.write(e)
r = json.load(open('../reg.json')); src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB')
box = (X0 / r['sx'] + r['ox'], Z0 / r['sz'] + r['oz'], X1 / r['sx'] + r['ox'], Z1 / r['sz'] + r['oz'])
BASE = 2048
base = src.transform((BASE, BASE), Image.EXTENT, box, Image.BICUBIC); base.save(tdir / 't_terrain_base_b.color.png')
for fn, c_ in {'t_terrain_base_nm.normal.png': (128, 128, 255), 't_terrain_base_ao.data.png': (255, 255, 255), 't_terrain_base_r.data.png': (230, 230, 230), 't_terrain_base_h.data.png': (128, 128, 128)}.items():
    Image.new('RGB', (BASE, BASE), c_).save(tdir / fn, optimize=True)
trel = f'{rel}/art/terrains/'; tset = f'{LVL}TerrainMaterialTextureSet'
tm = {tset: {'name': tset, 'class': 'TerrainMaterialTextureSet', 'baseTexSize': [BASE, BASE], 'detailTexSize': [1024, 1024], 'macroTexSize': [1024, 1024]}}
bn = {'baseColorBaseTex': 't_terrain_base_b.color.png', 'normalBaseTex': 't_terrain_base_nm.normal.png', 'aoBaseTex': 't_terrain_base_ao.data.png', 'roughnessBaseTex': 't_terrain_base_r.data.png', 'heightBaseTex': 't_terrain_base_h.data.png'}
v = dict(pal['terrain_defs'][name]); v.pop('persistentId', None); v.pop('name', None); v['internalName'] = name
for k in list(v):
    if k.endswith('BaseTex'):
        v[k] = trel + bn.get(k, 't_terrain_base_h.data.png')
    if k.endswith('BaseTexSize'):
        v[k] = 2 * HALF
tm[f'{name}_{LVL}'] = v
(tdir / 'main.materials.json').write_text(json.dumps(tm, indent=2))

# ---- shapes, materials, scene
sdir = level / 'art' / 'shapes'; sdir.mkdir(parents=True, exist_ok=True)
(sdir / f'{LVL}.materials.json').write_text(json.dumps(MATS, indent=2))
items = []
for (u, cat), mm in cells.items():
    groups = []
    for mat, parts in mm.items():
        ps = []; ns = []; uv = []; ix = []; u2 = []; o = 0
        for a_, bb_, cc, t, d_ in parts:
            ps.append(a_); ns.append(bb_); uv.append(cc); ix.append((t + o).ravel()); u2.append(d_); o += len(a_)
        groups.append((mat, np.concatenate(ps), np.concatenate(ns), np.concatenate(uv), np.concatenate(ix), np.concatenate(u2)))
    org = np.round(np.concatenate([g[1] for g in groups]).mean(0))
    fn = f'c{u}_{cat}'; write_dae(sdir / 'fh1' / f'{fn}.dae', groups, org)
    ct = 'Visible Mesh Final' if cat == 'col' else 'None'
    items.append({'class': 'TSStatic', 'name': f'fh1_{fn}', '__parent': 'fh1', 'shapeName': f'{rel}/art/shapes/fh1/{fn}.dae',
                  'position': [float(x) for x in org], 'collisionType': ct, 'decalType': ct})
pickle.dump(dict(world=sorted(int(c['f'].split('.')[1]) for c in sel), placed=sorted(k for k, v in defs.items() if v)), open('../used.pkl', 'wb'))
print('solid types', collections.Counter(re.sub(r'_?\d+$', '', k) for k in SOLIDS).most_common(30))
print('shapes', len(items), 'materials', len(MATS), 'textures', sum(1 for v in TX.info.values() if v), 'missing textures', len(TX.missing))

RT = 'D:/Roms/Xbox 360/Xenia/FH1XeniaFix/media/tracks/colorado/Ribbon_00/'
pat = (r"route_checkpoint_\d+'[^>]*>\s*<Transform pos\.x='([-\d.]+)' pos\.y='([-\d.]+)' pos\.z='([-\d.]+)' "
       r"facing\.x='([-\d.e]+)' facing\.y='[-\d.e]+' facing\.z='([-\d.e]+)'")
bestr = None
for f in glob.glob(RT + 'TrackRoute*.xml'):
    P = [tuple(map(float, x)) for x in re.findall(pat, open(f, errors='replace').read())]
    P = [(a, b, -c, d, -e) for a, b, c, d, e in P]
    ins = [x for x in P if -6700 < x[0] < -5200 and 4400 < x[2] < 6000]
    if ins and (bestr is None or len(ins) > bestr[0]):
        bestr = (len(ins), os.path.basename(f), ins[0])
x, y, z, fx, fz = bestr[2]
pos = g2b(np.array([[x, y, z]]))[0]; ri = int((x - X0) / SQ); rj = int((Z1 - z) / SQ)
if np.isfinite(Road[rj, ri]):
    pos[2] = Road[rj, ri]
yaw = math.atan2(fx, fz); c_, s_ = math.cos(yaw), math.sin(yaw)
spawn = {'class': 'SpawnSphere', 'name': 'spawn_redrock', '__parent': 'MissionGroup', 'dataBlock': 'SpawnSphereMarker',
         'position': [float(pos[0]), float(pos[1]), float(pos[2] + .6)], 'rotationMatrix': [c_, -s_, 0, s_, c_, 0, 0, 0, 1], 'radius': 1}
main = level / 'main'; mg = main / 'MissionGroup'; (mg / 'fh1').mkdir(parents=True, exist_ok=True)
(main / 'items.level.json').write_text(json.dumps({'name': 'MissionGroup', 'class': 'SimGroup', 'enabled': '1'}) + '\n')
obj = [{'class': 'LevelInfo', 'name': 'theLevelInfo', '__parent': 'MissionGroup', 'visibleDistance': 6000, 'gravity': -9.81, 'canvasClearColor': [.5, .6, .7, 1]},
       {'class': 'TimeOfDay', 'name': 'tod', '__parent': 'MissionGroup', 'time': .1, 'play': False, 'azimuthOverride': 0, 'startTime': .1},
       {'class': 'ScatterSky', 'name': 'sunsky', '__parent': 'MissionGroup', 'position': [0, 0, 0], 'shadowDistance': 1600, 'shadowSoftness': .2, 'brightness': 1.0},
       spawn, {'name': 'fh1', 'class': 'SimGroup', '__parent': 'MissionGroup'},
       {'name': 'theForest', 'class': 'Forest', '__parent': 'MissionGroup', 'position': [0, 0, 0]},
       {'name': 'theTerrain', 'class': 'TerrainBlock', '__parent': 'MissionGroup', 'position': [-HALF, -HALF, zmin], 'squareSize': SQ, 'maxHeight': maxh,
        'baseTexSize': 2048, 'materialTextureSet': tset, 'terrainFile': f'{rel}/theTerrain.ter'}]
(mg / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in obj))
(mg / 'fh1' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in items))
base.resize((1024, 1024)).save(level / f'{LVL}.jpg', quality=90)
(level / 'info.json').write_text(json.dumps({
    'title': 'Colorado: Red Rock (FH1 chunk)', 'description': 'Personal conversion of a chunk of Forza Horizon (2012) Colorado from your own copy. Not for redistribution.',
    'authors': 'Playground Games / Turn 10 (original); converted locally', 'country': 'United States', 'biome': 'desert',
    'size': [int(2 * HALF), int(2 * HALF)], 'previews': [f'{LVL}.jpg'],
    'spawnPoints': [{'objectname': 'spawn_redrock', 'translationId': 'Red Rock', 'preview': f'{LVL}.jpg'}], 'defaultSpawnPointName': 'spawn_redrock'}, indent=2))
pickle.dump({k: v for k, v in surf.items()}, open('../surf.pkl', 'wb'))
print('done', f'{time.time() - start_:.0f}s ->', level)
