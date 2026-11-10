"""Red Rock chunk, second pass: FH1's own meshes, textures and placed models in a BeamNG level."""
import sys, os, re, json, math, hashlib, pickle, shutil, struct, subprocess, threading, time, glob, collections
import numpy as np
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from pathlib import Path
from PIL import Image, ImageDraw
from numba import njit
from scipy import ndimage as ni
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import colfit, dds, frame, gamepath, hexzones, rmb, tex, xenos
from daewrite import FARCUT, SKIPMID, has_col, write_lod
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng, donor
from kn5port.level import safe
from kn5port.terrain import pyramid_fill

Image.MAX_IMAGE_PIXELS = None
T_START = T_LAP = time.time(); TIMES = []
# FH1_PROFILE=1 writes cProfile stats for the build and each post-build script to ../logs/prof-<stamp>/, for python -m pstats
PROF = Path('../logs') / f'prof-{time.strftime("%Y%m%d-%H%M%S")}' if os.environ.get('FH1_PROFILE') else None
if PROF:
    import cProfile
    PROF.mkdir(parents=True, exist_ok=True); PROFILER = cProfile.Profile(); PROFILER.enable()


def lap(name):
    """how long the section that just ended took, printed now and again in the summary at the end"""
    global T_LAP
    now = time.time(); TIMES.append((name, now - T_LAP)); T_LAP = now
    print(f'[time] {name} {TIMES[-1][1]:.1f}s', flush=True)


class Tee:
    """the terminal and the build log at once: the terminal a build ran in isn't always around afterwards"""
    def __init__(self, *s):
        self.s = s

    def write(self, t):
        for s_ in self.s:
            s_.write(t)
        return len(t)

    def flush(self):
        for s_ in self.s:
            s_.flush()

    def __getattr__(self, k):
        return getattr(self.s[0], k)


# FH1_CHUNK picks the area: level name, title, heightfield size, grid spacing, centre, map art size
CHUNKS = {'redrock': ('fh1_redrock', 'Colorado: Red Rock (FH1 chunk)', 2048, 2.0, -6000.0, 4250.0, 2048),
          'colorado': ('fh1_colorado', 'Colorado (FH1)', 4096, 4.0, -300.0, 500.0, 4096),
          'clearsprings': ('fh1_clearsprings', 'Colorado: Clear Springs (FH1 chunk)', 2048, 2.0, 1700.0, 2000.0, 2048)}
LVL, TITLE, N, SQ, CX, CZ, BASE = CHUNKS[os.environ.get('FH1_CHUNK', 'redrock')]
LOG = Path('../logs') / f'{LVL}-{time.strftime("%Y%m%d-%H%M%S")}.log'; LOG.parent.mkdir(exist_ok=True)
LOGF = open(LOG, 'w', encoding='utf8', buffering=1); sys.stdout = Tee(sys.stdout, LOGF); sys.stderr = Tee(sys.stderr, LOGF)
print('log', LOG.resolve())
CELL = 250.0; TEXMAX = 1024; CACHE = Path('../ddscache')       # delete CACHE after changing dds.py or TEXMAX
# LOD switch distances in metres, and the screen scale (pixels of radius per unit radius/distance) at 1440p
LOD_DIST = (300.0, 800.0); PROP_FAR = 1600.0; PX = 1250.0
IMPOSTERS = os.environ.get('FH1_IMPOSTERS', '1') != '0'
# FH1's far mountains stand up to 18 km out, beyond the square. The whole map keeps them as a backdrop; a chunk would pull in the rest of the map
BACKDROP = os.environ.get('FH1_BACKDROP', '1' if LVL == 'fh1_colorado' else '0') != '0'; VISIBLE = 24000 if BACKDROP else 6000
HALF = N * SQ / 2; X0, X1, Z0, Z1 = CX - HALF, CX + HALF, CZ - HALF, CZ + HALF
root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(beamng.find_mods()) / 'unpacked' / LVL
level = root / 'levels' / LVL; rel = f'/levels/{LVL}'
shutil.rmtree(level, ignore_errors=True)        # everything here is generated, and a stale shape would still load
inst = beamng.find_install()
F = pickle.load(open('../cat2.pkl', 'rb')); MT = pickle.load(open('../meshtab.pkl', 'rb'))
IDX, SHADERS = pickle.load(open('../texshader.pkl', 'rb')); DRAW = np.load('../mt.npy')


def samplers():
    """{shader name: (AO slot, lightmap slot, window mask, modulate slot, specular slot)}, from each shader's own pixel shader: a sampler's register is its material slot
    (see formats.md, .fxobj shaders). Window mask: ('map', slot) for an EmissiveMapSampler, ('spec', slot) for the *_emissive shaders without
    one, which light where the specular map's green is over half"""
    out = {}
    for f_ in glob.glob('../x/shaders_track_*.fxobj'):
        ps_ = [s_ for s_ in xenos.shaders(open(f_, 'rb').read()) if s_['kind'] == 'ps']
        if ps_:
            r_ = {n_: reg for n_, set_, reg, _, _ in ps_[0]['consts'] if set_ == 3}; nm_ = Path(f_).stem[len('shaders_track_'):].lower()
            win = ('map', r_['EmissiveMapSampler']) if 'EmissiveMapSampler' in r_ else ('spec', r_['SpecularMapSampler']) if 'emissive' in nm_ and 'SpecularMapSampler' in r_ else None
            out[nm_] = (r_.get('AOMapSampler', r_.get('AO_Sampler')), r_.get('Light_MapSampler'), win, r_.get('Modulate_Sampler'), r_.get('SpecularMapSampler'))
    return out


SAMP = samplers()
lap('wipe and load tables')

NOUSE = re.compile(r'occluder|shadow|underground|reflect|^Ref_|proxy|collision|_CAGE', re.I)
# The draw table's f flags above 0x3f tag a record's kind: 0x100 on all 1,103 TERR_CUBE records (FH1's occluder boxes, never visible ground,
# among them Carson's town-block stand-ins), 0x80 on *_ShadowCaster_* meshes, 0x1000 on road markings and tyre marks. A mesh whose every
# record is an occluder or shadow caster isn't drawn
_fl = collections.defaultdict(list)
for t_, f_ in zip(DRAW['t'].astype(int), DRAW['f'].astype(int)):
    _fl[t_].append(f_)
NODRAW = {t_ for t_, v in _fl.items() if all(f_ & 0x180 for f_ in v)}
# g bit 3 marks close-range ground (64% listed from hexes in or touching the mesh's box, 3% only from 500 m or more); bit 4 background
# (27% and 51%). Ground fallback prefers bit 3
GBITS = collections.defaultdict(list)
for t_, g_ in zip(DRAW['t'].astype(int), DRAW['g'].astype(int)):
    GBITS[t_].append(g_)
PLACEHOLDER = 0x2CA9     # neon yellow, on blocked-out ground such as Area01 and Zone2_Area3_6_middistance_Area3
NOMESH = {6664}      # Plane001: a 1000 by 500 m upright panel placed near the festival. Many other meshes are also called Plane<n>
TERR = re.compile(r'Terrain|^TERR_(CLRD|Moun_)|^Festival_Area\d+_\d+_LOD|PATCH|MIDDIST|_MID_|_CHUNK\d|_TERR_Close|UberLOD|Area\d+[AB]?_LOD|^Mountains_Area\d+_NOLOD', re.I)
FAR = re.compile(r'TERR_CUBE|MIDDIST|_MID_|UberLOD|PATCH', re.I)      # coarser ground: only shown where nothing better covers it
# TERR_CUBE (FH1's coarse filler, 80 to 130 m triangles, and box stand-ins for Carson's town blocks) stays out. As a last fill tier it
# drew those blocks as grey slabs through the houses. Holes are closed by choosing ground from FH1's zone hexes instead (HEXGROUND)
FARQ = []; ZBANDS = (150.0, 400.0, 800.0, 1500.0)      # far ground's tiers: how close FH1 draws it from, in metres. See far_tiers


def lodkey(n):
    """the name shared by a mesh's LODs. Exports vary: X_LOD00_ against X_LOD02, X_LOD00 against XLOD01.
    A number after the LOD is a tile, as in X_LOD02_12, so it stays. UberLOD24 is a name, not a level"""
    return re.sub(r'_?(?<!Uber)LOD\d+_*(\d*).*$', r'_\1', n).rstrip('_')


def lod(n):
    m = re.search(r'_LOD(\d+)', n)
    return int(m.group(1)) if m else 0


def g2b(p):
    return np.stack([p[:, 0] - CX, -(p[:, 2] - CZ), p[:, 1] + frame.LIFT], 1)


def n2b(n):
    return np.stack([n[:, 0], -n[:, 2], n[:, 1]], 1)


def materials(b, p):
    """[(shader index, [slots], constants)] for a file, by material index. A record's word 6 counts its constants, the shader's c360_i
    registers in order, as big-endian float4s right after it: (n, 4) floats. The slot list follows (formats.md)"""
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
                    nc = w[i + 6] if i + 7 + 4 * w[i + 6] <= j else 0
                    out.append((w[i + 1], list(w[j + 2:j + 2 + n]), np.array(struct.unpack_from('>%df' % (4 * nc), b, e + 4 * (i + 7))).reshape(nc, 4)))
                    break
    return out


def cooked(path, *args, save=dds.save, **kw):
    """dds.save through a disk cache: FH1's textures never change, and encoding them is slow"""
    c_ = CACHE / path.name
    if not c_.exists():
        CACHE.mkdir(exist_ok=True); save(c_, *args, **kw)
    shutil.copyfile(c_, path)


def keep_pickle(p_, d):
    tmp = p_.with_suffix(f'.{os.getpid()}.tmp')      # one per process: lanes for two chunks can build at once
    with open(tmp, 'wb') as f_:
        pickle.dump(d, f_)
    try:
        os.replace(tmp, p_)
    except PermissionError:      # the other build's copy is open, and holds the same
        os.remove(tmp)


# What decoding a texture tells the build (name, mean colour, alpha cut) and which colour layer each normal map pairs with, kept beside the cooked
# textures so a build decodes only what it hasn't seen. Both follow from FH1's files, tex.py and the code below: bump INFO_V after changing get()
# or section_normals(), or delete the pickles
INFO_V = 1; INFO_KEY = hashlib.md5(Path(tex.__file__).read_bytes() + f'{INFO_V} {TEXMAX}'.encode()).hexdigest()[:12]
INFO_P = CACHE / f'texinfo-{INFO_KEY}.pkl'; PAIR_P = CACHE / f'pairs-{INFO_KEY}.pkl'
load_ = lambda p_: pickle.load(open(p_, 'rb')) if p_.exists() else {}


class Info(dict):
    """a texture's info, its pixels (src) decoded only when asked for"""
    def __missing__(self, k):
        if k != 'src':
            raise KeyError(k)
        self['src'] = TX.pixels(self['tid'])[0]
        return self['src']


class Textures:
    def __init__(self):
        self.d = level / 'art' / 'textures'; self.d.mkdir(parents=True, exist_ok=True)
        self.info = {}; self.missing = collections.Counter(); self.known = load_(INFO_P)

    def pixels(self, tid):
        """(the texture at a power-of-two size, as BeamNG needs, 16 and up, whether it was already one), or None. UVs are normalised"""
        t = tex.bix(tid); r = None
        if t and t['fmt'] != 0x71:
            r = tex.load(tid)
        if r is None or r[0] is None:
            return None
        a = r[0]; w_, h_ = (max(1 << math.ceil(math.log2(v_)), 16) for v_ in a.shape[1::-1]); pot = (w_, h_) == a.shape[1::-1]
        if not pot:
            a = np.asarray(Image.fromarray(a).resize((w_, h_), Image.BICUBIC))
        return a, pot, t

    def get(self, tid):
        if tid in self.info:
            return self.info[tid]
        name = f'd{tid:X}'      # pre-cooked, so BeamNG loads FH1's sizes. The d prefix can't match PNG-era cached textures
        k_ = self.known.get(tid, ...)
        if k_ is None or k_ is not ... and (CACHE / f'{name}.color.dds').exists() and (k_[1] <= .002 or (CACHE / f'{name}_o.data.dds').exists()):
            if k_ is None:
                self.missing[tid] += 1; self.info[tid] = None
                return None
            cooked(self.d / f'{name}.color.dds')
            if k_[1] > .002:
                cooked(self.d / f'{name}_o.data.dds')
            self.info[tid] = Info(tid=tid, name=name, rgb=k_[0], cut=k_[1])
            return self.info[tid]
        r = self.pixels(tid)
        if r is None:
            self.missing[tid] += 1; self.info[tid] = self.known[tid] = None
            return None
        a, pot, t = r
        cooked(self.d / f'{name}.color.dds', a, tex.blocks(tid) if t['fmt'] == 0x52 and pot else None, TEXMAX)
        al = a[..., 3]; cut = float((al < 128).mean())
        if cut > 0.002:
            cooked(self.d / f'{name}_o.data.dds', al, limit=TEXMAX)
        rgb = a[..., :3].reshape(-1, 3).mean(0)
        self.info[tid] = Info(tid=tid, name=name, rgb=rgb, cut=cut, src=a); self.known[tid] = (rgb, cut)
        return self.info[tid]

    def normal(self, tid):
        """a 0x71 normal map as a BC5 .normal.dds, kept at FH1's size"""
        p_ = self.d / f'n{tid:X}.normal.dds'
        if not p_.exists():
            rg, b_ = tex.normal(tid); cooked(p_, rg, b_, TEXMAX, save=dds.save_bc5)
        return f'{rel}/art/textures/{p_.name}'


TX = Textures()
MATS = {}
# Lamp glass that glows at night, as West Coast's floodlights do: FH1's h_diff_spec_ao_2__emissive_lm lamps keep an emissive mask in slot
# position 3, black except the glass (a yellow globe on the lamp post, tinted glass on the street light). It becomes the material's emissiveMap
# under instanceEmissive, and the TSStatics carry nightEmissive with a black instanceColor, so stock core_environment lights only the glass from
# 18:00 to 6:00. GLOW: model name prefixes and the mask's slot position. GLOW_NITS by eye from West Coast's floodlight (4000) and traffic lights (5000)
GLOW = {'OBJ_CLRD_LampPost': 3, 'OBJ_CLRD_StreetLight': 3}; GLOW_NITS = 3000; GLOWING = set()
# Object lightmaps: FH1 adds the lightmap to the light falling on a surface, so it shows times the surface colour (formats.md, .fxobj shaders).
# Here it's an emissive map on UV1 tinted by the texture's mean colour, under instanceEmissive, lit at night as GLOW is. LM_NITS by eye: a full-white
# texel on a mid-grey wall comes out near nightlights.py's ground pools (NITS 40); raised from 80 when the user found night too dark. LM_LIT: share of texels above 40 of 255 for a lightmap to be used
LM_NITS = 120; LM_LIT = .002; LITMAP = {}
# Lit windows: FH1's emissive building shaders add a window mask in a warm colour, c254 in H_DIFF_SPEC_REFL_EMISSIVE_AO_4_LM's pixel shader
# (1, 0.5, 0.32), switched on per building at dusk. Here it's an emissiveMap lit at night like the lightmaps; WIN_NITS by eye. A section with
# both a lightmap and windows gets them baked into one UV1 texture of BAKE px (bake_emissive)
WIN_RGB = (1.0, .5, .32); WIN_NITS = 350; BAKE = 512; BAKE_NITS = max(LM_NITS, WIN_NITS); WINMASK = {}
NM = {}; PAIR = load_(PAIR_P)       # NM: the current section's normal map for each colour texture, from section_normals


def section_normals(no, slots):
    """{colour texture: normal map} for a material's 0x71 slots. A normal map goes with the colour layer whose brightness
    gradients it matches best, or with the first layer when none match, as with the generic road noise normals"""
    ids = MT[no][0]; col = []; nrm = []
    for s in slots:
        if s < 96 and s < len(ids):
            t = tex.bix(IDX[ids[s]][0])
            if t:
                (nrm if t['fmt'] == 0x71 else col).append(IDX[ids[s]][0])
    out = {}
    for n_ in nrm:
        left = [c_ for c_ in col if c_ not in out and not rgmask(c_)]
        if not left:
            break
        k_ = (n_, tuple(left))
        if k_ not in PAIR:
            PAIR[k_] = left[0]; rg = tex.normal(n_)[0].astype('f4') / 127.5 - 1; best = .1
            for c_ in left:
                g_ = TX.get(c_)
                if g_ is None:
                    continue
                L = ni.gaussian_filter(np.asarray(Image.fromarray(g_['src'][..., :3].mean(2).astype('f4')).resize(rg.shape[1::-1])), 1)
                with np.errstate(invalid='ignore'):       # a flat texture has no gradients, and scores nan
                    s_ = min(abs(np.corrcoef(rg[..., 0].ravel(), ni.sobel(L, 1).ravel())[0, 1]), abs(np.corrcoef(rg[..., 1].ravel(), ni.sobel(L, 0).ravel())[0, 1]))
                if s_ > best:
                    best = s_; PAIR[k_] = c_
        out[PAIR[k_]] = n_
    return out


# FH1's AO maps darken only its ambient light (formats.md, .fxobj shaders), and canyon ground's are mostly black. BeamNG's AO darkened sunlit
# rock too, so it's off: FH1_AO=1 puts it back
AO = os.environ.get('FH1_AO', '0') == '1'


def material(tid, ground=None, alpha=False, translucent=False, two=False, glow=None, ao=None, lm=None, win=None, emis=None, spec=None):
    """glow: an emissive mask texture, lit at night through the TSStatic's instance colour (see GLOW). ao and lm: an AO map and a lightmap,
    both on UV1 (see object_maps). win: a window mask on UV0 (window_mask). emis: a baked UV1 emissive (bake_emissive) holding both.
    One emissive map per material, taken in that order: glow, emis, lm, win. All light at night. spec: a specular map, as the roughness (rough_texture)"""
    t = TX.get(tid); g_ = TX.get(glow) if glow is not None else None; o_ = TX.get(ao) if ao is not None else None
    e_ = emis if g_ is None else None; l_ = TX.get(lm) if lm is not None and g_ is None and e_ is None else None
    w_ = win if g_ is None and e_ is None and l_ is None else None
    if t is None:
        return None
    use_a = (alpha or translucent) and t['cut'] > 0.002; n_ = NM.get(tid)
    key = (f"fh1_{t['name']}" + ('_a' if use_a else '') + ('_t' if translucent else '') + (f'_{ground}' if ground else '') + (f'_n{n_:X}' if n_ else '') + (f'_e{glow:X}' if g_ else '')
           + (f'_o{ao:X}' if o_ else '') + (f'_l{lm:X}' if l_ else '') + (f'_w{w_[0][0]}{w_[1]:X}' if w_ else '') + (f"_{Path(e_).name.split('.')[0]}" if e_ else '')
           + (f'_s{spec:X}' if spec is not None else ''))
    if key not in MATS:
        st = {'baseColorMap': f"{rel}/art/textures/{t['name']}.color.dds", 'roughnessFactor': .9, 'metallicFactor': 0}
        r_ = rough_texture(spec) if spec is not None else None
        if r_:
            st['roughnessMap'] = r_; del st['roughnessFactor']
        if n_:
            st['normalMap'] = TX.normal(n_)
        if o_ and AO:
            st.update({'ambientOcclusionMap': grey(ao), 'ambientOcclusionMapUseUV': 1})      # linear, as stock AO maps are (.data): as .color it read as sRGB and came out far too dark
        if g_:
            st.update({'emissiveMap': f"{rel}/art/textures/{g_['name']}.color.dds", 'emissiveFactor': [1, 1, 1], 'emissiveIntensityNits': GLOW_NITS, 'instanceEmissive': True})
        elif e_:
            st.update({'emissiveMap': e_, 'emissiveMapUseUV': 1, 'emissiveFactor': [1, 1, 1], 'emissiveIntensityNits': BAKE_NITS, 'instanceEmissive': True})
        elif l_:
            st.update({'emissiveMap': f"{rel}/art/textures/{l_['name']}.color.dds", 'emissiveMapUseUV': 1, 'emissiveFactor': [round(float(v_) / 255, 3) for v_ in t['rgb']],
                       'emissiveIntensityNits': LM_NITS, 'instanceEmissive': True})
        elif w_:
            st.update({'emissiveMap': window_texture(*w_), 'emissiveFactor': list(WIN_RGB), 'emissiveIntensityNits': WIN_NITS, 'instanceEmissive': True})
        m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng'}
        if use_a:
            st['opacityMap'] = f"{rel}/art/textures/{t['name']}_o.data.dds"
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
    return write_lod(path, [((g[0], (0,)),) + tuple(g[1:]) for g in groups], origin, (1,), None)


def blend_layers(no, slots, fx, cs):
    """a ground shader's colour layers as [(texture, (sx, sy))]: FH1 tiles A at c360_0.xy, B at c360_0.zw and C at c360_1.xy over UV0,
    mostly 4 or 6 times (formats.md, .fxobj shaders). Normal maps and blend masks listed where a colour layer should be are left out"""
    ids = MT[no][0]; c0, c1 = (mat_const(cs, 0), mat_const(cs, 1)) if fx.startswith('h_blnd') else (np.ones(4), np.ones(4)); out = []
    for q in blend_slots(fx):
        if q < len(slots) and slots[q] < len(ids):
            tid = IDX[ids[slots[q]]][0]
            if tex.bix(tid) and tex.bix(tid)['fmt'] != 0x71 and not rgmask(tid):
                out.append((tid, layer_scale({0: c0[:2], 1: c0[2:], 5: c1[:2]}[q], c0[:2])))
    return out


def layer_scale(sc, base):
    """a layer's UV0 scale from its material's constants, as a tuple. A zero scale, as on 442 h_vblnd materials (c360_0 1, 1, 0, 0), would
    read one texel: those take the base layer's"""
    return tuple(float(v_) for v_ in (sc if (np.asarray(sc) > 0).all() else base if (np.asarray(base) > 0).all() else (1, 1)))


def blend_material(lays, masktid, ground, ao=None):
    """FH1 h_blnd: layer 0 plus layers masked by the red and green channels, on the second UV set. lays: blend_layers' (texture, scale),
    UV0 going out at layer 0's scale, and layers at another scale drawn from detail maps (layer_stage). ao: the ground's AO map, which
    FH1 reads where it reads the mask, on every layer"""
    tids = [t for t, _ in lays]; sc = [np.array(v_) / np.array(lays[0][1]) for _, v_ in lays]
    key = ('fh1_b_' + '_'.join(f'{t:X}' for t in tids) + f'_m{masktid:X}' + (f'_{ground}' if ground else '') + ''.join(f'_n{NM[t]:X}' for t in tids if t in NM)
           + (f'_o{ao:X}' if ao is not None and TX.get(ao) else '') + ''.join(f'_s{v_[0]:g}x{v_[1]:g}' for v_ in sc[1:] if not np.allclose(v_, 1)))
    if key in MATS:
        return key
    info = [TX.get(t) for t in tids]; m_ = TX.get(masktid)
    if any(i is None for i in info) or m_ is None:
        return None
    for ch, nm in ((0, 'r'), (1, 'g')):
        p_ = TX.d / f"{m_['name']}_{nm}.data.dds"
        if not p_.exists():
            cooked(p_, m_['src'][..., ch], limit=TEXMAX)
    st = [layer_stage(t, d_) for t, d_ in zip(tids, sc)]
    for k_, s_ in enumerate(st):
        del s_['vertColor']; s_['roughnessFactor'] = .92
        if k_:
            s_.update({'opacityMap': f"{rel}/art/textures/{m_['name']}_{'rg'[k_ - 1]}.data.dds", 'opacityMapUseUV': 1})
    if ao is not None and TX.get(ao) and AO:
        for s_ in st:
            s_.update({'ambientOcclusionMap': grey(ao), 'ambientOcclusionMapUseUV': 1})
    while len(st) < 4:
        st.append({})
    m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st, 'activeLayers': len(info), 'materialTag0': 'beamng', 'translucentBlendOp': 'None'}
    if ground:
        m['groundType'] = ground
    MATS[key] = m
    return key


# FH1's road and h_vblnd ground shaders (formats.md, .fxobj shaders) draw layer A, then B and C over it. B's weight is
# saturate(c4.x (x - 1/2) + c4.y (noise.r - 1/2) + 1/2), C's the same with c4.z, c4.w, y and noise.g: x and y are the colour vertex's bytes 1
# and 2, c4 the material's constant c360_4. h_vblnd has no noise. Each layer tiles UV0 at its own scale: A at c360_0.xy, B at .zw, C at
# c360_1.xy, the noise at c360_1.zw. The colour is then times w squared (byte 0), and times 0.05 + 0.95 AO for *_ao shaders, the AO map read
# at TEXCOORD2. Skirts (*_blend) are drawn over the ground at alpha 1 - z, z byte 3.
#
# BeamNG gives a layer two textures for opacity, multiplied: opacityMap, and saturate(1 + 2d - 1) from the opacity detail map d at strength 1,
# so storing v / 2 there multiplies by exactly v. One holds the weight's curve over UV set 1, where the vertex bytes go (ramp_uv), and
# the other the noise's. FH1 adds the weight to the noise before clamping, which no product can do, so blend_fit finds the product nearest
# to it. BeamNG has no per-layer UV scale: a layer drawn at another scale than UV0's takes its colour from a detail map, whose detailScale
# is its own. The darkening is the vertex colour (vertColor), so it changes from vertex to vertex as FH1's does, not by section
RAMP_N = 64


def ramp(axis, gain=1, half=False):
    """A ramp texture: opacity along u (axis 0) or v (axis 1) of UV set 1, for weights put there by ramp_uv. half: as an opacity detail
    map, whose blend is saturate(1 + (2d - 1)), so storing w / 2 multiplies opacity by exactly w"""
    name = f"fh1_ramp_{'uv'[axis]}{gain:g}{'h' if half else ''}"; p_ = TX.d / f'{name}.data.dds'
    if not p_.exists():
        w = np.clip(np.linspace(0, 1, RAMP_N) * gain, 0, 1) / (2 if half else 1); a = np.tile(w, (RAMP_N, 1))
        cooked(p_, (np.clip(a if axis == 0 else a.T, 0, 1) * 255 + .5).astype('u1'), limit=TEXMAX)
    return f'{rel}/art/textures/{p_.name}'


def ramp_uv(w):
    """weights 0..1, shape (n, 2), to UV set 1 at the ramp's texel centres, so bilinear filtering is exact"""
    return (w * (RAMP_N - 1) + .5) / RAMP_N


def grey(tid):
    """a texture's red channel as a .data.dds, for decal masks"""
    t = TX.get(tid)
    if t is None:
        return None
    p_ = TX.d / f"{t['name']}_g.data.dds"
    if not p_.exists():
        cooked(p_, t['src'][..., 0], limit=TEXMAX)
    return f'{rel}/art/textures/{p_.name}'


def data_map(prefix, a):
    """a 0..1 array as a BC5 .data.dds, whose red BeamNG reads as opacity: 8 bits, where DXT1 keeps 5. Named by content"""
    a = np.clip(a, 0, 1); name = f"{prefix}_{hashlib.md5(np.round(a, 4).tobytes()).hexdigest()[:10]}"; p_ = TX.d / f'{name}.data.dds'
    if not p_.exists():
        v_ = (a * 255 + .5).astype('u1'); cooked(p_, np.dstack([v_, v_]), None, TEXMAX, save=dds.save_bc5)
    return f'{rel}/art/textures/{p_.name}'


def curve_map(P, axis, half=False, Q=None):
    """P sampled at RAMP_N weights from 0 to 1, as a ramp along UV1 axis 0 (u) or 1 (v), halved for an opacity detail map. Q: a second
    curve, along the other axis, multiplied in"""
    P = np.asarray(P, 'f8') / (2 if half else 1); a = np.outer(np.ones(RAMP_N) if Q is None else Q, P)        # rows v, columns u
    return data_map('fh1_c', a if axis == 0 else a.T)


def srgb(lin):
    return np.where(lin <= .0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - .055)


def half_color(tid):
    """a colour texture at half its linear value, as a layer's detail map at strength 1, whose blend is saturate(2 d): the layer's own
    colour at its own scale. Stored as sRGB, so the halving costs little precision"""
    t = TX.get(tid); p_ = TX.d / f"{t['name']}_h.color.dds"
    if not p_.exists():
        c = t['src'][..., :3] / 255; lin = np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)
        cooked(p_, (srgb(lin / 2) * 255 + .5).astype('u1'), limit=TEXMAX)
    return f'{rel}/art/textures/{p_.name}'


FITS = {}


def blend_fit(a, b, noise, ch):
    """FH1's layer weight saturate(a (x - 1/2) + b (n - 1/2) + 1/2), x a vertex weight and n the noise texture's channel ch, as the product
    P(x) Q(n) nearest to it in the mean square, over x from 0 to 1 and n as the texture has it. -> (P at RAMP_N weights, the noise texture
    remapped by Q as a halved opacity detail map). Without noise (b 0) P is exact. Measured on 11505's red: rms error 0.11 to 0.28 by slope,
    against 0.19 to 0.32 for the hand-set ramp and noise it replaced"""
    k_ = (round(a, 3), round(b, 3), noise, ch)
    if k_ in FITS:
        return FITS[k_]
    xs = np.linspace(0, 1, RAMP_N); t = TX.get(noise) if noise is not None and b else None
    if t is None:
        FITS[k_] = (np.clip(a * (xs - .5) + .5, 0, 1), None)
        return FITS[k_]
    n_ = t['src'][..., ch].astype('f8') / 255; h_, e_ = np.histogram(n_, 64, (0, 1)); wn = h_ / h_.sum(); nc = (e_[:-1] + e_[1:]) / 2
    T = np.clip(a * (xs[:, None] - .5) + b * (nc[None] - .5) + .5, 0, 1)
    P = T.mean(1); Q = np.ones(len(nc))
    for _ in range(100):        # alternating least squares, weighted by how often each noise value occurs
        Q = (wn * T * P[:, None]).sum(0) / np.maximum((wn * P[:, None] ** 2).sum(0), 1e-12)
        P = (wn * T * Q[None]).sum(1) / np.maximum((wn * Q[None] ** 2).sum(1), 1e-12)
    s_ = max(P.max(), 1e-9); P = P / s_; Q = np.clip(Q * s_, 0, 1)
    FITS[k_] = (P, data_map(f'fh1_q{noise:X}{"rg"[ch]}', np.interp(n_, nc, Q) / 2))
    return FITS[k_]


def mat_const(cs, i):
    """the material's constant c360_i, as 4 floats. Ones when the record has none"""
    return np.array(cs[i], 'f8') if cs is not None and i < len(cs) else np.ones(4)


def slot_grey(no, slots, k):
    """the texture at slot position k as a noise, AO or modulate map: any format but normal maps"""
    ids = MT[no][0]
    if k is not None and k < len(slots) and slots[k] < len(ids):
        tid = IDX[ids[slots[k]]][0]; t = tex.bix(tid)
        if t and t['fmt'] != 0x71 and TX.get(tid):
            return tid
    return None


def shader_layers(no, slots, fx, cs):
    """the colour layers of an h_road or h_vblnd material, bottom up: [(texture, (sx, sy), weight)], weight None for layer A, else
    (vertex byte, a, b, noise channel) for the weight above. -> (layers, (noise texture, scale) or None), or None when layer A has no texture.
    h_vblnd blends only DXT1 pairs: its decal variants and blend masks aren't colour layers"""
    c0, c1, c4 = mat_const(cs, 0), mat_const(cs, 1), mat_const(cs, 4); A = slot_at(no, slots, 0); sA = layer_scale(c0[:2], (1, 1))
    if A is None:
        return None
    if fx.startswith('h_vblnd'):
        B = slot_at(no, slots, 1)
        if 'decal' in fx or B is None or not all(tex.bix(x)['fmt'] == 0x52 for x in (A, B)):
            return [(A, sA, None)], None
        return [(A, sA, None), (B, layer_scale(c0[2:], sA), (1, c4[0], 0, 0))], None
    nt_ = slot_grey(no, slots, 2) if 'noise' in fx else None; noise = (nt_, layer_scale(c1[2:], sA)) if nt_ else None; out = [(A, sA, None)]
    if ('diff2' in fx or 'diff3' in fx) and slot_at(no, slots, 1):
        out.append((slot_at(no, slots, 1), layer_scale(c0[2:], sA), (1, c4[0], c4[1] if noise else 0, 0)))
    if 'diff3' in fx and slot_at(no, slots, 5):
        out.append((slot_at(no, slots, 5), layer_scale(c1[:2], sA), (2, c4[2], c4[3] if noise else 0, 1)))
    return out, noise


def vertex_shade(no, slots, fx, m, s):
    """FH1's darkening of an h_road or h_vblnd section's colour, per vertex of the mesh: w squared, w the colour vertex's byte 0, times
    0.05 + 0.95 AO for *_ao shaders, the AO map read at TEXCOORD2 (bytes 24-27, through the section's UV box), as the vertex colour.
    The AO map is 64 px over the mesh, so reading it per vertex keeps it. None without a colour vertex"""
    if m['sd'] < 28:
        return None
    a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd']); f = (a[:, -4].astype('f8') / 255) ** 2
    t_ = slot_grey(no, slots, SAMP.get(fx, (None,) * 5)[0]) if '_ao' in fx and m['sd'] >= 32 else None
    if t_:
        A = TX.get(t_)['src'][..., :3].mean(2) / 255; h_, w_ = A.shape
        u2 = box_uv(np.frombuffer(a[:, 24:28].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535, s['fl'])
        f = f * (.05 + .95 * ni.map_coordinates(A, [u2[:, 1] * h_ - .5, u2[:, 0] * w_ - .5], order=1, mode='nearest'))
    return f


def layer_stage(tid, d):
    """a layer drawing tid, its colour from baseColorMap, or at detail scale d (when not 1, 1) from half_color. Darkened by the vertex colour"""
    t = TX.get(tid); n_ = NM.get(tid); st = {'roughnessFactor': .9, 'metallicFactor': 0, 'vertColor': True}
    if np.allclose(d, 1):
        st['baseColorMap'] = f"{rel}/art/textures/{t['name']}.color.dds"
        if n_:
            st['normalMap'] = TX.normal(n_)
    else:
        st.update({'detailMap': half_color(tid), 'detailBaseColorMapStrength': 1, 'detailScale': [round(float(v_), 4) for v_ in d]})
        if n_:
            st.update({'detailNormalMap': TX.normal(n_), 'detailNormalMapStrength': 1})
    return st


def layered(no, slots, fx, cs, ground):
    """the material for an h_road or h_vblnd section, and how to write its UVs: (key, S0, bytes, div), or None. UV0 goes out at FH1's
    UVs times S0, and UV1 is ramp_uv of the colour vertex's bytes `bytes` (u, v), divided by div (layered_uv1). Each layer above A puts
    its weight's curve on its own axis of UV1, and the noise (blend_fit) on a texture over UV0. A layer takes the first of these that keeps
    every scale: its colour on UV0 when it's at UV0's scale S0, the noise then on the detail map; both at the detail scale when they share
    it; or the noise on UV0 (when S0 is the noise's scale) and the colour on the detail map, the curve then on the detail map too, over
    UV1, which detailScale also scales: div undoes it. S0 is A's scale, or else the noise's.
    *_modulate: FH1 multiplies by lerp(1, modulate map, z), z byte 3: a black layer at opacity z (1 - map), z on a free UV1 axis.
    FH1 reads the map at TEXCOORD1 times c360_2.xy, which is UV0 here (TEXCOORD1 isn't decoded for roads)"""
    sl = shader_layers(no, slots, fx, cs)
    if sl is None or any(TX.get(t) is None for t, _, _ in sl[0]):
        return None
    lay, noise = sl; sA = np.array(lay[0][1]); sn = np.array(noise[1]) if noise else None; nz = any(w and w[2] for _, _, w in lay)

    def plan_for(S0):
        out = []
        for _, sc, w in lay[1:]:
            sc = np.array(sc)
            if not (w[2] and nz):
                out.append('ramp')
            elif np.allclose(sc, S0):
                out.append('colour')
            elif np.allclose(sc, sn):
                out.append('shared')
            elif np.allclose(S0, sn):
                out.append('noise')
            else:
                return None
        return out
    S0 = sA; plan = plan_for(sA)
    if plan is None:
        S0 = sn; plan = plan_for(sn)
    st = [layer_stage(lay[0][0], sA / S0)]; bytes_ = [0, 0]; div = np.ones(2); free = [0, 1]
    for (tid, sc, w), how in zip(lay[1:], plan):
        ax = w[0] - 1; free.remove(ax); bytes_[ax] = w[0]; d = np.array(sc) / S0; P, q_ = blend_fit(w[1], w[2], noise[0] if noise else None, w[3])
        s_ = layer_stage(tid, d)
        if how == 'ramp' or q_ is None:     # opacityMap reads UV1 unscaled
            s_.update({'opacityMap': curve_map(P, ax), 'opacityMapUseUV': 1})
        elif how in ('colour', 'shared'):       # the noise on the detail map, at the noise's scale over UV0
            s_.update({'opacityMap': curve_map(P, ax), 'opacityMapUseUV': 1, 'opacityDetailMap': q_, 'detailOpacityMapStrength': 1,
                       'detailScale': [round(float(v_), 4) for v_ in sn / S0]})
        else:       # 'noise': the noise on UV0 at S0, the curve on the detail map over UV1, which detailScale scales and div undoes
            s_.update({'opacityMap': q_, 'opacityDetailMap': curve_map(P, ax, half=True), 'opacityDetailMapUseUV': 1, 'detailOpacityMapStrength': 1})
            div[ax] = d[ax]
        st.append(s_)
    mt_ = slot_grey(no, slots, SAMP.get(fx, (None,) * 5)[3]) if 'modulate' in fx else None
    if mt_ and free and len(st) < 4:
        ax = free[0]; bytes_[ax] = 3
        st.append({'baseColorFactor': [0, 0, 0, 1], 'roughnessFactor': .9, 'metallicFactor': 0, 'opacityMap': curve_map(np.linspace(0, 1, RAMP_N), ax),
                   'opacityMapUseUV': 1, 'opacityDetailMap': data_map(f'fh1_m{mt_:X}', (1 - TX.get(mt_)['src'][..., :3].mean(2) / 255) / 2),
                   'detailOpacityMapStrength': 1, 'detailScale': [round(float(v_), 4) for v_ in mat_const(cs, 2)[:2] / S0]})
    key = f"fh1_L{lay[0][0]:X}_" + hashlib.md5(json.dumps([st, ground]).encode()).hexdigest()[:10]
    if key not in MATS:
        n_ = len(st)
        MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st + [{}] * (4 - n_), 'activeLayers': n_,
                     'materialTag0': 'beamng', 'translucentBlendOp': 'None', **({'groundType': ground} if ground else {})}
    return key, S0, bytes_, div


def layered_uv1(vw, shade, bytes_, div):
    """UV set 1 for layered(): the colour vertex's bytes on the ramps, then the vertex colour as a third column, which write_lod writes"""
    return np.c_[ramp_uv(vw[:, bytes_]) / div, shade if shade is not None else np.ones(len(vw))]


def skirt_passes(no, slots, fx, cs):
    """an h_road *_blend skirt: a verge FH1 lays over the ground at alpha 1 - z, z byte 3, which is 0 at the road's edge (95% of those
    vertices lie on the road's footprint) and 1 out over the terrain. Layers B and C blend in by their weights under that. BeamNG mixes a
    translucent material's layer weights into its alpha, so each layer is a translucent pass of its own, B and C at alpha (1 - z) t: exact
    where the skirt is opaque, close where it fades. -> [(key, scale, bytes, lift)], UV0 at the layer's scale, UV1 (z, weight) by ramp_uv"""
    sl = shader_layers(no, slots, fx, cs)
    if sl is None:
        return []
    lay, noise = sl; out = []; fade = 1 - np.linspace(0, 1, RAMP_N)
    for k_, (tid, sc, w) in enumerate(lay):
        if TX.get(tid) is None:
            continue
        P, q_ = blend_fit(w[1], w[2], noise[0] if noise else None, w[3]) if w else (np.ones(RAMP_N), None)
        st = layer_stage(tid, (1, 1)); st.update({'opacityMap': curve_map(fade, 0, Q=P), 'opacityMapUseUV': 1})
        if q_:
            st.update({'opacityDetailMap': q_, 'detailOpacityMapStrength': 1, 'detailScale': [round(float(v_), 4) for v_ in np.array(noise[1]) / np.array(sc)]})
        key = f"fh1_K{tid:X}_" + hashlib.md5(json.dumps(st).encode()).hexdigest()[:10]
        if key not in MATS:
            MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng',
                         'translucent': True, 'translucentZWrite': False, 'translucentRecvShadows': True, 'castShadows': False}
        out.append((key, np.array(sc), [3, w[0] if w else 3], .02 + .01 * k_))
    return out


def decal_material(tid, mask, alpha):
    """FH1 decals: the texture's alpha times the vertex alpha, which goes in UV set 1 through a ramp. A masked decal (h_vblnd_decal_mask)
    has its mask on UV set 1 instead, so its vertex alpha becomes one opacityFactor, the section's mean. Opaque DXT1 patches take their shape
    from the mask alone; before, they were drawn as solid squares"""
    t = TX.get(tid)
    if t is None:
        return None
    o_ = f"{rel}/art/textures/{t['name']}_o.data.dds" if t['cut'] > .002 else None
    key = f"fh1_{t['name']}_dc" + (f'_m{mask:X}_{alpha:.2f}' if mask else '')
    if key not in MATS:
        st = {'baseColorMap': f"{rel}/art/textures/{t['name']}.color.dds", 'roughnessFactor': .9, 'metallicFactor': 0}
        if mask:
            st.update({'opacityMap': grey(mask), 'opacityMapUseUV': 1, 'opacityFactor': alpha})
            if o_:
                st.update({'opacityDetailMap': o_, 'detailScale': [1, 1], 'detailOpacityMapStrength': 1})
        else:
            if o_:
                st['opacityMap'] = o_
            st.update({'opacityDetailMap': ramp(0, 1, half=True), 'opacityDetailMapUseUV': 1, 'detailScale': [1, 1], 'detailOpacityMapStrength': 1})
        MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng',
                     'translucent': True, 'translucentZWrite': False, 'translucentRecvShadows': True, 'castShadows': False}
    return key


def slot_at(no, slots, k):
    """the colour texture at a material's slot position k, or None"""
    ids = MT[no][0]
    if k < len(slots) and slots[k] < len(ids):
        tid = IDX[ids[slots[k]]][0]; t = tex.bix(tid)
        if t and t['fmt'] != 0x71 and not rgmask(tid):
            return tid
    return None


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
    r0 = np.frombuffer(a[:, 16:20].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535      # where in the UV box, 0 to 1: see box_uv
    r1 = np.frombuffer(a[:, 20:24].tobytes(), '>i2').reshape(-1, 2).astype('f8') / 32767 if m['sd'] >= 24 else None
    return r0, r1


def uv1_box(m, fl):
    """An object's second UV set, TEXCOORD1: bytes 20-23 as u16 fractions of the same UV box as UV0. Lightmaps and AO maps read it.
    None for strides under 24, which have none"""
    if m['sd'] < 24:
        return None
    a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
    return box_uv(np.frombuffer(a[:, 20:24].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535, fl)


def lit_map(tid):
    """whether a lightmap has any light in it: most are black, or only exist as the bundles' 16 px stand-ins"""
    if tid not in LITMAP:
        r_ = tex.load(tid) if tex.bix(tid) and tex.bix(tid)['fmt'] != 0x71 else None
        LITMAP[tid] = bool(r_ and r_[0] is not None and (r_[0][..., :3].mean(2) > 40).mean() > LM_LIT)
    return LITMAP[tid]


def object_maps(no, slots, fx, key=None):
    """(AO map, lightmap, window mask) of an object material, at the slots its shader samples them from. A lightmap slot of 0xFFFFFFFF
    takes the placement's own lightmap: key, the draw table's s[1] for that placement. 'draw' when that's wanted but no key is given.
    Lightmaps without light are left out. The window mask is (kind, texture id), see samplers and window_mask"""
    a_, l_, w_ = SAMP.get(fx, (None,) * 5)[:3]; ids = MT[no][0]; ao = lm = win = None
    if w_ and w_[1] < len(slots) and slots[w_[1]] < len(ids):
        t_ = IDX[ids[slots[w_[1]]]][0]
        win = (w_[0], t_) if tex.bix(t_) and tex.bix(t_)['fmt'] != 0x71 and window_mask(w_[0], t_) is not None else None
    ao = ao_map(no, slots, fx)
    if l_ is not None and l_ < len(slots):
        if slots[l_] == 0xFFFFFFFF:
            lm = 'draw' if key is None else (IDX[key][0] if key in IDX and lit_map(IDX[key][0]) else None)
        elif slots[l_] < len(ids) and lit_map(IDX[ids[slots[l_]]][0]):
            lm = IDX[ids[slots[l_]]][0]
    return ao, lm, win


def slot_map(no, slots, k):
    """the colour (not normal) texture at material slot k, or None"""
    ids = MT[no][0]
    if k is not None and k < len(slots) and slots[k] < len(ids):
        t_ = IDX[ids[slots[k]]][0]
        return t_ if tex.bix(t_) and tex.bix(t_)['fmt'] != 0x71 else None
    return None


def ao_map(no, slots, fx):
    """the AO map an object or ground shader samples (AOMapSampler, or AO_Sampler on ground and roads), at UV1. Ground reads it where
    it reads its splat mask, which blend materials already put on UV1"""
    return slot_map(no, slots, SAMP.get(fx, (None,) * 5)[0])


def spec_map(no, slots, fx):
    """the specular map an object shader samples (SpecularMapSampler), for rough_texture"""
    return slot_map(no, slots, SAMP.get(fx, (None,) * 5)[4])


def rough_texture(tid):
    """FH1's specular map as a BeamNG roughness map: shiny is smooth, 0.95 - 0.8 x its brightness, 0.15 to 0.95. FH1 uses it as specular
    strength with a fixed power, so the scale is by eye: siding (spec 0.1 to 0.25) comes out 0.75 to 0.87, glass (0.8) 0.31"""
    p_ = TX.d / f'r{tid:X}.data.dds'
    if not p_.exists():
        r_ = tex.load(tid)
        if not r_ or r_[0] is None:
            return None
        cooked(p_, (np.clip(.95 - .8 * r_[0][..., :3].mean(2) / 255, .15, .95) * 255).astype('u1'), limit=TEXMAX)
    return f'{rel}/art/textures/{p_.name}'


def window_mask(kind, tid):
    """A window mask as floats 0-1, or None when nothing in it glows. 'spec': FH1's *_emissive building shaders light where the specular
    map's green is over half, (g - 0.5) x 2: glass with curtains drawn in, against siding under 0.3. 'map': an emissive map as it is"""
    k_ = (kind, tid)
    if k_ not in WINMASK:
        r_ = tex.load(tid); m_ = None
        if r_ and r_[0] is not None:
            a_ = r_[0].astype('f4') / 255
            m_ = np.clip(a_[..., 1] * 2 - 1, 0, 1) if kind == 'spec' else a_[..., :3].max(2)
            m_ = m_ if (m_ > .1).mean() > .002 else None
        WINMASK[k_] = m_
    return WINMASK[k_]


def window_texture(kind, tid):
    """the mask as a texture for an emissiveMap on UV0: w<id>.color.dds"""
    name = f'w{kind[0]}{tid:X}'; p_ = level / 'art' / 'textures' / f'{name}.color.dds'
    if not p_.exists():
        m_ = (window_mask(kind, tid) * 255).astype('u1'); dds.save(p_, np.dstack([m_, m_, m_]))
    return f'{rel}/art/textures/{name}.color.dds'


def bake_emissive(lm, rgb, win, uv0, uv1, T):
    """One UV1 emissive for a section with both a lightmap and windows, as a material has one emissive map: the lightmap times the
    texture's mean colour (rgb) at LM_NITS, plus the window mask drawn into the section's UV1 charts, sampled at each texel's UV0
    (tiling), in WIN_RGB at WIN_NITS, both as shares of BAKE_NITS. Named by what goes in, so placements that share them share the texture"""
    h_ = hashlib.md5(np.ascontiguousarray(uv1[np.unique(T)]).tobytes() + np.ascontiguousarray(T).tobytes()).hexdigest()[:10]
    name = f'b{lm:X}_{win[0][0]}{win[1]:X}_{h_}'; p_ = level / 'art' / 'textures' / f'{name}.color.dds'
    if not p_.exists():
        L_ = tex.load(lm)[0][..., :3].astype('f4') / 255; S = BAKE
        out = (np.asarray(Image.fromarray((L_ * 255).astype('u1')).resize((S, S), Image.BICUBIC)).astype('f4') / 255) ** 2.2 * (np.asarray(rgb, 'f4') / 255)
        M_ = window_mask(*win); mh, mw = M_.shape
        P1 = uv1[T] * S; P0 = uv0[T]
        # which triangle covers each texel, drawn by PIL as index + 1 (outlines too, so charts bleed a texel), then UV0 there from barycentrics
        ix = Image.new('I', (S, S)); d_ = ImageDraw.Draw(ix)
        for i_, q1 in enumerate(P1):
            d_.polygon([tuple(v) for v in q1], fill=i_ + 1, outline=i_ + 1)
        ix = np.asarray(ix); ys, xs = np.nonzero(ix); k_ = ix[ys, xs] - 1; q1 = P1[k_]; q0 = P0[k_]
        px, py = xs + .5, ys + .5
        den = (q1[:, 1, 1] - q1[:, 2, 1]) * (q1[:, 0, 0] - q1[:, 2, 0]) + (q1[:, 2, 0] - q1[:, 1, 0]) * (q1[:, 0, 1] - q1[:, 2, 1])
        den = np.where(np.abs(den) < 1e-9, 1e-9, den)
        a = ((q1[:, 1, 1] - q1[:, 2, 1]) * (px - q1[:, 2, 0]) + (q1[:, 2, 0] - q1[:, 1, 0]) * (py - q1[:, 2, 1])) / den
        b = ((q1[:, 2, 1] - q1[:, 0, 1]) * (px - q1[:, 2, 0]) + (q1[:, 0, 0] - q1[:, 2, 0]) * (py - q1[:, 2, 1])) / den
        a = np.clip(a, 0, 1); b = np.clip(b, 0, 1 - a); u0 = a[:, None] * q0[:, 0] + b[:, None] * q0[:, 1] + (1 - a - b)[:, None] * q0[:, 2]
        add_ = np.zeros((S, S), 'f4'); add_[ys, xs] = M_[((u0[:, 1] % 1) * mh).astype(int) % mh, ((u0[:, 0] % 1) * mw).astype(int) % mw]
        out = (out * LM_NITS + add_[..., None] * np.asarray(WIN_RGB, 'f4') * WIN_NITS) / BAKE_NITS
        dds.save(p_, (np.clip(out, 0, 1) ** (1 / 2.2) * 255).astype('u1'))
    return f'{rel}/art/textures/{name}.color.dds'


def box_uv(t, fl):
    """UVs are u16 fractions t = v / 65535 of the mesh's UV box: fl[4:6] is its low corner and fl[6:8] its size. Reading them as s16
    shifted every UV by half the box, which tiling textures hide and atlases (signs, cars, facades) don't, and reading fl[6:8] as the
    high corner squashed billboard text. See formats.md"""
    return np.array(fl[4:6]) + t * np.array(fl[6:8])


def blend_slots(fx):
    """slot positions of a ground shader's colour layers. h_blnd2 has two: its slot 5 is a 64 px grey AO map, which drawn as a third
    layer laid white blotches over the ground wherever the mask's green was on. h_blnd3 has a colour layer there"""
    return (0, 1) if fx.startswith(('h_blnd1', 'h_blnd2')) else (0, 1, 5)


def rgmask(t):
    """blend masks are pure red and green. Some far sections list one where a colour layer should be"""
    i = TX.get(t)
    return i is None or (i['rgb'][2] < 24 and max(i['rgb'][:2]) > 64)


LAKE_NORMALS = (0x1295, 0x1296)      # the lake shader's two ripple normal maps. Its first slot, 0xD9D, isn't in the dump


def water():
    """lake_anim shader meshes. water.py swaps these 'wat' shapes for River objects"""
    if 'fh1_water' not in MATS:
        MATS['fh1_water'] = {'name': 'fh1_water', 'mapTo': 'fh1_water', 'class': 'Material', 'version': 1.5, 'materialTag0': 'beamng', 'translucent': True, 'translucentZWrite': False,
                             'Stages': [{'baseColorFactor': [.07, .16, .2, .78], 'roughnessFactor': .08, 'metallicFactor': 0, 'normalMap': TX.normal(LAKE_NORMALS[0]),
                                         'detailNormalMap': TX.normal(LAKE_NORMALS[1]), 'detailNormalMapStrength': .6, 'detailScale': [.3, .3]}, {}, {}, {}]}
    return 'fh1_water'


def additive(tid, cs):
    """FH1's add_diff_opac_rgba (the waterfalls, TERR_Fuji_Waterfall_*): lit diffuse blended additively, so black is clear. Its pixel shader adds
    frac(TimeGain / c360_0.xy) to the diffuse UV, about 0.01 u and 0.67 v a second down the fall. BeamNG's Stage scroll adds scrollDir *
    scrollSpeed * time the same way, and V reaches it unflipped (daewrite stores 1 - v, the importer flips it back). FH1's opacity mask stays put
    while the diffuse moves; BeamNG would scroll both, so it's left out: additive black already fades the sheet"""
    t = TX.get(tid)
    if t is None:
        return None
    c_ = cs[0, :2] if len(cs) else np.zeros(2); d_ = [round(1 / float(v), 4) if abs(v) > 1e-3 else 0 for v in c_]
    key = f"fh1_{t['name']}_add" + ('_s%g_%g' % tuple(d_) if any(d_) else '')
    if key not in MATS:
        st = {'baseColorMap': f"{rel}/art/textures/{t['name']}.color.dds", 'roughnessFactor': 1, 'metallicFactor': 0}
        if any(d_):
            st.update({'animFlags': 1, 'scrollDir': d_, 'scrollSpeed': 1})
        MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng',
                     'translucent': True, 'translucentBlendOp': 'Add', 'translucentZWrite': False, 'doubleSided': True, 'castShadows': False}
    return key


def flat():
    """for wire whose texture isn't in the dump"""
    if 'fh1_flat' not in MATS:
        MATS['fh1_flat'] = {'name': 'fh1_flat', 'mapTo': 'fh1_flat', 'class': 'Material', 'version': 1.5, 'materialTag0': 'beamng', 'Stages': [{'baseColorFactor': [.09, .09, .08, 1], 'roughnessFactor': .6, 'metallicFactor': .5}, {}, {}, {}]}
    return 'fh1_flat'


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


cells = defaultdict(lambda: defaultdict(list)); RAILS = defaultdict(list); stats = collections.Counter(); surf = collections.Counter(); start_ = t0 = time.time()


LV = (0,); FIX = None; DROPPED = collections.Counter()
BACK = []; BPRI = (-1,)      # triangles beyond the square, for backdrop(), with BPRI: (-1,) for fine ground, else (0, the far mesh's median edge)


def cell_split(b, nb, uv, uv1, T):
    """Cut triangles along the cell lines. A group over 1 km has no single cell, and split by centroid, a cell drawing its LOD2 beside one
    drawing LOD0 left holes where triangles straddled the line, while coarse ones poked through finer ground. Cut, each cell holds exactly its part"""
    A = np.concatenate([b, nb, uv, uv1], 1); S = A[T]
    for ax in (0, 1):
        done = []
        while len(S):
            lo_ = S[:, :, ax].min(1); hi_ = S[:, :, ax].max(1)
            X = (np.floor((lo_ + HALF) / CELL) + 1) * CELL - HALF       # the first line above each triangle's lowest corner
            cut = X < hi_ - 1e-4
            done.append(S[~cut]); S = S[cut]; X = X[cut]
            if not len(S):
                break
            s_ = S[:, :, ax] - X[:, None]; pos = s_ > 0
            lone = np.where(pos.sum(1) == 1, pos.argmax(1), (~pos).argmax(1))      # the corner alone on its side goes first, keeping the winding
            r_ = (lone[:, None] + np.arange(3)) % 3; S = np.take_along_axis(S, r_[:, :, None], 1); s_ = np.take_along_axis(s_, r_, 1)
            a_, b_, c_ = S[:, 0], S[:, 1], S[:, 2]
            p_ = a_ + (b_ - a_) * (s_[:, 0] / (s_[:, 0] - s_[:, 1]))[:, None]; q_ = a_ + (c_ - a_) * (s_[:, 0] / (s_[:, 0] - s_[:, 2]))[:, None]
            S = np.concatenate([np.stack(v, 1) for v in ((a_, p_, q_), (p_, b_, c_), (p_, c_, q_))])
        S = np.concatenate(done)
    S = S[np.linalg.norm(np.cross(S[:, 1, :3] - S[:, 0, :3], S[:, 2, :3] - S[:, 0, :3]), axis=1) > 1e-6]
    V = S.reshape(-1, A.shape[1]); n_ = V[:, 3:6]; n_ /= np.maximum(np.linalg.norm(n_, axis=1, keepdims=True), 1e-9)
    return V[:, :3], n_, V[:, 6:8], V[:, 8:], np.arange(len(V)).reshape(-1, 3)


def add(cat, mat, p, n, uv, T, lift=0.0, uv1=None):
    if not len(T):
        return
    b = g2b(p); nb = n2b(n)
    if lift:
        b = b + np.array([0, 0, lift])
    c = b[T].mean(1); ins = (np.abs(c[:, :2]) < HALF - 4).all(1)
    if BACKDROP and 2 in LV and not ins.all():        # the backdrop is one detail level, so it takes what's drawn furthest out
        BACK.append((BPRI, cat, mat, b, nb, uv, T[~ins], uv1 if uv1 is not None else uv))
    T = T[ins]; c = c[ins]
    if not len(T):
        return
    if FIX is None and LV != (0, 1, 2):
        b, nb, uv, uv1, T = cell_split(b, nb, uv, uv1 if uv1 is not None else uv, T); c = b[T].mean(1)
    key = np.floor((c[:, :2] + HALF) / CELL).astype(int); k = key[:, 0] * 1000 + key[:, 1] if FIX is None else np.full(len(T), FIX)
    for u in np.unique(k):
        sel = T[k == u]; idx, inv = np.unique(sel, return_inverse=True)
        cells[(int(u), cat)][(mat, LV)].append((b[idx], nb[idx], uv[idx], inv.reshape(-1, 3).astype(np.int64), uv1[idx] if uv1 is not None else uv[idx]))
        if cat == 'bar' and 0 in LV:
            RAILS[int(u)].append(b[sel])
    stats[cat] += len(T)


# ---- world meshes
def lake(c):
    """Mountains_Water_03, Finley's reservoir, shares a mesh with water 14 km off the map, so it's exempt from the size limit. add() drops the far part"""
    no = int(c['f'].split('.')[1])
    return no < len(MT) and any(SHADERS[s].startswith('lake') for s in MT[no][1])


sel = [c for c in F if (BACKDROP or c['mx'][0] > X0 and c['mn'][0] < X1 and c['mx'][2] > Z0 and c['mn'][2] < Z1)
       and (BACKDROP or max(c['mx'][0] - c['mn'][0], c['mx'][2] - c['mn'][2]) < 6000 or 'UberLOD' in c['name'] or lake(c)) and not NOUSE.search(c['name'])
       and int(c['f'].split('.')[1]) not in NODRAW]
# Origin-local meshes only belong where a zone record puts them, so they must not also go in raw at (0, 0).
# Any record away from the origin marks a whole LOD group as local. World-space meshes have records at the origin.
ZREC = pickle.load(open('../zonerec.pkl', 'rb')); NAMEOF = {int(c['f'].split('.')[1]): c['name'] for c in F}
GENERIC = re.compile(r'(Plane|Box|Line|Object|Mesh|Rectangle)\d+', re.I)        # exporter default names, shared by unrelated meshes


def lod_rings():
    """FH1's own LOD groups, from the draw table. p is a signed offset to the next record of the mesh's group, and following it comes
    back round: rings of 3 (LOD0, 1, 2), 2 (LOD0 and a coarse one) or 1. g bits 12-13 are the level, where 3 is the coarse member
    of a pair and most lone meshes, and bit 11 marks the top member. Every file sits in one ring at one level.
    -> {file: ring id}, {file: level}"""
    n_ = len(DRAW); p_ = DRAW['p'].astype(np.int64); p_ = np.where(p_ >= 32768, p_ - 65536, p_); lv_ = (DRAW['g'].astype(int) >> 12) & 3
    ring = {}; level = {}
    for i in range(n_):
        t = int(DRAW['t'][i])
        if t in ring:
            continue
        out = [i]; j = i
        for _ in range(8):
            j += int(p_[j])
            if j == i or not 0 <= j < n_:
                break
            out.append(j)
        if j != i:      # a few hundred records point elsewhere, into some other hierarchy
            continue
        r_ = ('ring', min(int(DRAW['t'][k]) for k in out))
        for k in out:
            ring[int(DRAW['t'][k])] = r_; level[int(DRAW['t'][k])] = int(lv_[k])
    return ring, level


RING, LEVEL = lod_rings()


def lod_groups(cs, margin=25.0):
    """A group per mesh: its file's ring in the draw table. Names are only a fallback: they joined every Object025 on the map into one
    group 12 km wide, and exporters misnamed some LODs (ROAD_CLRD_Mountains_Area03_53_LOD00 has _49_LOD01). The few files with no ring
    group by lodkey and overlapping boxes"""
    by = defaultdict(list); out = {}
    for c in cs:
        t = int(c['f'].split('.')[1])
        if t in RING:
            out[(c['f'], c['mi'])] = RING[t]
        else:
            by[lodkey(c['name'])].append(c)
    for k, g in by.items():
        lo = np.array([[c['mn'][0], c['mn'][2]] for c in g]) - margin; hi = np.array([[c['mx'][0], c['mx'][2]] for c in g]) + margin
        ov = ((lo[:, None] < hi[None]) & (lo[None] < hi[:, None])).all(2)
        _, lab = connected_components(ov, directed=False)
        for c, l_ in zip(g, lab):
            out[(c['f'], c['mi'])] = (k, int(l_))
    return out


GID = lod_groups(sel); GFILES = defaultdict(set); FILEMESH = defaultdict(list)
for c in sel:
    GFILES[GID[(c['f'], c['mi'])]].add(int(c['f'].split('.')[1])); FILEMESH[int(c['f'].split('.')[1])].append(c)
print('LOD groups from the draw table', sum(k[0] == 'ring' for k in GID.values()), 'by name', sum(k[0] != 'ring' for k in GID.values()))


def gkey(c):
    return GID[(c['f'], c['mi'])]


def lone_copies(cs):
    """Meshes alone in their ring with a finer LOD of the same name over them: spare LOD2 copies such as Reservoir_Area6_PATCH_01_LOD02 in
    file 14474 (g 0x7814), beside the real ring 13203-13205. Each is its own group's best, so it went in as close ground over the fine tiles"""
    ring_n = collections.Counter(RING.values()); by = defaultdict(list); out = set()
    key = lambda n: lodkey(re.sub(r'_CHOP\d+', '', n))     # Reservoir_Area6_PATCH_02_CHOP21_LOD02: a piece of _PATCH_02's LOD2 over its LOD00, in one 256 px texture
    for c in cs:
        by[key(c['name'])].append(c)
    for c in cs:
        t = int(c['f'].split('.')[1])
        if t not in RING or ring_n[RING[t]] > 1:
            continue
        if any(lod(d['name']) < lod(c['name']) and int(d['f'].split('.')[1]) != t and d['mn'][0] < c['mx'][0] and c['mn'][0] < d['mx'][0]
               and d['mn'][2] < c['mx'][2] and c['mn'][2] < d['mx'][2] for d in by[key(c['name'])]):
            out.add((c['f'], c['mi']))
    return out


LONE = lone_copies(sel)
print('lone LOD copies left out', len(LONE))


def lod_level(c):
    """a mesh's LOD level: 0 is finest, 3 the coarse member of a pair. From the draw table, else the name"""
    return LEVEL.get(int(c['f'].split('.')[1]), lod(c['name']))


moved = set(); movedno = set(); atzero = set(); SHELL = set(); COARSE = set(); WORLDLM = {}      # SHELL: FH1 never culls these. They're the coarse ground behind everything
for (did, p_), r_ in ZREC.items():
    t = int(DRAW['t'][did])
    if abs(r_['pos'][0]) + abs(r_['pos'][2]) >= 1:
        movedno.add(t)
        moved.update(gkey(c) for c in FILEMESH.get(t, ()) if gkey(c)[0] == 'ring' or not GENERIC.match(lodkey(c['name'])))
    elif not (r_['lst'] or r_['name']):
        atzero.add(t); WORLDLM[t] = int(DRAW['s'][did][1])     # the draw's own lightmap, for materials that take one from it
        if r_['lod'][0] >= 1e5:
            SHELL.add(t)


def world_mesh(c):
    """The game only draws a world-space mesh through a free-roam record at the origin. A mesh with no record is never loaded,
    and a placeholder texture marks FH1's blocked-out ground"""
    no = int(c['f'].split('.')[1])
    if no in movedno or gkey(c) in moved or no not in atzero:
        return False
    return not (no < len(MT) and any(IDX[i][0] == PLACEHOLDER for i in MT[no][0]))


n_ = len(sel); sel = [c for c in sel if world_mesh(c)]
print('meshes left out of the world path', n_ - len(sel))
best = defaultdict(lambda: 99); AV = defaultdict(set); BOX = {}
for c in sel:      # a large, sparse terrain mesh is coarse ground too: fine tiles are 7,000 tris per km2 and up
    a_ = (c['mx'][0] - c['mn'][0]) * (c['mx'][2] - c['mn'][2]) / 1e6
    if TERR.search(c['name']) and not FAR.search(c['name']) and a_ > .3 and c['nt'] < 1000 * a_:
        COARSE.add((c['f'], c['mi']))
for c in sel:
    if int(c['f'].split('.')[1]) in SHELL or (c['f'], c['mi']) in COARSE:
        continue
    k = gkey(c); best[k] = min(best[k], lod_level(c)); AV[k].add(lod_level(c))
    b_ = BOX.setdefault(k, [c['mn'][0], c['mx'][0], c['mn'][2], c['mx'][2]])
    b_[0] = min(b_[0], c['mn'][0]); b_[1] = max(b_[1], c['mx'][0]); b_[2] = min(b_[2], c['mn'][2]); b_[3] = max(b_[3], c['mx'][2])


def group_cell(k):
    """All of a mesh's LODs share one shape, so its detail switches as a unit. Split by triangle, a cell could drop LOD0 with its coarse triangles held by a nearer cell.
    A group over 1 km gets None: add() then cuts its triangles along the cell lines, so each cell holds exactly its own part of every LOD"""
    b_ = BOX[k]
    if max(b_[1] - b_[0], b_[3] - b_[2]) > 1000:
        return None
    q_ = np.floor((g2b(np.array([[(b_[0] + b_[1]) / 2, 0, (b_[2] + b_[3]) / 2]]))[0, :2] + HALF) / CELL).astype(int)
    return int(np.clip(q_[0], 0, 999)) * 1000 + int(np.clip(q_[1], 0, 999))
print(len(sel), 'meshes in box')
Road = np.full((N, N), np.nan, 'f8'); Water = np.full((N, N), np.nan, 'f8'); Cov = np.full((N, N), np.nan, 'f8'); Rock = np.full((N, N), np.nan, 'f8')

# ---- ground chosen by FH1's zone hexes (FH1_HEXGROUND=1)
# Each 100 m hex loads and unloads what FH1 streams while the player is in it, and each record gives its LOD group's switch distances. A HG
# cell takes the ground groups loaded by the nearest hex listing them (see hex_selection): what a player sees from the closest viewpoint.
# That replaces the far tiers' priorities and every footprint and height rule for water and rock. Cells no hex draws ground for take the
# finest ground over them, close-range ground (g bit 3) first. Clear Springs 2026-11-04: no visible holes (user-checked).
# FH1_HEXGROUND=0 goes back to the far tiers and their clipping
HEXGROUND = os.environ.get('FH1_HEXGROUND', '1') == '1'; HG = 16.0; HEX_K = 8
GROUNDFX = re.compile(r'^(h_blnd|h_vblnd|h_road)'); NOTGROUND = re.compile(r'sign|barrier|armco|fence|rail|wall|pole|bridge|decal|cable|building|^TERR_CUBE', re.I)
HN = (int(np.ceil((X1 - X0) / HG)), int(np.ceil((Z1 - Z0) / HG)))
HEXSEL = {}; HEXRING = {}       # ring -> sorted selected cell numbers; file -> ring, for the ground files


@njit(cache=True)
def hg_cells(G, nx, ny, out):
    """HG cells of each triangle (grid coords): its centroid's, and every cell whose centre it covers -> count written"""
    k = 0
    for t in range(G.shape[0]):
        ax, ay = G[t, 0, 0], G[t, 0, 1]; bx, by = G[t, 1, 0], G[t, 1, 1]; cx, cy = G[t, 2, 0], G[t, 2, 1]
        mi, mj = int((ax + bx + cx) / 3), int((ay + by + cy) / 3)
        if 0 <= mi < nx and 0 <= mj < ny and k < out.shape[0]:
            out[k] = mj * nx + mi; k += 1
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) < 1e-9:
            continue
        for j in range(max(int(math.floor(min(ay, by, cy))), 0), min(int(math.ceil(max(ay, by, cy))) + 1, ny)):
            for i in range(max(int(math.floor(min(ax, bx, cx))), 0), min(int(math.ceil(max(ax, bx, cx))) + 1, nx)):
                px = i + .5; py = j + .5
                w0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d; w1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                if w0 >= 0 and w1 >= 0 and w0 + w1 <= 1 and k < out.shape[0]:
                    out[k] = j * nx + i; k += 1
    return k


@njit(cache=True)
def hg_class(G, sel, nx, ny):
    """per triangle (grid coords): 0 if no cell it covers is chosen, 1 if all are, 2 if mixed. Covered: the cells of its corners and edge
    midpoints, and every cell whose centre it holds. Outside the grid counts as chosen (sel's last entry)"""
    out = np.zeros(G.shape[0], np.int8)
    for t in range(G.shape[0]):
        ax, ay = G[t, 0, 0], G[t, 0, 1]; bx, by = G[t, 1, 0], G[t, 1, 1]; cx, cy = G[t, 2, 0], G[t, 2, 1]
        yes = False; no = False
        for k in range(6):
            if k < 3:
                px, py = G[t, k, 0], G[t, k, 1]
            else:
                u = G[t, k - 3]; v = G[t, (k - 2) % 3]; px, py = (u[0] + v[0]) / 2, (u[1] + v[1]) / 2
            i, j = int(math.floor(px)), int(math.floor(py))
            s_ = sel[-1] if i < 0 or j < 0 or i >= nx or j >= ny else sel[j * nx + i]
            if s_:
                yes = True
            else:
                no = True
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(d) > 1e-12 and not (yes and no):
            for j in range(max(int(math.floor(min(ay, by, cy))), 0), min(int(math.ceil(max(ay, by, cy))) + 1, ny)):
                for i in range(max(int(math.floor(min(ax, bx, cx))), 0), min(int(math.ceil(max(ax, bx, cx))) + 1, nx)):
                    px = i + .5; py = j + .5
                    w0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d; w1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
                    if w0 >= 0 and w1 >= 0 and w0 + w1 <= 1:
                        if sel[j * nx + i]:
                            yes = True
                        else:
                            no = True
                        if yes and no:
                            break
                if yes and no:
                    break
        out[t] = 2 if yes and no else 1 if yes else 0
    return out


def hg_grid(p):
    """FH1 positions to HG grid coords"""
    return np.stack([(p[..., 0] - X0) / HG, (p[..., 2] - Z0) / HG], -1)


def is_ground(c):
    """ground for hex_selection: terrain by name, or meshes drawn only with ground and road shaders (lake sections allowed)"""
    no = int(c['f'].split('.')[1]); fx_ = [SHADERS[s_] for s_ in MT[no][1]] if no < len(MT) else []
    terr_ = TERR.search(c['name']) or FAR.search(c['name'])
    return c['sd'] >= 20 and not NOTGROUND.search(c['name']) and bool(terr_ or fx_ and all(GROUNDFX.match(s_) or s_.startswith('lake') for s_ in fx_) and any(GROUNDFX.match(s_) for s_ in fx_))


def zone_objects(cs):
    """World-space objects (not ground: hex_selection gates that per cell) that some zone near them loads. A mesh is kept when, at any of up to
    64 points over its box, one of the HEX_K nearest hexes loads its LOD ring. 5,886 of 6,062 pass over their whole box. The rest pass nowhere:
    58 power line spans no zone loads at all (817 others are loaded), about 100 catalog meshes whose only record is at the origin, 22 m
    underground there, and spare LODs such as Barrier_020_Armco_LOD01_019. -> the meshes kept"""
    HX, _ = hexzones.hexes(); zn = sorted(HX); tree = cKDTree(np.array([HX[z] for z in zn])); loads = defaultdict(set)
    for i, z in enumerate(zn):
        for d in hexzones.drawn(z):
            if d < len(DRAW):
                loads[int(DRAW['t'][d])].add(i)
    mem = defaultdict(list)
    for f_, r_ in RING.items():
        mem[r_].append(f_)
    out = []
    for c in cs:
        no = int(c['f'].split('.')[1])
        if is_ground(c):
            out.append(c)
            continue
        zl = set().union(*[loads.get(f_, set()) for f_ in (mem[RING[no]] if no in RING else [no])])
        if zl:
            xs = np.linspace(c['mn'][0], c['mx'][0], int(np.clip((c['mx'][0] - c['mn'][0]) / 50, 2, 8)))
            zs = np.linspace(c['mn'][2], c['mx'][2], int(np.clip((c['mx'][2] - c['mn'][2]) / 50, 2, 8)))
            X, Z_ = np.meshgrid(xs, zs); _, h = tree.query(np.c_[X.ravel(), -Z_.ravel()], k=HEX_K)
            if any(zn_ in zl for zn_ in h.ravel().tolist()):
                out.append(c)
                continue
        stats['zone_objects_dropped'] += 1; stats['zone_objects_dropped_tris'] += c['nt']; DROPPED['no zone loads: ' + re.sub(r'\d+', '#', c['name'])[:30]] += c['nt']
    return out


def structure_tris():
    """Large placed structures, such as Finley Dam's 300 m wall, stand in holes in the terrain meshes. -> [(FH1 positions, triangles)], one per
    placement. Free-roam records only, at LOD0. Bridges, turbines, conveyors and light rigs span open ground, so they'd cut holes under themselves"""
    byno = defaultdict(list); out = []; OPEN = re.compile(r'bridge|turbine|conveyor|^Lights', re.I)
    for c in F:
        if c['sd'] >= 20 and lod(c['name']) == 0 and max(c['mx'][0] - c['mn'][0], c['mx'][2] - c['mn'][2]) >= 50 and not (NOUSE.search(c['name']) or OPEN.search(c['name'])):
            byno[int(c['f'].split('.')[1])].append(c)
    for (did, p_), r_ in ZREC.items():
        cs = byno.get(int(DRAW['t'][did]))
        if not cs or r_['lst'] or r_['name'] or abs(r_['pos'][0]) + abs(r_['pos'][2]) < 1:
            continue
        Rg = np.array(r_['rot']).T; sc = float(np.linalg.norm(Rg[:, 1])); Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
        F_ = np.diag([1., 1., -1.]); R_ = (F_ @ Rg if np.linalg.det(Rg) < 0 else F_ @ Rg @ F_) * np.clip(sc, .2, 5)      # game_rot, defined further down
        o_ = np.array([r_['pos'][0], r_['pos'][1], -r_['pos'][2]]); P_ = []; T_ = []
        for c in cs:
            m = rmb.parse(open('../x/' + c['f'], 'rb').read())['meshes'][c['mi']]; p, _, _ = rmb.verts(m)
            T_ += [rmb.tris(s['ib'], m['nv'], s['ints'][1]) + sum(len(q) for q in P_) for s in m['secs']]; P_.append(p @ R_.T + o_)
        out.append((np.concatenate(P_), np.concatenate(T_)))
    return out


STRUCTS = structure_tris()


def hex_selection(cs):
    """-> {ring: sorted selected cells}, {file: ring}. cs: the world-path meshes"""
    lod_of = {}
    for (did, p_), r_ in ZREC.items():
        if abs(r_['pos'][0]) + abs(r_['pos'][2]) < 1 and not (r_['lst'] or r_['name']):
            lod_of.setdefault(int(DRAW['t'][did]), r_['lod'])
    mem = defaultdict(list)
    for f_, r_ in RING.items():
        mem[r_].append(f_)
    ring_of = {}; foot = defaultdict(list); ntri = collections.Counter(); name_ = {}; buf = np.zeros(8_000_000, np.int64)
    byf = defaultdict(list)
    for c in cs:
        if is_ground(c):
            byf[c['f']].append(c)
    for f_, cs_ in byf.items():
        P_ = rmb.parse(open('../x/' + f_, 'rb').read()); no = int(f_.split('.')[1])
        for c in cs_:
            m = P_['meshes'][c['mi']]; p, _, _ = rmb.verts(m)
            T = np.concatenate([rmb.tris(s['ib'], m['nv'], s['ints'][1]) for s in m['secs']] or [np.zeros((0, 3), int)])
            if not len(T):
                continue
            r_ = RING.get(no, ('file', no)); ring_of[no] = r_; name_[r_] = name_.get(r_) or c['name']
            k = hg_cells(hg_grid(p)[T], HN[0], HN[1], buf); foot[r_].append(buf[:min(k, len(buf))].copy()); ntri[r_] += len(T)
    rings_ = sorted(foot, key=str); ri = {r_: i for i, r_ in enumerate(rings_)}
    pc = []; pr = []
    for r_ in rings_:
        u = np.unique(np.concatenate(foot[r_])); pc.append(u); pr.append(np.full(len(u), ri[r_]))
    pc = np.concatenate(pc); pr = np.concatenate(pr)       # candidate (cell, ring) pairs
    dens = np.array([ntri[r_] / max(len(np.unique(np.concatenate(foot[r_]))), 1) for r_ in rings_])
    HX, HR = hexzones.hexes(); zn = sorted(HX); ZC = np.array([HX[z] for z in zn])
    load = [{int(DRAW['t'][d]) for d in hexzones.drawn(z) if d < len(DRAW)} for z in zn]       # the ids a zone loads (bit 31)
    cj, ci = np.divmod(np.arange(HN[0] * HN[1]), HN[0]); cx_ = X0 + (ci + .5) * HG; cz_ = Z0 + (cj + .5) * HG
    dK, hK = cKDTree(ZC).query(np.c_[cx_, -cz_], k=HEX_K)
    ms_ = [set(mem[r_]) if r_ in mem else {r_[1]} for r_ in rings_]
    Lh = defaultdict(list); byfile = defaultdict(list)
    for a, m_ in enumerate(ms_):
        for f_ in m_:
            byfile[f_].append(a)
    for k_ in range(len(zn)):
        for f_ in load[k_]:
            for a in byfile.get(f_, ()):
                Lh[a].append(k_)
    Ls = {a: set(v) for a, v in Lh.items()}
    cull = {}
    for a in range(len(rings_)):        # the record's last switch distance is where FH1 stops drawing the group; 131008 means never
        sw_ = next((lod_of[f_] for f_ in ms_[a] if f_ in lod_of), None)
        cull[a] = np.inf if sw_ is None or sw_[0] < 0 or sw_[2] >= 1e5 else sw_[2]

    # The lists are changes, not states: a fine tile is loaded from every hex over it and listed nowhere past 1 km, and the Uber shell over
    # Clear Springs is listed by 6 of the 105 hexes inside its box, loaded from 1.6 km. Counting unlisted hexes as loaded drew that shell 2 m
    # over the fine tiles. Unloads don't say a group is gone either: Reservoir_Area6_PATCH_10 is unloaded by a hex inside its own box with
    # loading ones all round, and taking that as final left a 26 m hole. So the nearest of HEX_K hexes that loads a group decides: drawn
    # there short of the cull distance
    sel = np.zeros(len(pc), bool); decided = np.zeros(len(pc), bool)
    for q in range(HEX_K):
        o_ = np.flatnonzero(~decided)
        if not len(o_):
            break
        key = np.stack([pr[o_], hK[pc[o_], q], (dK[pc[o_], q] // 10).astype(np.int64)], 1)
        uk, inv = np.unique(key, axis=0, return_inverse=True); inv = inv.ravel()
        ld_ = np.array([h in Ls.get(a, ()) for a, h, b in uk])
        ok = np.array([l and (b + .5) * 10 < cull[a] for (a, h, b), l in zip(uk, ld_)])
        decided[o_[ld_[inv]]] = True; sel[o_[ok[inv]]] = True
    done = np.zeros(HN[0] * HN[1], bool); done[pc[sel]] = True
    # copies of one tile in separate rings, such as TERR_CLRD_FEST_AREA_09_LOD00 and _LOD02: the finest in each cell
    lk = np.array([hash(lodkey(name_[r_])) for r_ in rings_]); lv = np.array([lod(name_[r_]) for r_ in rings_])
    s_ = np.flatnonzero(sel); o_ = s_[np.lexsort((lv[pr[s_]], lk[pr[s_]], pc[s_]))]
    first = np.r_[True, (pc[o_][1:] != pc[o_][:-1]) | (lk[pr[o_]][1:] != lk[pr[o_]][:-1])]
    lead = lv[pr[o_]][np.maximum.accumulate(np.where(first, np.arange(len(o_)), 0))]
    sel[o_[lv[pr[o_]] > lead]] = False
    # cells no hex draws ground for: the finest ground over them, except under large structures. Those stand in holes in the terrain, and
    # the only ground over Finley Dam's is the coarse shell Mountains_Area04A_Terrain_05, which FH1's hexes there never load: filled with
    # it, its 100 m triangles hung over the dam's face as a tan dome
    held = np.zeros(HN[0] * HN[1], bool)
    for p, T in STRUCTS:
        k = hg_cells(hg_grid(p)[T], HN[0], HN[1], buf); held[buf[:min(k, len(buf))]] = True
    near = np.array([any(((int(g_) >> 3) & 1) for f_ in ms_[a] for g_ in GBITS.get(f_, ())) for a in range(len(rings_))])     # g bit 3: close-range ground
    o_ = np.flatnonzero(~done[pc] & ~held[pc]); o_ = o_[np.lexsort((-dens[pr[o_]], ~near[pr[o_]], pc[o_]))]
    stats['hex_cells_structure'] = int((held & ~done).sum())
    first = np.r_[True, pc[o_][1:] != pc[o_][:-1]]; sel[o_[first]] = True
    fb = collections.Counter((name_[rings_[pr[i]]], bool(decided[i])) for i in o_[first])      # decided: a hex among the nearest loads it, past its cull distance
    print('hex fallback by ring (name, loaded by a near hex): cells', fb.most_common(15))
    stats['hex_cells_fallback'] = int(first.sum()); stats['hex_cells_drawn'] = int(done.sum())
    out = {r_: np.sort(pc[sel & (pr == i)]) for i, r_ in enumerate(rings_)}
    print('hex ground', len(rings_), 'groups,', int(done.sum()), 'cells from hexes,', int(first.sum()), 'filled by the finest ground over them', flush=True)
    return out, ring_of


def hex_split(no, A, T):
    """A ground mesh's triangles (A: vertex rows, position first) where its group is chosen. A triangle up to two cells across is kept whole
    when any cell it covers is chosen, so where one group hands over to another they overlap by a triangle rather than part. Cut on the cell
    lines and kept by centre, pieces on such a line were dropped from both, leaving slivers of sky, and the cuts left T-junctions that crack.
    Bigger triangles go by the exact cells they cover, whole if those agree, else split in four and looked at again"""
    r_ = HEXRING.get(no)
    if r_ is None or not len(T):
        return A, T
    cells = HEXSEL.get(r_, np.zeros(0, np.int64))
    sel_ = np.zeros(HN[0] * HN[1] + 1, bool); sel_[cells] = True; sel_[-1] = True      # the last entry: outside the grid, kept (the backdrop's)

    def cell(i_, j_):
        out_ = (i_ < 0) | (j_ < 0) | (i_ >= HN[0]) | (j_ >= HN[1])
        return np.where(out_, HN[0] * HN[1], np.clip(j_, 0, HN[1] - 1) * HN[0] + np.clip(i_, 0, HN[0] - 1))
    S = A[T]; kept = []; mixed = []
    while len(S):       # big triangles: by the exact cells they cover, whole if those agree, else split in four and looked at again
        g_ = hg_grid(S[:, :, :3]); span = (np.floor(g_.max(1)) - np.floor(g_.min(1))).max(1)
        big = span > 2; mixed.append(S[~big]); S = S[big]
        if not len(S):
            break
        cl = hg_class(hg_grid(S[:, :, :3]), sel_, HN[0], HN[1])
        kept.append(S[cl == 1]); S = S[cl == 2]
        if len(S):
            a_, b_, c2 = S[:, 0], S[:, 1], S[:, 2]; ab = (a_ + b_) / 2; bc = (b_ + c2) / 2; ca = (c2 + a_) / 2
            S = np.concatenate([np.stack(v, 1) for v in ((a_, ab, ca), (ab, b_, bc), (ca, bc, c2), (ab, bc, ca))])
    S = np.concatenate(mixed) if mixed else A[:0].reshape(0, 3, A.shape[1])
    g_ = hg_grid(S[:, :, :3]); lo = np.floor(g_.min(1)).astype(np.int64); hi = np.floor(g_.max(1)).astype(np.int64)
    if len(S):         # every cell of the box, up to 3 by 3
        ii = lo[:, 0, None] + np.arange(3)[None]; jj = lo[:, 1, None] + np.arange(3)[None]
        inb = (ii[:, :, None] <= hi[:, 0, None, None]) & (jj[:, None, :] <= hi[:, 1, None, None])
        k_ = sel_[cell(ii[:, :, None], jj[:, None, :])]
        kept.append(S[(k_ & inb).any((1, 2))])       # kept whole where any cell it covers is chosen: no cut, so no T-junction to crack open
    V = np.concatenate(kept).reshape(-1, A.shape[1]) if kept else A[:0]
    stats['hex_tris_in'] += len(T); stats['hex_tris_out'] += len(V) // 3
    return V, np.arange(len(V)).reshape(-1, 3)


if HEXGROUND:
    BARY = np.array([[a_, b_, 8 - a_ - b_] for a_ in range(9) for b_ in range(9 - a_)], 'f8') / 8
    HEXSEL, HEXRING = hex_selection(sel)
    lap('hex ground')
    sel = zone_objects(sel)
    lap('zone objects')
byfile = defaultdict(list)
for c in sel:
    byfile[c['f']].append(c)
for f, cs in byfile.items():
    b = open('../x/' + f, 'rb').read(); P = rmb.parse(b); M = materials(b, P); no = int(f.split('.')[1])
    if len(M) < 1 + max((s['ints'][2] for m in P['meshes'] for s in m['secs']), default=0):
        stats['file_short_mats'] += 1
    for c in cs:
        m = P['meshes'][c['mi']]; n = c['name']; l = lod_level(c)
        if m['sd'] < 20:
            stats['stride16'] += 1
            continue
        p, nr, _ = rmb.verts(m); r0, r1 = uv_sets(m)
        vw = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])[:, -4:].astype('f8') / 255 if m['sd'] >= 28 else None      # alpha, then layer weights
        shell_ = no in SHELL or (c['f'], c['mi']) in COARSE
        cube = (bool(FAR.search(n)) or shell_) and 'water' not in n.lower()
        isterr = (bool(TERR.search(n)) or cube) and not re.search(r'_rocks?(_|$)|armco|barrier|_road|water', n, re.I)
        g = togrid(p)
        if isterr:
            if cube:
                if 'TERR_CUBE' not in n and l <= best[gkey(c)] and (c['f'], c['mi']) not in LONE:
                    # coarse by measured density, as names mislead: TERR_UberLOD_Patch is the coarse shell, at 240 tris per km2 against PATCH's 21,000
                    d_ = c['nt'] / max((c['mx'][0] - c['mn'][0]) * (c['mx'][2] - c['mn'][2]) / 1e6, 1e-3)
                    FARQ.append((d_ < 1500, no, M, m, p, nr, r0, r1, g, gkey(c)))
                continue
        have_ = sorted(AV[gkey(c)]); rel_ = have_.index(l)
        if rel_ > 1 and isterr or rel_ > 2:     # terrain LOD1 and LOD2 are close in size, so LOD1 covers both
            stats['lod_dropped'] += 1
            continue
        # a group's coarsest mesh also stands in for any lower LODs it lacks
        LV = tuple(range(rel_, 3)) if rel_ == len(have_) - 1 or isterr and rel_ == 1 else (rel_,)
        FIX = group_cell(gkey(c))
        near_ = rel_ == 0; base_ = (p, nr, r0, r1, vw, g)
        for si_, s in enumerate(m['secs']):
            p, nr, r0, r1, vw, g = base_
            T = rmb.tris(s['ib'], m['nv'], s['ints'][1])
            q_ = MT[no][1]
            if len(T) and s['ints'][2] < len(q_) and SHADERS[q_[s['ints'][2]]].startswith('lake'):      # materials() can't read this shader's record
                add('wat', water(), p, nr, box_uv(r0, s['fl']), T); surf['water'] += len(T); raster(Water, g[T])
                continue
            if not len(T) or s['ints'][2] >= len(M):
                stats['sec_nomat'] += 1; DROPPED[re.sub(r'\d+', '#', n)[:30]] += len(T)
                continue
            si, slots, cs_ = M[s['ints'][2]]; NM = section_normals(no, slots)
            fx = SHADERS[MT[no][1][si]] if si < len(MT[no][1]) else ''
            sh_ = vertex_shade(no, slots, fx, m, s) if fx.startswith(('h_road', 'h_vblnd')) and vw is not None else None      # read per vertex, before hex_split
            if HEXGROUND and no in HEXRING and (isterr or fx.startswith('h_road')):     # only where FH1's hexes draw this ground. Objects in
                # the same mesh (rock, props) read their UV1 from its vertex buffer, so they stay whole
                cols_ = [a_ for a_ in (p, nr, r0, r1, vw, None if sh_ is None else sh_[:, None]) if a_ is not None]; w_ = np.cumsum([0] + [a_.shape[1] for a_ in cols_])
                V_, T = hex_split(no, np.concatenate(cols_, 1), T)
                if not len(T):
                    continue
                it_ = iter(V_[:, a:b] for a, b in zip(w_[:-1], w_[1:]))
                p, nr, r0 = next(it_), next(it_), next(it_); r1 = next(it_) if r1 is not None else None; vw = next(it_) if vw is not None else None; g = togrid(p)
                sh_ = next(it_)[:, 0] if sh_ is not None else None
            uv = box_uv(r0, s['fl'])
            if fx.startswith('lake'):
                add('wat', water(), p, nr, uv, T); surf['water'] += len(T); raster(Water, g[T])
                continue
            decal = 'decal' in fx or fx.startswith('h_road') and 'decal' in n.lower(); opac = bool(re.search(r'opac|mask|tree|bunting', fx)) and not decal
            road = fx.startswith('h_road') and not re.search(r'sign|barrier|wall|armco', n, re.I)
            ts = slot_tex(no, [x for x in slots if x < 96])
            if not ts and re.search(r'barbwire|cable|tether', n, re.I):      # power lines and tethers name 0x108E, which no archive in the dump holds
                add('vis', flat(), p, nr, box_uv(r0, s['fl']), T); surf['wire'] += len(T)
                continue
            if not ts:
                stats['sec_notex'] += 1; DROPPED[re.sub(r'\d+', '#', n)[:30]] += len(T)
                continue
            sk_ = skirt_passes(no, slots, fx, cs_) if fx.startswith('h_road') and '_blend' in fx and vw is not None else []
            if sk_:     # verges over the ground beside the road, a translucent pass per layer, 2, 3 and 4 cm up, a cm apart as FH1 draws them over ground and road that their own triangles only drape, under road decals (5 cm)
                if near_:
                    raster(Cov if isterr else Road, g[T])
                for key_, sc_, b_, lift_ in sk_:
                    add('vis', key_, p, nr, uv * sc_, T, lift=lift_, uv1=layered_uv1(vw, sh_, b_, np.ones(2)))
                surf['road_skirt'] += len(T); surf['road_skirt_passes'] += len(T) * len(sk_)
                continue
            if isterr:
                if near_:
                    raster(Cov, g[T])
                layers = blend_layers(no, slots, fx, cs_); ids = MT[no][0]
                ts = [x for x in ts if not rgmask(x)] or ts
                mask = None
                if fx.startswith('h_blnd') and r1 is not None and len(slots) > 2 and slots[2] < len(ids):
                    mt = tex.bix(IDX[ids[slots[2]]][0])
                    if mt and mt['fmt'] == 0x52:
                        mask = TX.get(IDX[ids[slots[2]]][0])
                gnd = role_ground(s['name']); vb_ = layered(no, slots, fx, cs_, gnd) if fx.startswith('h_vblnd') and vw is not None else None
                if mask is not None and len(layers) > 1:
                    mname = blend_material(layers[:3], IDX[ids[slots[2]]][0], gnd, ao_map(no, slots, fx))
                    add('col', mname, p, nr, uv * np.array(layers[0][1]), T, uv1=(r1 + 1) / 2); surf[f'terrain_blend:{gnd}'] += len(T)
                elif vb_:
                    add('col', vb_[0], p, nr, uv * vb_[1], T, uv1=layered_uv1(vw, sh_, *vb_[2:])); surf[f'terrain_vblend:{gnd}'] += len(T)
                else:
                    ao_ = ao_map(no, slots, fx) if r1 is not None else None      # on UV1 where FH1 reads it, as the blend masks
                    add('col', material(layers[0][0] if layers else ts[0], gnd, ao=ao_), p, nr, uv * np.array(layers[0][1] if layers else (1, 1)), T,
                        uv1=(r1 + 1) / 2 if ao_ else None); surf[f'terrain:{gnd}'] += len(T)
                continue
            tid = ts[0]; info = TX.get(tid)
            if info is None:
                stats['sec_notex'] += 1
                continue
            if decal:
                if road and near_:         # drains and the like on Decal meshes: still road, so far ground keeps clear of them as before
                    raster(Road, g[T])
                mask = slot_at(no, slots, 2) if 'mask' in fx and r1 is not None else None
                if vw is None:
                    add('vis', material(tid, translucent=True), p, nr, uv, T, lift=.05)
                elif mask:
                    a_ = round(float(vw[np.unique(T), 0].mean()) * 20) / 20         # shared by sections with about the same alpha
                    add('vis', decal_material(tid, mask, a_), p, nr, uv, T, lift=.05, uv1=(r1 + 1) / 2)
                else:
                    add('vis', decal_material(tid, None, 0), p, nr, uv, T, lift=.05, uv1=ramp_uv(vw[:, [0, 0]]))
                surf['decal'] += len(T)
            elif road:
                rgb = info['rgb']; brown = rgb[0] - rgb[2] > 14
                gnd = 'DIRT' if brown else 'ASPHALT'
                if near_:
                    raster(Road, g[T])
                rm_ = layered(no, slots, fx, cs_, gnd) if vw is not None else None
                if rm_:
                    add('col', rm_[0], p, nr, uv * rm_[1], T, uv1=layered_uv1(vw, sh_, *rm_[2:])); surf['road_layers'] += len(T)
                else:
                    add('col', material(tid, gnd), p, nr, uv * np.array(layer_scale(mat_const(cs_, 0)[:2], (1, 1))), T)
                surf[f'road:{gnd}'] += len(T)
            else:       # objects: AO map and lightmap on UV1 (object_maps). A lit lightmap puts the section in a 'lit' cell, lit at night
                u1 = uv1_box(m, s['fl']) if 'vblnd' not in fx else None
                ao, lm, win = object_maps(no, slots, fx, WORLDLM.get(no)) if u1 is not None else (None, None, None)
                lm = None if lm == 'draw' else lm
                emis = bake_emissive(lm, info['rgb'], win, uv, u1, T) if lm and win else None; sp_ = spec_map(no, slots, fx)
                if fx.startswith('add_'):
                    add('vis', additive(tid, cs_), p, nr, uv, T); surf['additive'] += len(T)
                elif re.search(r'cable|tether', n, re.I):
                    add('vis', material(tid, alpha=opac, ao=ao, spec=sp_), p, nr, uv, T, uv1=u1); surf['cable'] += len(T)
                elif re.search(r'armco|barrier|fence|rail', n, re.I):
                    lv_ = LV; LV = tuple(FARCUT if x == 2 else x for x in LV)      # barriers stop at 800 m, and their copies add up
                    add('bar', material(tid, 'METAL', alpha=opac, ao=ao, spec=sp_), p, nr, uv, T, uv1=u1); surf['barrier'] += len(T); LV = lv_
                elif re.search(r'rock|wall|cliff', n, re.I) or 'vblnd' in fx:
                    add('col', material(tid, 'ROCK', alpha=opac, ao=ao, spec=sp_), p, nr, uv, T, uv1=u1); surf['rock'] += len(T)
                    if near_:
                        raster(Rock, g[T])
                else:
                    lit_ = bool(lm or win)
                    add('lit' if lit_ else 'pro', material(tid, alpha=opac, ao=ao, lm=lm, win=win, emis=emis, spec=sp_), p, nr, uv, T, uv1=u1)
                    surf['prop_lit' if lit_ else 'prop'] += len(T); surf['windows'] += len(T) if win else 0
                surf['ao'] += len(T) if ao else 0
LV = (0, 1, 2); FIX = None; NM = {}
print('dropped sections by mesh', DROPPED.most_common(24))
lap('world meshes')
shown = np.isfinite(Cov)
road = np.isfinite(Road)
np.savez_compressed(Path('../logs') / f'{LVL}-road.npz', road=road, SQ=SQ, HALF=HALF)     # FH1's road-shader triangles, for checks. road[j, i] is BeamNG (i, j) x SQ - HALF
if road.any():
    RF = pyramid_fill(np.where(road, Road, np.nan)); dist = ni.distance_transform_edt(~road) * SQ
else:
    RF = np.zeros((N, N)); dist = np.full((N, N), 1e9)
limit = RF - .2 + np.maximum(dist - SQ, 0)
LIP = 16.0      # far ground runs this far under nearer ground, sloping down, so a height mismatch at the seam can't open a crack
BARY = np.array([[a_, b_, 8 - a_ - b_] for a_ in range(9) for b_ in range(9 - a_)], 'f8') / 8


def clip_far(A, T, dout, din):
    """Split far terrain triangles and keep the parts outside the mask and its lip, so a coarse triangle clipping a road only loses that corner.
    A: vertices (position, normal, uv, uv1). dout and din: metres to the mask from outside and from inside it."""
    S = A[T]; keep = []
    while len(S):
        c_ = togrid(S[:, :, :3].reshape(-1, 3)).reshape(-1, 3, 3)[:, :, :2]
        L = np.sqrt(((c_ - c_[:, [1, 2, 0]]) ** 2).sum(2)).max(1) * SQ
        Q_ = np.einsum('ij,kjl->kil', BARY, c_)
        ci = np.clip(Q_[..., 0].astype(int), 0, N - 1); cj = np.clip(Q_[..., 1].astype(int), 0, N - 1)
        gap = L / 8                                # samples are this far apart, so a distance above it proves the whole triangle
        gone = din[cj, ci].min(1) > gap + LIP
        near = (dist[cj, ci].min(1) < 40 + gap) & (L > 32)        # small enough to follow the road height
        clear = (dout[cj, ci].min(1) > gap) & ~near & ~gone
        tiny = (L <= LIP) & ~clear & ~gone
        keep.append(S[clear]); keep.append(S[tiny & (din[cj, ci].mean(1) < LIP)])
        s_ = S[~(clear | gone | tiny)]
        if not len(s_):
            break
        a_, b_, c2 = s_[:, 0], s_[:, 1], s_[:, 2]; ab = (a_ + b_) / 2; bc = (b_ + c2) / 2; ca = (c2 + a_) / 2
        S = np.concatenate([np.stack(v, 1) for v in ((a_, ab, ca), (ab, b_, bc), (ca, bc, c2), (ab, bc, ca))])
    V = np.concatenate(keep).reshape(-1, A.shape[1]) if keep else A[:0]
    c_ = togrid(V[:, :3]); ci = np.clip(c_[:, 0].astype(int), 0, N - 1); cj = np.clip(c_[:, 1].astype(int), 0, N - 1)
    k_ = dist[cj, ci] <= 40
    V[k_, 1] = np.minimum(V[k_, 1], limit[cj, ci][k_])
    if coarse_:
        V[:, 1] = np.minimum(V[:, 1], lump[cj, ci])
    k_ = dsink[cj, ci] > 0
    V[k_, 1] = np.minimum(V[k_, 1], top[cj, ci][k_])      # a coarse mountain side can stand well above the fine ground's edge
    V[:, 1] -= .5 * np.minimum(dsink[cj, ci], LIP)      # only under ground. At a road edge there's nothing above it, so it would show as a ditch
    return V, np.arange(len(V)).reshape(-1, 3)


def far_tiers():
    """A far triangle's tier: fine before coarse, then how close to it FH1 ever draws its mesh. Each zone hex lists what FH1 draws from it,
    so the distance to the nearest hex listing any LOD of the mesh's group is the closest it's ever seen from. The Uber shell isn't drawn within
    1 km of itself, and a fine tile is drawn from every hex within 300 m. All fine ground goes before any coarse, each by that distance: by
    distance first, MIDDIST_01_NOLOD (listed by 120 hexes, 2,300 tris per km2) took Kettle Hills from PATCH_13 (10 hexes, 44,000), which FH1
    swaps in up close. A shell can't cover finer ground either way.
    -> per FARQ entry, per section, a tier for each triangle"""
    HX, HR = hexzones.hexes(); zn = sorted(HX); ZC = np.array([HX[z] for z in zn]); seen = defaultdict(list)
    for k_, z in enumerate(zn):
        for d in hexzones.drawn(z):
            if d < len(DRAW):
                seen[int(DRAW['t'][d])].append(k_)
    trees = {}; out = []; unseen = 0
    for coarse, no, M, m, p, *_, gk in FARQ:
        if gk not in trees:
            h_ = sorted({z for f in GFILES[gk] for z in seen.get(f, ())}); trees[gk] = cKDTree(ZC[h_]) if h_ else None
        t_ = trees[gk]; unseen += t_ is None; secs = []
        for s in m['secs']:
            T = rmb.tris(s['ib'], m['nv'], s['ints'][1])
            c_ = p[T].mean(1) if len(T) else np.zeros((0, 3))
            d_ = np.maximum(t_.query(np.c_[c_[:, 0], -c_[:, 2]])[0] - HR, 0) if t_ is not None else np.full(len(T), 1e9)
            secs.append(np.searchsorted(ZBANDS, d_) + coarse * (len(ZBANDS) + 1))
        out.append(secs)
    print('far meshes no zone draws', unseen, 'of', len(FARQ))
    return out


def structures():
    """Large placed structures stand in holes in the terrain meshes (structure_tris). They're placed after the far ground, so their
    footprints go in Rock here, which keeps far ground off them"""
    for p, T in STRUCTS:
        raster(Rock, togrid(p)[T])
    print('structure footprints', len(STRUCTS))


BQ = 16.0; BSINK = 4.0      # backdrop coverage grid, and how far coarser ground sinks under finer, in metres


def tkeys(k):
    """a key per triangle from its corners' vkeys, in any order"""
    k = np.sort(k, 1)
    return k[:, 0] * 1000003 ^ k[:, 1] * 999983 ^ k[:, 2]


def vkeys(p):
    """a key per vertex, to 5 cm: FH1's copies of a mesh, such as NOLOD pieces of the Uber shell, repeat its vertices exactly"""
    q = np.round(np.asarray(p, 'f8') * 20).astype(np.int64)
    return q[:, 0] * 73856093 ^ q[:, 1] * 19349663 ^ q[:, 2] * 83492791


def backdrop():
    """FH1's far mountains beyond the square, up to 18 km out and 2,700 m up: one shape with no collision, shown at every distance.
    Near-path ground goes in first, then far meshes one at a time, finest first: Mountains_Area5_NOLOD and the Uber shell overlap there,
    and at one priority they z-fought. A coarser triangle is cut where finer ground starts and the covered part dropped, splitting until its
    pieces are two grid cells across. Dropping only wholly covered triangles left 300 m Uber triangles cutting through the finer Rockies.
    Corners still over finer ground sink under it. Water doesn't count as cover, so lake beds stay"""
    if not BACK:
        return
    pts = np.concatenate([b_[T_].reshape(-1, 3) for _, _, _, b_, _, _, T_, _ in BACK])
    lo = pts[:, :2].min(0); nx, ny = (np.ceil((pts[:, :2].max(0) - lo) / BQ)).astype(int) + 1
    H = np.full((ny, nx), np.nan); kept = collections.Counter(); lost = collections.Counter()
    for pri in sorted({e[0] for e in BACK}):
        low = ni.minimum_filter(np.nan_to_num(H, nan=np.inf), 3); done = []
        hard = ni.binary_erosion(np.isfinite(H), np.ones((3, 3)))      # a cell away from the edge, so neighbouring tiles of one fineness can't eat each other's border
        for e in BACK:
            if e[0] != pri:
                continue
            _, cat, mat, b_, nb, uv, T_, u1 = e
            g = np.c_[(b_[:, :2] - lo) / BQ, b_[:, 2]]; i_ = np.clip(g[:, 0].astype(int), 0, nx - 1); j_ = np.clip(g[:, 1].astype(int), 0, ny - 1)
            gc = g[T_].mean(1); ic = np.clip(gc[:, 0].astype(int), 0, nx - 1); jc = np.clip(gc[:, 1].astype(int), 0, ny - 1)
            if pri[0] >= 0 and cat != 'wat' and hard.any():
                A_ = np.concatenate([b_, nb, uv, u1], 1); S_ = A_[T_]; out_ = []
                while len(S_):
                    q_ = np.einsum('ij,kjl->kil', BARY, S_[:, :, :2]); c_ = hard[np.clip(((q_[..., 1] - lo[1]) / BQ).astype(int), 0, ny - 1), np.clip(((q_[..., 0] - lo[0]) / BQ).astype(int), 0, nx - 1)]
                    all_ = c_.all(1); none_ = ~c_.any(1); L_ = np.linalg.norm(S_[:, :, :2] - S_[:, [1, 2, 0], :2], axis=2).max(1)
                    small_ = (L_ <= 2 * BQ) & ~all_ & ~none_
                    lost[pri[0]] += int(all_.sum()); out_ += [S_[none_], S_[small_]]
                    S_ = S_[~(all_ | none_ | small_)]
                    a_, b2, c2 = S_[:, 0], S_[:, 1], S_[:, 2]; ab, bc, ca = (a_ + b2) / 2, (b2 + c2) / 2, (c2 + a_) / 2
                    S_ = np.concatenate([np.stack(v, 1) for v in ((a_, ab, ca), (ab, b2, bc), (ca, bc, c2), (ab, bc, ca))]) if len(S_) else S_
                V_ = np.concatenate(out_).reshape(-1, A_.shape[1]) if out_ else A_[:0]
                b_, nb, uv, u1 = V_[:, :3], V_[:, 3:6], V_[:, 6:8], V_[:, 8:]; T_ = np.arange(len(V_)).reshape(-1, 3)
                g = np.c_[(b_[:, :2] - lo) / BQ, b_[:, 2]]; i_ = np.clip(g[:, 0].astype(int), 0, nx - 1); j_ = np.clip(g[:, 1].astype(int), 0, ny - 1)
                cov = hard[j_, i_]
            else:
                cov = hard[j_, i_]; T_ = T_[~(cov[T_].all(1) & hard[jc, ic])]; lost[pri[0]] += len(e[6]) - len(T_)
            kept[pri[0]] += len(T_)
            if not len(T_):
                continue
            b_ = b_.copy(); b_[cov, 2] = np.minimum(b_[cov, 2], low[j_, i_][cov] - BSINK); g[:, 2] = b_[:, 2]
            idx, inv = np.unique(T_, return_inverse=True)
            cells[(0, 'bak')][(mat, (0, 1, 2))].append((b_[idx], nb[idx], uv[idx], inv.reshape(-1, 3).astype(np.int64), u1[idx]))
            if cat != 'wat':
                done.append(g[T_])
        for t_ in done:         # after the whole priority, so meshes at the same one can't drop each other
            raster(H, t_)
    print('backdrop tris kept', dict(kept), 'covered by finer', dict(lost))


structures()
lap('structure footprints')
TIERS = far_tiers(); NT = 2 * len(ZBANDS) + 2
FINE = []       # each far mesh's median longest edge. Beyond the square the backdrop takes them finest first, whatever their tier
for e_ in FARQ:
    T_ = np.concatenate([rmb.tris(s['ib'], e_[3]['nv'], s['ints'][1]) for s in e_[3]['secs']] or [np.zeros((0, 3), int)])
    FINE.append(float(np.median(np.linalg.norm(e_[4][T_] - e_[4][T_][:, [1, 2, 0]], axis=2).max(1))) if len(T_) else 0.0)
# within a tier, the Uber background goes first and finer LODs before coarser, so a copy of ground already drawn is the one dropped
ORDER = sorted(range(len(FARQ)), key=lambda i: ('uber' not in NAMEOF.get(FARQ[i][1], '').lower(), LEVEL.get(FARQ[i][1], lod(NAMEOF.get(FARQ[i][1], '')))))
lap('far tier lookup')
for tier_ in range(NT):
    if not any((t__ == tier_).any() for e_ in TIERS for t__ in e_):
        continue
    SEEN = np.zeros(0, np.int64)      # triangle keys drawn in this tier
    new_ = np.full((N, N), np.nan, 'f8'); hide = shown | road | np.isfinite(Rock) | np.isfinite(Water)      # road shaders also draw town grass and paving. Far ground would cover a river
    if os.environ.get('FH1_DUMPMASKS') and tier_ == 0:      # what keeps far ground off, for checking where it went missing
        np.savez_compressed(Path('../logs') / f'{LVL}-masks.npz', shown=shown, road=road, rock=np.isfinite(Rock), water=np.isfinite(Water), X0=X0, Z0=Z0, SQ=SQ, N=N)
    if tier_ > len(ZBANDS):       # the shell has festival tents baked into it. In a small hole in finer ground, it's held to that ground's height
        lab_, n_ = ni.label(~shown); small = np.bincount(lab_.ravel()) * SQ * SQ < 150000; small[0] = False
        lump = np.where(small[lab_], pyramid_fill(Cov) + 1, np.inf)
    dout = ni.distance_transform_edt(~hide) * SQ; din = ni.distance_transform_edt(hide) * SQ; dsink = ni.distance_transform_edt(shown | np.isfinite(Rock)) * SQ
    # FH1's own close-range ground ignores water: a river's surface mesh runs on under its banks, and this dropped the banks along
    # Clear Springs' river (Reservoir_Area5_PATCH14). Height rules for water and rock were tried and reverted: by rock top they
    # dropped the ground along every wall in Carson, and by water height they dropped ground at the waterline. FH1_HEXGROUND replaces both
    din0 = ni.distance_transform_edt(shown | road | np.isfinite(Rock)) * SQ if tier_ == 0 else None
    top = ni.minimum_filter(np.nan_to_num(np.fmax(Cov, Rock), nan=np.inf), 3)      # raster keeps a cell's highest point, so take the lowest nearby
    for e_ in ORDER:
        (coarse_, no, M, m, p, nr, r0, r1, g, _), tiers_ = FARQ[e_], TIERS[e_]; BPRI = (0, FINE[e_])
        ids = MT[no][0]; mine_ = []
        for s, ti_ in zip(m['secs'], tiers_):
            T = rmb.tris(s['ib'], m['nv'], s['ints'][1])
            if not len(T) or s['ints'][2] >= len(M):
                continue
            T = T[ti_ == tier_]; vk_ = vkeys(p)
            tk_ = tkeys(vk_[T])
            if len(SEEN) and not HEXGROUND:       # the same triangle already drawn in this tier: a copy, such as a NOLOD piece of the Uber shell. SEEN stays sorted.
                # Tested corner by corner, it also dropped every far triangle filling a small hole in nearer ground, whose corners its neighbours share
                c_ = SEEN[np.minimum(np.searchsorted(SEEN, tk_), len(SEEN) - 1)] == tk_; stats['far_copies'] += int(c_.sum()); T = T[~c_]; tk_ = tk_[~c_]
            if not len(T):
                continue
            if not HEXGROUND:
                mine_.append(tk_)
            uv = box_uv(r0, s['fl'])
            si, slots, cs_ = M[s['ints'][2]]; fx = SHADERS[MT[no][1][si]] if si < len(MT[no][1]) else ''
            vb_ = layered(no, slots, fx, cs_, role_ground(s['name'])) if fx.startswith('h_vblnd') and m['sd'] >= 28 else None
            if vb_:     # vertex weights and shade through clip_far as UV set 1, so split triangles interpolate them
                u1 = layered_uv1(np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])[:, -4:].astype('f8') / 255, vertex_shade(no, slots, fx, m, s), *vb_[2:])
            else:
                u1 = (r1 + 1) / 2 if r1 is not None else uv
            A = np.c_[p, nr, uv, u1]; o_ = A[:0]
            if BACKDROP:        # clip_far would hold ground beyond the square to the edge cells' heights. add() sends it to the backdrop
                k_ = (np.abs(g2b(p[T].mean(1))[:, :2]) < HALF - 4).all(1); o_ = A[T[~k_]].reshape(-1, A.shape[1]); T = T[k_]
            if HEXGROUND:       # where FH1's hexes draw it, as it is: no tiers, no clipping, no water or rock rules
                V, T = hex_split(no, A, T); V = V[T].reshape(-1, A.shape[1])      # as triangle rows: a mesh with no hex group comes back indexed
                # copies are told after the selection, from what it kept: told before, Mountains_Area5_NOLOD_01's 129 triangles shared with
                # UberLOD24_LOD00 went as copies of Uber pieces the selection then dropped, leaving holes in the far mountains
                tk_ = tkeys(vkeys(V[:, :3]).reshape(-1, 3))
                if len(SEEN):
                    c_ = SEEN[np.minimum(np.searchsorted(SEEN, tk_), len(SEEN) - 1)] == tk_; stats['far_copies'] += int(c_.sum())
                    V = V.reshape(-1, 3, A.shape[1])[~c_].reshape(-1, A.shape[1]); tk_ = tk_[~c_]
                mine_.append(tk_)
            elif tier_ == 0:      # FH1 draws this from its own hexes: real close-range ground beside the near tiles, only named PATCH or MIDDIST.
                # clip_far's road cap and its hold to the lowest nearby ground tore it open at their seams: a slit down Redstone's canyon
                # walls, the cliff beside Clear Springs' road. Kept as it is, unless it lies wholly over near ground
                c_ = togrid(np.concatenate([A[T][..., :3], A[T][..., :3].mean(1, keepdims=True)], 1).reshape(-1, 3)).reshape(-1, 4, 3)
                T = T[~(din0[np.clip(c_[..., 1].astype(int), 0, N - 1), np.clip(c_[..., 0].astype(int), 0, N - 1)].min(1) > LIP)]
                V = A[T].reshape(-1, A.shape[1])
            else:
                V, T = clip_far(A, T, dout, din)
            V = np.concatenate([V, o_]); T = np.arange(len(V)).reshape(-1, 3)
            if not len(T):
                continue
            p_, n_, uv, u1 = V[:, :3], V[:, 3:6], V[:, 6:8], V[:, 8:]; g_ = togrid(p_)
            si, slots, cs_ = M[s['ints'][2]]; fx = SHADERS[MT[no][1][si]] if si < len(MT[no][1]) else ''; NM = section_normals(no, slots)
            ts = slot_tex(no, [x for x in slots if x < 96])
            if not ts:
                continue
            gnd = role_ground(s['name']); layers = blend_layers(no, slots, fx, cs_); ts = [x for x in ts if not rgmask(x)]
            if not layers and not ts:
                continue
            mask = None
            if fx.startswith('h_blnd') and r1 is not None and len(slots) > 2 and slots[2] < len(ids):
                mt_ = tex.bix(IDX[ids[slots[2]]][0])
                if mt_ and mt_['fmt'] == 0x52:
                    mask = TX.get(IDX[ids[slots[2]]][0])
            if mask is not None and len(layers) > 1:
                add('col', blend_material(layers[:3], IDX[ids[slots[2]]][0], gnd, ao_map(no, slots, fx)), p_, n_, uv * np.array(layers[0][1]), T, uv1=u1)
            elif vb_:
                add('col', vb_[0], p_, n_, uv * vb_[1], T, uv1=u1); surf[f'far_vblend:{gnd}'] += len(T)
            else:
                ao_ = ao_map(no, slots, fx) if r1 is not None and not vb_ else None      # u1 is the blend mask's UV1 here
                add('col', material(layers[0][0] if layers else ts[0], gnd, ao=ao_), p_, n_, uv * np.array(layers[0][1] if layers else (1, 1)), T, uv1=u1 if ao_ else None)
            surf[f'far{tier_}'] += len(T)
            raster(new_, g_[T]); raster(Cov, g_[T])
        if mine_:       # after the whole mesh, as its own sections share vertices along their edges
            SEEN = np.union1d(SEEN, np.concatenate(mine_))
    shown = np.isfinite(Cov)
    print('far tier', tier_, 'shown now', float(shown.mean()))
lap('far tiers')
backdrop()
lap('backdrop')
NM = {}
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


def model_table(w, M, total):
    """model records (flag, k, a, n, lods), in model order: model k owns n packed instances and a full-transform ones.
    Returns ([a], [n]), or None if they don't account for the packed block. Empty tail models may have no record"""
    a_ = []; n_ = []
    for i in range(1, len(w) - 4):
        if len(a_) < M and w[i] <= 1 and w[i - 1] in (0, 1, 4) and w[i + 1] == len(a_) and w[i + 2] < 100000 and w[i + 3] <= total and 1 <= w[i + 4] <= 64:
            a_.append(w[i + 2]); n_.append(w[i + 3])
    a_ += [0] * (M - len(a_)); n_ += [0] * (M - len(n_))
    return (a_, n_) if sum(n_) == total else None


def placed_blocks(b, a_, lo, hi):
    """The full-transform instances of a packed file: 96 byte rows per model, in model order, before the packed block.
    Word 15 of a row is 1 and words 3, 7 and 11 are 0. What separates the blocks varies in length, so rows are found by that.
    Returns [(k, rows)], or None if the runs can't supply the records"""
    w = np.frombuffer(b, '>u4', (hi - lo) // 4, lo)
    i_ = np.where((w[15:] == 0x3f800000) & (w[3:-12] == 0) & (w[7:-8] == 0) & (w[11:-4] == 0))[0]
    runs = [list(r_) for r_ in np.split(i_, np.where(np.diff(i_) != 24)[0] + 1)] if len(i_) else []
    out = []; ri = 0
    for k_, n_ in enumerate(a_):         # runs in model order. A longer run is neighbouring blocks that touch; a run that fits nothing is a stray match
        if not n_:
            continue
        while ri < len(runs) and len(runs[ri]) < n_:
            ri += 1
        if ri == len(runs):
            return None
        out.append((k_, np.stack([np.frombuffer(b, '>f4', 24, lo + int(q_) * 4) for q_ in runs[ri][:n_]]).astype('f8')))
        runs[ri] = runs[ri][n_:]
        if not runs[ri]:
            ri += 1
    return out


EVENT = re.compile(r'festival|multiplayer|street|showcase|activation|crowd|_track_|testfest|_fest_|_pr_barriers|ghostmp', re.I)
LAYERS_SEEN = collections.Counter(); LOSTSET = collections.Counter(); PLACED = []


def model_ids(b, h_, M):
    """-> ([mesh number per model, -1 for a model named by a .max path], offset after the id and path lists), or None.
    Models carry up to three LOD ids, as words 1, 3 and 5 of their header entry say. An entry whose word 8 isn't 0 has no
    draw id: the word is the byte length of its path, and the paths follow the ids"""
    E = [struct.unpack_from('>6I', b, h_ + 0x40 * i + 32) for i in range(M)]; q_ = h_ + 0x40 * M; meshes = []
    for e_ in E:
        if e_[0]:
            meshes.append(-1)
            continue
        v_ = struct.unpack_from('>I', b, q_)[0]
        if v_ >= len(DRAW):
            return None
        meshes.append(int(DRAW['t'][v_])); q_ += 4 * max(sum(e_[f_] for f_ in (1, 3, 5) if e_[f_] < 8), 1)
    for e_ in E:
        if e_[0]:
            if b[q_ + e_[0] - 1] != 0:
                return None
            q_ += e_[0]
    return meshes, (q_ + 3) & ~3


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
        if not 1 <= M <= 64:
            return None
        # the header's start moves in some files. Model entries are 64 bytes and the first ids follow the last entry's 'tga' tag
        t_ = b.find(b'tga\0', 0x98, 0xd0 + 0x40 * M + 0x40)
        h_ = t_ + 4 - 0x40 * M if t_ >= 0 else 0xd0
        ids_ = model_ids(b, h_, M)
        if ids_ is None:
            stats['proc_bad'] += 1
            print('proc unreadable', n, M)
            return None
        meshes, q_ = ids_
        w = struct.unpack_from('>%dI' % min((len(b) - q_) // 4, 200), b, q_)
        G = []; TR = []
        for i in range(1, len(w) - 4):            # (k, 0, count, 0, 1) or the older (1, k, count, 0, trailers)
            if len(G) == M:
                break
            if w[i - 1] == 0 and w[i] == len(G) and 0 < w[i + 1] < 100000 and w[i + 2] == 0 and w[i + 3] == 1:
                G.append(w[i + 1]); TR.append(1)
            elif w[i] == 1 and w[i + 1] == len(G) and 0 < w[i + 2] + w[i + 3] < 100000 and 1 <= w[i + 4] <= 8:
                G.append(w[i + 2] + w[i + 3]); TR.append(w[i + 4])
        tot = sum(G); A = None
        start = len(b) - 96 * tot - 32 * sum(TR)        # each model's rows are followed by one 32 byte trailer per LOD distance set
        if len(G) == M and start >= q_:
            parts_ = []; p_ = start
            for c_n, t_n in zip(G, TR):
                parts_.append(np.frombuffer(b, '>f4', c_n * 24, p_).reshape(-1, 24).astype('f8')); p_ += 96 * c_n + 32 * t_n
            c_ = np.concatenate(parts_)
            if np.abs(c_[:, 15] - 1).max() < .01 and (np.linalg.norm(c_[:, :3], axis=1) > .01).all():
                A = c_
        if A is None:
            stats['proc_bad'] += 1
            print('proc unreadable', n, M, G)
            return None
        stats['proc_ok'] += 1
        return 'proc', A, meshes, G
    if M < 1 or M > 512 or size % 40 or size >= len(b):        # size 0: only full-transform instances
        stats['pgeo_badhdr'] += 1
        return None
    h_ = 0xd0 if b[0x98:0xa3] == b'models_proc' else 0x80        # named sets carry a longer header
    o = h_ + 0x44 * M; start = len(b) - size
    w = struct.unpack_from('>%dI' % ((start - o) // 4), b, o)
    table = model_table(w, M, size // 40)
    if table is None:
        stats['pgeo_groupmismatch'] += 1; LOSTSET[n] = size // 40
        return None
    W = [struct.unpack_from('>f', b, h_ + 0x40 * i + 16)[0] - struct.unpack_from('>f', b, h_ + 0x40 * i)[0] for i in range(M)]
    A = np.frombuffer(b, '>u4', size // 4, start).reshape(-1, 10).astype(np.int64)
    ids_ = model_ids(b, h_, M)
    if ids_ is None:
        stats['pgeo_unreadable'] += 1
        return None
    meshes, q_ = ids_
    blocks = placed_blocks(b, table[0], q_, start)
    if blocks is None:
        stats['pgeo_placed_mismatch'] += 1; LOSTSET[n] = size // 40
    elif blocks:
        c_ = np.concatenate([c_ for _, c_ in blocks]); c_[:, 16] = 0      # these rows keep a tint there, so scale comes from the axes
        PLACED.append(('proc', c_, [meshes[k_] for k_, _ in blocks], [len(c_) for _, c_ in blocks]))
        stats['pgeo_placed_instances'] += sum(len(c_) for _, c_ in blocks)
    return bb, A, meshes, list(zip(table[1], W))


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
files += PLACED
print('layers', LAYERS_SEEN.most_common(30))
print('pgeo files', len(files), {k: v for k, v in stats.items() if k.startswith(('pgeo', 'proc'))}, 'instances in them', sum(len(f[1]) for f in files))
lap('read pgeo')

fdir = level / 'forest'; fdir.mkdir(parents=True, exist_ok=True); adir = level / 'art' / 'forest' / 'models'; adir.mkdir(parents=True, exist_ok=True)
pal = donor.analyze(inst, 'utah')
tmpl = dict(next(iter(pal['vegetation'].values()))['def']); tmpl.pop('persistentId', None)
def game_rot(Rg):
    return S_ @ Rg if np.linalg.det(Rg) < 0 else S_ @ Rg @ S_


defs = {}; rows = defaultdict(list); S_ = np.diag([1., 1., -1.]); B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]])


NAME_ = {}
for c in F:
    NAME_.setdefault(int(c['f'].split('.')[1]), c['name'])


def lod_sibling(no):
    """the mesh file with the next LOD of this model"""
    m = re.search(r'_LOD(\d+)', NAME_.get(no, ''))
    if not m:
        return None
    want = NAME_[no].replace(m.group(0), '_LOD' + str(int(m.group(1)) + 1).zfill(len(m.group(1))), 1)
    return next((q for q in range(no + 1, no + 4) if NAME_.get(q) == want), None)


def model_groups(no):
    fs = f'coloradoout.{no:05d}.rmb.bin'
    if not os.path.exists('../x/' + fs):
        return [], None, None, ''
    global NM
    b = open('../x/' + fs, 'rb').read(); P = rmb.parse(b); M = materials(b, P); groups = []; lo = np.full(3, np.inf); hi = -lo; label = ''
    ids = MT[no][0]; tree = any(SHADERS[q].startswith('tree') for q in MT[no][1] if q < len(SHADERS))
    flag = any(SHADERS[q].startswith('anim_flag_') for q in MT[no][1] if q < len(SHADERS))
    for m in P['meshes']:
        label = label or m['name']
        a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
        p = np.frombuffer(a[:, :12].reshape(-1, 3, 4)[:, :, ::-1].tobytes(), '<f4').reshape(-1, 3).astype('f8')
        card = m['sd'] < 20
        cloth = flag and m['sd'] == 32         # the waving cloth has its UVs at byte 12, like tree cards, and no normal
        if card or cloth:
            r0 = np.frombuffer(a[:, 12:16].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535
            nr = np.zeros_like(p)
        else:
            _, nr, _ = rmb.verts(m); r0, _ = uv_sets(m)
        pb = np.stack([p[:, 0], -p[:, 2], p[:, 1]], 1); nb = n2b(nr)
        for s in m['secs']:
            T = rmb.tris(s['ib'], m['nv'], s['ints'][1])
            if not len(T):
                continue
            mi = s['ints'][2]; ts = slot_tex(no, [x for x in M[mi][1] if x < 96]) if mi < len(M) else []; NM = section_normals(no, M[mi][1]) if mi < len(M) else {}
            if not ts:
                q = IDX[ids[min(mi, len(ids) - 1)]][0] if ids else None
                ts = [q] if q is not None and tex.bix(q) and tex.bix(q)['fmt'] != 0x71 else []
            if not ts:
                continue
            if tree and len(ids) > 1:        # the slot list can be bark first, which puts opaque bark on the leaf cards
                cut_ = sorted((TX.get(IDX[i][0])['cut'], IDX[i][0]) for i in ids if TX.get(IDX[i][0]))
                c0_ = TX.get(ts[0])['cut'] if TX.get(ts[0]) else 0
                if card and c0_ < .05 and cut_:
                    ts = [cut_[-1][1]]
                elif not card and c0_ >= .05 and cut_:
                    ts = [cut_[0][1]]
            gs_ = next((v_ for k_, v_ in GLOW.items() if NAME_.get(no, '').startswith(k_)), None)
            sl_ = M[mi][1] if mi < len(M) else []
            glow = IDX[ids[sl_[gs_]]][0] if gs_ is not None and gs_ < len(sl_) and sl_[gs_] < len(ids) else None
            fx_ = SHADERS[MT[no][1][M[mi][0]]] if mi < len(M) and M[mi][0] < len(MT[no][1]) else ''
            u1 = None if card or cloth else uv1_box(m, s['fl'])
            ao, lm, win = object_maps(no, sl_, fx_) if u1 is not None else (None, None, None)
            lm_ = None if lm == 'draw' else lm; info_ = TX.get(ts[0])
            emis = bake_emissive(lm_, info_['rgb'], win, box_uv(r0, s['fl']), u1, T) if lm_ and win and info_ else None
            sp_ = spec_map(no, sl_, fx_) if u1 is not None else None
            mt = material(ts[0], alpha=True, two=True, glow=glow, ao=ao, lm=lm_, win=win, emis=emis, spec=sp_)
            if mt is None:
                continue
            if glow is not None or lm_ or win:      # stays a TSStatic, which core_environment lights at night
                GLOWING.add(no)
            if card or cloth:
                uv = box_uv(r0, s['fl'])
                e1 = pb[T[:, 1]] - pb[T[:, 0]]; e2 = pb[T[:, 2]] - pb[T[:, 0]]; fn_ = np.cross(e1, e2)
                fn_ /= np.maximum(np.linalg.norm(fn_, axis=1, keepdims=True), 1e-9); nb = nb.copy(); nb[T.ravel()] = fn_.repeat(3, 0)
            else:
                uv = box_uv(r0, s['fl'])
            idx, inv = np.unique(T, return_inverse=True)
            # the last entry lets a placement remake the material with its own lightmap (see the free-roam objects)
            groups.append((mt, pb[idx], nb[idx], uv[idx], inv.astype(np.int64).ravel(), (uv if u1 is None else u1)[idx],
                           dict(tid=ts[0], glow=glow, ao=ao, win=win, sp=sp_, nm=NM.get(ts[0]), draw=lm == 'draw')))
            lo = np.minimum(lo, pb.min(0)); hi = np.maximum(hi, pb.max(0))
    return groups, lo, hi, label


def model_item(no):
    """export one mesh file as a forest shape; returns (item name, height, width) or None"""
    if no in defs:
        return defs[no]
    defs[no] = None
    if 'BarrierConcrete' in NAME_.get(no, ''):       # placed all over the event data, across real roads, but never shown in the game
        return None
    groups, lo, hi, label = model_groups(no)
    if not groups:
        return None
    sib = lod_sibling(no); low = model_groups(sib)[0] if sib is not None else []
    stats['model_with_lod1' if low else 'model_single_lod'] += 1
    item = re.sub(r'[^A-Za-z0-9_]', '_', label) + f'_{no}'
    lv0 = (0,) if low else (0, 1)
    shape = [((g[0], lv0),) + tuple(g[1:]) for g in groups] + [((g[0], (1,)),) + tuple(g[1:]) for g in low]
    write_lod(adir / f'{item}.dae', shape, np.zeros(3), (120, 40 if IMPOSTERS else 1), None, ['bb_autobillboard25'] if IMPOSTERS else [])
    if IMPOSTERS:       # the imposter settings stock trees carry beside the mesh (544 of 607), as the shape editor writes them: with the dae node alone
        # the sprites drew their transparent background black
        (adir / f'{item}.dae.asset.json').write_text(json.dumps({'imposters': [{'detailLevel': 1, 'dimension': 128, 'equatorSteps': 16, 'includePoles': False,
                                                                               'polarAngle': 0, 'polarSteps': 0, 'size': 25}]}, indent=2))
        for f_ in (f'{item}.dae.imposter.dds', f'{item}.dae.imposter_normals.dds'):      # captured imposters with mips that keep thin trees (impfix.py)
            if os.path.exists(f'../impcache/{f_}'):
                shutil.copyfile(f'../impcache/{f_}', adir / f_)
    d = dict(tmpl); d.update({'name': item, 'internalName': item, 'shapeFile': f'{rel}/art/forest/models/{item}.dae', 'branchAmp': 0, 'detailAmp': 0, 'detailFreq': 0, 'windScale': 0})
    d.pop('persistentId', None)
    tri = (sum(len(g[4]) // 3 for g in groups), sum(len(g[4]) // 3 for g in (low or groups)))
    defs[no] = dict(item=item, h=float(hi[2] - lo[2]), w=float(hi[0] - lo[0]), d=d, groups=groups, low=low, tri=tri)
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
lap('placed models')
# --- CollObjs: smashable scenery
RT0 = gamepath.RIBBON
col = open(RT0 + 'CollObjs.xml', errors='replace').read()
pc = (r'PhysicsType="([^"]+?)\.\d+\.rmb"[^>]*>\s*<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<Orientation>\s*'
      r'<XAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<YAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>\s*<ZAxis x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"/>')
# FH1 breaks these on contact. A free-roam record exactly on one (5 cm) is smashable, so it gets no collision and the car drives through
SMASH = cKDTree(np.array([[float(x_), -float(z_)] for _, x_, _, z_ in re.findall(r'PhysicsType="([^"]+?)\.\d+\.rmb"[^>]*>\s*<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', col)]))


def norm(n):
    n = n.lower(); n = re.sub(r'_lod\d+.*$', '', n); n = re.sub(r'^(o_|co_|s_|obj_)+', '', n); n = re.sub(r'_\d+$', '', n)
    return n.strip('_')


byname = {}
for c in F:
    if lod(c['name']) == 0:
        byname.setdefault(norm(c['name']), set()).add(int(c['f'].split('.')[1]))
seen = set(); miss = collections.Counter()
for r_ in []:        # smashables now come from the zone records, with exact mesh ids
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
PLANT_RE = re.compile(r'(?<!s)tree(?!stump|cutting)|bush|shrub|pinion|(?<![a-z])sage|grass|cactus|weed|fern', re.I)      # not StreetLight or Message
# every static object comes from the zone files (zones.py). Free roam shows records with no owner block, plus smashable scenery,
# whose block has an empty list and no owner. A block with a list or an owner ties the object to those events.
names_ = {}
for c in F:
    names_.setdefault(int(c['f'].split('.')[1]), c['name'])
skip = collections.Counter(); kept = collections.Counter(); uniq = {}; FLYERS = set()      # FH1's 100 discount signs, smashed by driving through them
ALWAYS = re.compile('FlowerHolder', re.I); ROADSIDE = re.compile('Barrier(?!Brand)', re.I)
STARTER = re.compile('TeamTrailer', re.I)     # each event owns one trailer at its start, where races.py puts its BeamNG mission


# Beaumont's parked gondola cabins: animated.py runs both lifts' cabins (ANIM_Cablecars_001 and _002), which ride over the parked ones' spots
ANIMATED_INSTEAD = re.compile(r'^Gondola_LOD', re.I)
# Gameplay markers, not scenery. Their records are listed for free roam, but each is owned by a GameObjs.xml gameplay node (event _NODEs,
# reset points, outposts, gas stations), and FH1 draws them only as markers from game state: Temp_Arrow_Pointer (mesh 6620) is an
# orange-and-white wedge lying on the road at each node, and Glow_01 (mesh 6509) is a 6 m wide, 17.7 m tall light pillar. The user saw
# the arrows lying on the road at the festival (2026-11-06)
MARKER = re.compile(r'^(Temp_Arrow_Pointer|Glow_\d)', re.I)


def event_kept(r_, nm_, x, y, z):
    """Event scenery the map needs in free roam"""
    if 0 in r_['lst']:      # listed for route 0, Colorado Free Roam (Tracks id 317), so FH1 shows it in free roam: each event's start pen
        return True         # (barriers, flags, trailer or outhouses) and discount-sign flyers (gameplay markers go earlier: MARKER). 21-31 alone are online free roam
    if r_['name']:          # an owner is an event id: Events.HorizonEventID in gamedb.slt, or Tracks.DevName
        return bool(STARTER.search(nm_))
    return bool(ALWAYS.search(nm_) or ROADSIDE.search(nm_))


# Race layouts: a record's list bytes are the Tracks.RouteId values (TrackRoute<n>.xml) of the races that show it, and 0 is the route of
# "Colorado Free Roam" (see free-roam.md). Each event route's props go in their own prefab, which the race mission loads while it runs
# (races.py copies it in as mainPrefab.prefab.json). Owner-named records are the events' start pens, listed for route 0, and stay out
import sqlite3, gamezip
RACE_ROUTES = {int(r_) for (r_,) in sqlite3.connect(gamezip.MEDIA + 'db/gamedb.slt').execute(
    'select t.RouteId from Events e join Races r on r.EventId = e.Id join Tracks t on t.id = r.TrackId where t.MediaName = "Colorado"')} - {0}
RACE = defaultdict(dict)            # route -> {(base, x, z): (t, name, x, y, z, rotation)}
for (did, p_), r_ in pickle.load(open('../zonerec.pkl', 'rb')).items():
    t = int(DRAW['t'][did]); nm_ = names_.get(t, ''); x, y, z = r_['pos'][0], r_['pos'][1], -r_['pos'][2]
    if ANIMATED_INSTEAD.search(nm_):
        skip['animated instead'] += 1
        continue
    if MARKER.search(nm_):
        skip['gameplay marker'] += 1
        continue
    if (r_['lst'] or r_['name']) and not event_kept(r_, nm_, x, y, z):
        skip['event only'] += 1
        rs_ = set(r_['lst']) & RACE_ROUTES
        if rs_ and not r_['name'] and nm_ and X0 < x < X1 and Z0 < z < Z1 and t not in NOMESH and not NOUSE.search(nm_) and not PLANT_RE.search(nm_) \
                and not re.match(r'S_|C_(?!CLRD)|.*Debri', nm_):
            k_ = (re.sub(r'_LOD\d+.*$', '', nm_), round(x, 1), round(z, 1))
            for rt_ in rs_:
                if k_ not in RACE[rt_] or lod(nm_) < lod(RACE[rt_][k_][1]):
                    RACE[rt_][k_] = (t, nm_, x, y, z, r_['rot'].tolist())
        continue
    if not (X0 < x < X1 and Z0 < z < Z1):
        continue
    if not nm_ or t in NOMESH or NOUSE.search(nm_) or PLANT_RE.search(nm_) or re.match(r'S_|C_(?!CLRD)|.*Debri', nm_) or abs(r_['pos'][0]) + abs(r_['pos'][2]) < 1:
        skip['not placed here'] += 1
        continue
    base = re.sub(r'_LOD\d+.*$', '', nm_)
    k_ = (base, round(x, 1), round(z, 1))
    if k_ not in uniq or lod(nm_) < lod(uniq[k_][1]):
        uniq[k_] = (t, nm_, x, y, z, r_['rot'].tolist(), did)
        if r_['name'].startswith('flyer_'):
            FLYERS.add(k_)
print('free-roam objects', len(uniq), dict(skip))
for (base, kx, kz), (t, nm_, x, y, z, Rr, did) in uniq.items():
    l_ = lod(nm_); t0 = t
    for d_ in range(1, l_ + 1):                  # use the LOD0 sibling when the game had a lower one loaded
        if re.sub(r'_LOD\d+.*$', '', names_.get(t - d_, '')) == base and lod(names_[t - d_]) == 0:
            t0 = t - d_
            break
    it = model_item(t0)
    if it is None:
        skip['no mesh'] += 1
        continue
    Rg = np.array(Rr).T; sc = float(np.linalg.norm(Rg[:, 1])); Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
    Rm = B @ game_rot(Rg) @ B.T
    if np.linalg.det(Rm) < 0:
        skip['mirrored'] += 1
    rows[it['item']].append(dict(ctxid=0, pos=[round(float(q), 3) for q in g2b(np.array([[x, y, z]]))[0]], rotationMatrix=[round(float(q), 5) for q in Rm.ravel()], scale=round(float(np.clip(sc, .2, 5)), 3), type=it['item']))
    if SMASH.query([x, z])[0] < .05 or (base, kx, kz) in FLYERS:
        rows[it['item']][-1]['smash'] = True; kept['smashable'] += 1
    # The placement's own lightmap, for models whose material takes one from the draw. Only when this record's mesh is the one drawn:
    # a LOD1 record's lightmap is laid out for LOD1's UV1
    if t0 == t and any(g[6]['draw'] for g in it['groups']):
        k_ = int(DRAW['s'][did][1])
        if k_ in IDX and lit_map(IDX[k_][0]):
            rows[it['item']][-1]['lm'] = IDX[k_][0]; kept['own lightmap'] += 1
    kept[re.sub(r'\d+', '#', nm_)[:30]] += 1
print('live objects', sum(kept.values()), 'skipped', dict(skip)); print(kept.most_common(70))
lap('smashables and free-roam objects')
RACEOBJ = defaultdict(list)         # route -> [(model number, BeamNG placement)]
for rt_, recs in RACE.items():
    for (base, kx, kz), (t, nm_, x, y, z, Rr) in recs.items():
        t0 = t
        for d_ in range(1, lod(nm_) + 1):
            if re.sub(r'_LOD\d+.*$', '', names_.get(t - d_, '')) == base and lod(names_[t - d_]) == 0:
                t0 = t - d_
                break
        if model_item(t0) is None:
            skip['race prop without mesh'] += 1
            continue
        Rg = np.array(Rr).T; sc = float(np.linalg.norm(Rg[:, 1])); Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
        Rm = B @ game_rot(Rg) @ B.T
        if np.linalg.det(Rm) < 0:
            continue
        RACEOBJ[rt_].append((t0, dict(pos=[round(float(q), 3) for q in g2b(np.array([[x, y, z]]))[0]], rotationMatrix=Rm.ravel().tolist(), scale=round(float(np.clip(sc, .2, 5)), 3))))
print('race layouts', len(RACEOBJ), 'routes,', sum(len(v) for v in RACEOBJ.values()), 'props from', len({no for v in RACEOBJ.values() for no, _ in v}), 'models')
PLANT = PLANT_RE
solid = 0; SOLIDS = collections.Counter(); INST = defaultdict(list)
for no, it in list(defs.items()):
    if not it or PLANT.search(it['item']) or it['item'] not in rows:
        continue
    for o in rows.pop(it['item']):
        SOLIDS[it['item']] += 1; smash_ = o.pop('smash', False); lm_ = o.pop('lm', None)
        Rm = np.array(o['rotationMatrix']).reshape(3, 3) * o['scale']; pos = np.array(o['pos'])
        # one shape per model. Mirrored objects stay baked, as do placements with their own lightmap, which a shared shape can't carry
        if np.linalg.det(Rm) > 0 and (np.abs(pos[:2]) < HALF - 4).all() and lm_ is None:
            INST[no].append(dict(o, smash=smash_)); solid += 1
            continue
        far_ = FARCUT if max(it['h'], it['w']) < 15 else 2                       # small objects stop at 800 m
        # 'lit' cells are TSStatics that core_environment lights at night, for lightmaps and glowing glass
        cat_ = 'vis' if smash_ else 'lit' if lm_ is not None or no in GLOWING else 'pro'
        for lvl_, top_, g in [(((0,) if it['low'] else (0, 1, far_)), True, g) for g in it['groups']] + [((1, far_), False, g) for g in it['low']]:
            mat, pb, nb, uv, t, uv1, spec = g
            if lm_ is not None and spec['draw'] and top_:       # the lightmap is laid out for this mesh's UV1, not its LOD1's
                nm_keep = NM; NM = {spec['tid']: spec['nm']} if spec['nm'] else {}; info_ = TX.get(spec['tid'])
                emis = bake_emissive(lm_, info_['rgb'], spec['win'], uv, uv1, t.reshape(-1, 3)) if spec['win'] and info_ else None
                mat = material(spec['tid'], alpha=True, two=True, glow=spec['glow'], ao=spec['ao'], lm=lm_, win=spec['win'], emis=emis, spec=spec['sp']) or mat; NM = nm_keep
            P = pb @ Rm.T + pos; N_ = nb @ Rm.T; N_ = N_ / np.maximum(np.linalg.norm(N_, axis=1, keepdims=True), 1e-9)
            c = P.mean(0)
            if (np.abs(c[:2]) >= HALF - 4).any():
                continue
            key = np.floor((c[:2] + HALF) / CELL).astype(int); u = int(key[0]) * 1000 + int(key[1])
            cells[(u, cat_)][(mat, lvl_)].append((P, N_, uv, t.reshape(-1, 3), uv1))
            stats['pro'] += len(t) // 3; stats['own_lightmap_tris'] += len(t) // 3 if lm_ is not None else 0
        solid += 1
print('pgeo problems', {k: v for k, v in stats.items() if k.startswith(('pgeo', 'proc'))}, LOSTSET.most_common(8))
print('solid placed objects', solid, 'plant instances', sum(len(v) for v in rows.values()), 'plant items', len(rows))
for item, r in rows.items():
    (fdir / f'{item}.forest4.json').write_text(''.join(json.dumps(o) + '\n' for o in r))
(level / 'art' / 'forest' / 'managedItemData.json').write_text(json.dumps({v['item']: v['d'] for v in defs.values() if v and v['item'] in rows}, indent=2))
for v in defs.values():
    if v and v['item'] not in rows:
        p_ = adir / f"{v['item']}.dae"
        for try_ in range(20):           # sync tools and indexers can hold a new file for a moment
            try:
                p_.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(.5)
print('card meshes', stats['card_meshes'], 'mean opaque coverage %', stats['card_fit_pct'] / max(stats['card_meshes'], 1))
print('forest', len(rows), 'items', stats['instances'], 'instances; no model', stats['inst_nomodel'], 'no mesh', stats['inst_nomesh'])
print('top items', sorted(((len(v), k) for k, v in rows.items()), reverse=True)[:12])
lap('solids and forest')

# ---- map art, for the preview. FH1's meshes are the whole ground: the level has no TerrainBlock
print('terrain meshes shown on', float(np.isfinite(Cov).mean()), 'of the square')
level.mkdir(parents=True, exist_ok=True)
r = json.load(open('../reg.json')); src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB')
box = (X0 / r['sx'] + r['ox'], Z0 / r['sz'] + r['oz'], X1 / r['sx'] + r['ox'], Z1 / r['sz'] + r['oz'])
base = src.transform((BASE, BASE), Image.EXTENT, box, Image.BICUBIC)
lap('map art')

# ---- shapes, materials, scene
sdir = level / 'art' / 'shapes'; sdir.mkdir(parents=True, exist_ok=True)
(sdir / f'{LVL}.materials.json').write_text(json.dumps(MATS, indent=2))
items = []; METRIC = []
sys.modules['__main__'].__file__ = os.path.abspath('daewrite.py')      # Windows workers re-import the main module, which must not be this script
POOL = ProcessPoolExecutor(min(6, os.cpu_count() or 1)); PENDING = set()


def queue_lod(*job, colmesh=None):
    """write_lod in a worker. The queue is short, because each job holds a copy of its geometry"""
    global PENDING
    while len(PENDING) >= 24:
        done_, PENDING = wait(PENDING, return_when=FIRST_COMPLETED)
        for f_ in done_:
            f_.result()
    PENDING.add(POOL.submit(write_lod, *job, colmesh=colmesh))
    return has_col(job[1], job[4], colmesh)



def detail_sizes(R_, cat):
    """detail sizes follow from a shape's radius, so every shape switches at the same distance from its edge. The engine
    measures from the centre: a kilometre-wide tile group would otherwise show LOD1 to a car on top of it"""
    s0 = max(int(R_ / (LOD_DIST[0] + R_) * PX), 3); s1 = max(min(int(R_ / (LOD_DIST[1] + R_) * PX), s0 - 1), 2)
    s2 = 1 if cat in ('col', 'wat', 'bak') else max(min(int(R_ / (PROP_FAR + R_) * PX), s1 - 1), 1)
    if s2 >= s1:
        s1 = s2 + 1; s0 = max(s0, s1 + 1)
    return s0, s1, s2


# Props collide with simple shapes instead of their mesh, as each colliding object is its own copy in physics at about 41.5 bytes a
# triangle: fences alone held 2.7M of the 8M instanced collision triangles. Thin and simple props (BOXED) get one or two boxes; the rest
# a convex hull (rocks, logs) or a skyline of boxes (buildings, tents) from colfit.py, even where that isn't fewer triangles, as boxes
# behave better in a crash than a detailed mesh. FH1_COLBOX=0 keeps
# the meshes, for comparison. KEEPMESH names surfaces a wheel may roll on, where seams between boxes would be bumps
COLBOX = os.environ.get('FH1_COLBOX', '1') != '0'
BOXED = re.compile(r'fence|wall|gate|railing|barrier|logwall|wood0|proc_cars|truckcab|pole|pylon|streetlight|lamppost|signs?(?![a-z])|sign_|'
                   r'(?<![a-z])bin[a-z]?(?![a-z])|bench|table|container|skip|haybale|crate|rack|hoarding|bollard', re.I)
NOCOL = re.compile(r'rope|BannerSingleLane', re.I)       # festival bunting, 35 m across, above the cars; banners over a lane, whose hull would close it
# Placed props go in the forest rather than one TSStatic each: 61k scene objects, each with its own shape instance, against a small record
# per forest item. Glowing props stay TSStatics for their instance colour, as do the shapes animated.py swaps out (OBJECTS there).
# FH1_PROPFOREST=0 keeps TSStatics, for comparison
PROPFOREST = os.environ.get('FH1_PROPFOREST', '1') != '0'; PROPDATA = {}
KEEPSTATIC = {6625, 6627, 6429}
KEEPMESH = re.compile(r'bridge|ramp|jump|stage|platform|deck|road|tunnel|pavement|kerb|curb|car_?park|parking|dam(?![a-z])', re.I)


def prop_shape(no, objs):
    """write art/shapes/fh1/m<no>.dae, one shared shape for every placement of model `no` -> the TSStatics' collision type"""
    it = defs[no]; far_ = FARCUT if max(it['h'], it['w']) < 15 else 2
    shape = [((g[0], (0,) if it['low'] else (0, 1, far_)),) + tuple(g[1:]) for g in it['groups']] + [((g[0], (1, far_)),) + tuple(g[1:]) for g in it['low']]
    allp = np.concatenate([g[1] for g in shape]); R_ = float(np.linalg.norm(allp.max(0) - allp.min(0))) / 2
    collide = None if all(o['smash'] for o in objs) or NOCOL.search(it['item']) else 2; cm = None
    if COLBOX and collide is not None and not KEEPMESH.search(it['item']):
        P_ = colfit.points(shape, collide, FARCUT); had = sum(len(g[4]) // 3 for g in shape if collide in g[0][1] or FARCUT in g[0][1])
        kind, cm = ('box', colfit.boxes(P_)) if BOXED.search(it['item']) and 2 * R_ < 16 else (colfit.generic(P_) or (None, None))
        if cm is not None:
            n_ = sum(not o['smash'] for o in objs)
            stats[f'col_{kind}_shapes'] += 1; stats['col_tris_saved'] += n_ * (had - len(cm[1]) // 3)
    col = queue_lod(sdir / 'fh1' / f'm{no}.dae', shape, np.zeros(3), detail_sizes(R_, 'pro'), collide, colmesh=cm)
    return 'Collision Mesh' if col else 'None'


SHAPE_CT = {}
for no, objs in INST.items():
    ct = SHAPE_CT[no] = prop_shape(no, objs)
    glow = no in GLOWING or lod_sibling(no) in GLOWING
    if PROPFOREST and not glow and no not in KEEPSTATIC:
        it_ = f'fh1_m{no}'
        PROPDATA[it_] = {'name': it_, 'internalName': it_, 'class': 'TSForestItemData', 'branchAmp': 0, 'detailAmp': 0, 'detailFreq': 0,
                         'mass': 1, 'rigidity': 1, 'shapeFile': f'{rel}/art/shapes/fh1/m{no}.dae', 'windScale': 0}
        (fdir / f'{it_}.forest4.json').write_text(''.join(json.dumps({'ctxid': 0, 'pos': o['pos'], 'rotationMatrix': np.array(o['rotationMatrix']).reshape(3, 3).T.ravel().tolist(),
                                                                     'scale': o['scale'], 'type': it_}) + '\n' for o in objs))
        continue
    for i_, o in enumerate(objs):
        items.append({'class': 'TSStatic', 'name': f'fh1_m{no}_{i_}', '__parent': 'fh1', 'shapeName': f'{rel}/art/shapes/fh1/m{no}.dae', 'position': o['pos'],
                      'rotationMatrix': np.array(o['rotationMatrix']).reshape(3, 3).T.ravel().tolist(),     # a TSStatic lists its axes as rows, measured on stock guardrail chains
                      'scale': [o['scale']] * 3, 'collisionType': 'None' if o['smash'] else ct, 'decalType': 'None' if o['smash'] else ct,
                      **({'useInstanceRenderData': True, 'instanceColor': [0, 0, 0, 1], 'nightEmissive': '1'} if glow else {})})
if PROPDATA:
    mf_ = level / 'art' / 'forest' / 'managedItemData.json'; mf_.write_text(json.dumps({**json.loads(mf_.read_text()), **PROPDATA}, indent=2))
print('instanced', sum(len(v) for v in INST.values()), 'objects from', len(INST), 'shapes,', len(PROPDATA), 'shapes as forest items')
print('simple collision', {k[4:-7]: v for k, v in stats.items() if k.startswith('col_') and k.endswith('_shapes')}, 'shapes,', stats['col_tris_saved'], 'collision triangles saved')
# One prefab per race route, in the stock mission prefab format (a SimGroup_ line, then its objects), loaded only while that race runs.
# Light props a car would knock over in FH1 (cones, flags, chevron boards, barrels, signs) don't collide: a TSStatic can't fall over
SOFT = re.compile(r'cone|flag|chevron|barrel|equipsign|banner|arrow|countdown', re.I)
rdir = level / 'art' / 'fh1_races'; rdir.mkdir(parents=True, exist_ok=True)
for rt_, objs in sorted(RACEOBJ.items()):
    lines = [{'name': 'SimGroup_', 'class': 'SimGroup', 'groupPosition': '0 0 0'}]
    for i_, (no, o) in enumerate(objs):
        if no not in SHAPE_CT:
            SHAPE_CT[no] = prop_shape(no, [dict(smash=False)])
        ct = 'None' if SOFT.search(defs[no]['item']) else SHAPE_CT[no]
        lines.append({'class': 'TSStatic', '__parent': 'SimGroup_', 'shapeName': f'{rel}/art/shapes/fh1/m{no}.dae', 'position': o['pos'],
                      'rotationMatrix': np.array(o['rotationMatrix']).reshape(3, 3).T.ravel().tolist(), 'scale': [o['scale']] * 3, 'collisionType': ct, 'decalType': ct})
    (rdir / f'route_{rt_}.prefab.json').write_text(''.join(json.dumps(l_) + '\n' for l_ in lines))
print('race prefabs', len(RACEOBJ), 'in', rdir)
# barrier collision for the whole level at once, so walls run on across cell lines; each is carried by the cell its first piece is in
RAILMESH, (RAIL_SEGS, RAIL_CHAINS) = colfit.rails({u: np.concatenate(t) for u, t in RAILS.items()}, POOL) if COLBOX else ({}, (0, 0))
for (u, cat), mm in cells.items():
    groups = []
    for mat, parts in mm.items():
        ps = []; ns = []; uv = []; ix = []; u2 = []; o = 0
        for a_, bb_, cc, t, d_ in parts:
            ps.append(a_); ns.append(bb_); uv.append(cc); ix.append((t + o).ravel()); u2.append(d_); o += len(a_)
        wd_ = max(x.shape[1] for x in u2); u2 = [x if x.shape[1] == wd_ else np.c_[x, np.ones((len(x), wd_ - x.shape[1]))] for x in u2]      # a vertex colour column, else 1
        groups.append((mat, np.concatenate(ps), np.concatenate(ns), np.concatenate(uv), np.concatenate(ix), np.concatenate(u2)))
    allp = np.concatenate([g[1] for g in groups]); org = np.round((allp.min(0) + allp.max(0)) / 2)
    R_ = float(np.linalg.norm(allp.max(0) - allp.min(0))) / 2
    # a mesh drawn at every LOD is cooked once per detail level, so those go in a one-detail shape: one copy instead of three
    whole = [((g[0][0], (0,)),) + tuple(g[1:]) for g in groups if {0, 1, 2} <= set(g[0][1])]
    lods = [g for g in groups if not {0, 1, 2} <= set(g[0][1])]; collide = {'col': 0, 'pro': 2, 'lit': 2}.get(cat)
    rail = RAILMESH.get(u) if cat == 'bar' else None
    if cat == 'bar' and not COLBOX:
        collide = 2
    for fn, gs, single in ((f'c{u}_{cat}', lods, False), (f'c{u}_{cat}f', whole, True)):
        if not gs:
            continue
        p_ = np.concatenate([g[1] for g in gs]); o_ = np.round((p_.min(0) + p_.max(0)) / 2); sizes = detail_sizes(float(np.linalg.norm(p_.max(0) - p_.min(0))) / 2, cat)
        cm = None
        if rail is not None:        # the first shape of the cell carries its rail walls, in world space as write_lod subtracts o_
            cm = rail; collide = 0; stats['col_rail_tris'] += len(rail[1]) // 3; rail = None
        elif cat == 'bar' and COLBOX:
            collide = None
        col = queue_lod(sdir / 'fh1' / f'{fn}.dae', gs, o_, sizes[2:] if single else sizes, 0 if single and collide is not None else collide, colmesh=cm)
        ct = 'Collision Mesh' if col else 'None'
        items.append({'class': 'TSStatic', 'name': f'fh1_{fn}', '__parent': 'fh1', 'shapeName': f'{rel}/art/shapes/fh1/{fn}.dae',
                      'position': [float(x) for x in o_], 'collisionType': ct, 'decalType': ct,
                      **({'useInstanceRenderData': True, 'instanceColor': [0, 0, 0, 1], 'nightEmissive': '1'} if cat == 'lit' else {})})
    METRIC.append((org, R_, cat, [sum(len(g[4]) // 3 for g in groups if lv in g[0][1]) for lv in range(3)]))
print('rail collision', RAIL_CHAINS, 'walls from', RAIL_SEGS, 'pieces,', stats['col_rail_tris'], 'triangles, for', sum(len(t) for v in RAILS.values() for t in v), 'barrier triangles at LOD0')
lap('shapes queued')
for f_ in PENDING:
    f_.result()
POOL.shutdown()
lap('shape writers finishing')
cam = g2b(np.array([[-5681., 350., 5301.]]))[0]; drawn = [0, 0, 0, 0]; before = 0
for org, R_, cat, tr in METRIC:
    d_ = float(np.linalg.norm(org - cam)); lv = 0 if d_ < LOD_DIST[SKIPMID] else 1 if d_ < LOD_DIST[1] else 2
    if cat not in ('col', 'wat', 'bak') and d_ > PROP_FAR:
        lv = 3
    before += tr[0]
    if lv < 3:
        drawn[lv] += tr[lv]
print('static tris, all cells at LOD0', before, 'estimated drawn from the outpost: lod0', drawn[0], 'lod1', drawn[1], 'lod2', drawn[2], 'total', sum(drawn))
fb = fa = 0; fc = collections.Counter()
for v in defs.values():
    if not v or v['item'] not in rows:
        continue
    r_ = max(v['h'], v['w'], 1) / 2
    for o in rows[v['item']]:
        d_ = float(np.linalg.norm(np.array(o['pos']) - cam)); px = r_ * o['scale'] / max(d_, 1) * PX
        if d_ > 6000:
            continue
        fb += v['tri'][0]
        if px >= 120:
            fa += v['tri'][0]; fc['lod0'] += 1
        elif px >= 40:
            fa += v['tri'][1]; fc['lod1'] += 1
        else:
            fa += 2; fc['imposter'] += 1
print('plant tris before', fb, 'after', fa, dict(fc))
pickle.dump(dict(world=sorted(int(c['f'].split('.')[1]) for c in sel), placed=sorted(k for k, v in defs.items() if v)), open('../used.pkl', 'wb'))
print('solid types', collections.Counter(re.sub(r'_?\d+$', '', k) for k in SOLIDS).most_common(30))
print('shapes', len(items), 'materials', len(MATS), 'textures', sum(1 for v in TX.info.values() if v), 'missing textures', len(TX.missing))

RT = gamepath.RIBBON
pat = (r"route_checkpoint_\d+'[^>]*>\s*<Transform pos\.x='([-\d.]+)' pos\.y='([-\d.]+)' pos\.z='([-\d.]+)' "
       r"facing\.x='([-\d.e]+)' facing\.y='[-\d.e]+' facing\.z='([-\d.e]+)'")
bestr = None
SBOX = (-6700, -5200, 4400, 6000)       # Red Rock. A chunk without it, such as Clear Springs, spawns on a route inside the chunk
if not (X0 < SBOX[0] and SBOX[1] < X1 and Z0 < SBOX[2] and SBOX[3] < Z1):
    SBOX = (X0 + 200, X1 - 200, Z0 + 200, Z1 - 200)
for f in glob.glob(RT + 'TrackRoute*.xml'):
    P = [tuple(map(float, x)) for x in re.findall(pat, open(f, errors='replace').read())]
    P = [(a, b, -c, d, -e) for a, b, c, d, e in P]
    ins = [x for x in P if SBOX[0] < x[0] < SBOX[1] and SBOX[2] < x[2] < SBOX[3]]
    if ins and (bestr is None or len(ins) > bestr[0]):
        bestr = (len(ins), os.path.basename(f), ins[0])
x, y, z, fx, fz = bestr[2]
pos = g2b(np.array([[x, y, z]]))[0]; ri = int((x - X0) / SQ); rj = int((Z1 - z) / SQ)
if np.isfinite(Road[rj, ri]):
    pos[2] = Road[rj, ri] + frame.LIFT        # the rasters keep FH1's heights
yaw = math.atan2(fx, fz); c_, s_ = math.cos(yaw), math.sin(yaw)
spawn = {'class': 'SpawnSphere', 'name': 'spawn_redrock', '__parent': 'MissionGroup', 'dataBlock': 'SpawnSphereMarker',
         'position': [float(pos[0]), float(pos[1]), float(pos[2] + .6)], 'rotationMatrix': [c_, -s_, 0, s_, c_, 0, 0, 0, 1], 'radius': 1}
main = level / 'main'; mg = main / 'MissionGroup'; (mg / 'fh1').mkdir(parents=True, exist_ok=True)
(main / 'items.level.json').write_text(json.dumps({'name': 'MissionGroup', 'class': 'SimGroup', 'enabled': '1'}) + '\n')
obj = [{'class': 'LevelInfo', 'name': 'theLevelInfo', '__parent': 'MissionGroup', 'visibleDistance': VISIBLE, 'gravity': -9.81, 'canvasClearColor': [.5, .6, .7, 1]},
       {'class': 'TimeOfDay', 'name': 'tod', '__parent': 'MissionGroup', 'time': .1, 'play': False, 'azimuthOverride': 0, 'startTime': .1},
       {'class': 'ScatterSky', 'name': 'sunsky', '__parent': 'MissionGroup', 'position': [0, 0, 0], 'shadowDistance': 1600, 'shadowSoftness': .2, 'brightness': 1.0},
       spawn, {'name': 'fh1', 'class': 'SimGroup', '__parent': 'MissionGroup'},
       {'name': 'theForest', 'class': 'Forest', '__parent': 'MissionGroup', 'position': [0, 0, 0]}]
(mg / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in obj))
(mg / 'fh1' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in items))
base.resize((1024, 1024)).save(level / f'{LVL}.jpg', quality=90)
(level / 'info.json').write_text(json.dumps({
    'title': TITLE, 'description': 'Personal conversion of a chunk of Forza Horizon (2012) Colorado from your own copy. Not for redistribution.',
    'authors': 'Playground Games / Turn 10 (original); converted locally', 'country': 'United States', 'biome': 'desert',
    'size': [int(2 * HALF), int(2 * HALF)], 'previews': [f'{LVL}.jpg'],
    'spawnPoints': [{'objectname': 'spawn_redrock', 'translationId': 'Red Rock', 'preview': f'{LVL}.jpg'}], 'defaultSpawnPointName': 'spawn_redrock'}, indent=2))
pickle.dump({k: v for k, v in surf.items()}, open('../surf.pkl', 'wb'))
keep_pickle(INFO_P, TX.known); keep_pickle(PAIR_P, PAIR)
lap('scene files')
print('done', f'{time.time() - start_:.0f}s ->', level)
# The scripts after the build, each with the ones it must wait for. animated reads fh1_nav and grass's forest files, particles adds spray nodes to animated's fh1_movers.json, spectators edits the forest item list after grass and adds forest files after animated reads them, levelinfo and navmap both
# edit info.json, and grade grades grass's, animated's and spectators' textures (people.py, the cutscene characters, is left out: they belong to cutscenes FH1 plays, and some stand under the ground). The items files they share (the top-level one, and fh1's, which water and animated edit and
# nightlights reads) go through mission.py, which locks them and replaces them whole
AFTER = {'sky': (), 'navmap': (), 'grass': (), 'ambience': (), 'particles': ('animated',), 'water': (), 'nightlights': (), 'grade': ('grass', 'animated', 'spectators'), 'levelinfo': ('navmap',), 'races': (),
         'animated': ('navmap', 'grass'), 'spectators': ('grass', 'animated')}
SAY = threading.Lock()      # one whole line at a time from the scripts running side by side


def post_script(s_):
    """one script, its output piped into the log with its name in front -> seconds taken"""
    t_ = time.time()
    p_ = subprocess.Popen([sys.executable, '-u'] + (['-m', 'cProfile', '-o', str(PROF / f'{s_}.prof')] if PROF else []) + [f'{s_}.py', LVL], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=dict(os.environ, PYTHONIOENCODING='utf8'), encoding='utf8', errors='replace')
    for l_ in p_.stdout:
        with SAY:
            print(f'[{s_}] {l_.rstrip()}', flush=True)
    if p_.wait():
        raise subprocess.CalledProcessError(p_.returncode, f'{s_}.py')
    return time.time() - t_


def post_build():
    """the AFTER scripts, each started once those it waits for are done. After a failure nothing new starts, and the first error is raised once the rest finish"""
    done_ = set(); running = {}; failed = []
    with ThreadPoolExecutor(len(AFTER)) as ex:
        while True:
            for s_, need in AFTER.items():
                if not failed and s_ not in done_ and s_ not in running.values() and all(n in done_ for n in need):
                    running[ex.submit(post_script, s_)] = s_
            if not running:
                break
            fin, _ = wait(running, return_when=FIRST_COMPLETED)
            for f_ in fin:
                s_ = running.pop(f_)
                try:
                    TIMES.append((f'{s_}.py, side by side', f_.result())); done_.add(s_)
                    with SAY:
                        print(f'[time] {s_}.py {TIMES[-1][1]:.1f}s', flush=True)
                except Exception as e:
                    failed.append(e)
    if failed:
        raise failed[0]


if len(sys.argv) == 1:                  # the wipe at the top also removes the sky. sky.py only patches installed levels
    post_build(); lap('post-build scripts, wall clock')
if PROF:
    PROFILER.disable(); PROFILER.dump_stats(str(PROF / 'build3.prof')); print('profile', PROF.resolve())
print('[time] summary, slowest first, total', f'{time.time() - T_START:.0f}s')
for n_, t_ in sorted(TIMES, key=lambda x: -x[1]):
    print(f'[time] {t_:8.1f}s  {n_}')
