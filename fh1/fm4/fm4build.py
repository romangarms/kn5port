"""Build an FM4 track as a BeamNG level, every layout (ribbon) in one: one shape per mesh file, one TSStatic per draw, diffuse and blended
ground materials, FM4's collision, a spawn on each layout's grid, and FM4's own menu art and text. Most detailed LOD only, no lightmaps or
paint palettes. See docs/fm4.md.
python fm4build.py Tsukuba [output mod folder]
Work data (bin.zip extracted) goes in fh1/fm4/<track>/ (or $FM4_WORK/<track>/); the level goes to mods/unpacked/fm4_<track>.
The game folder is $FM4_DUMP, holding Media/."""
import collections
import json
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(1, str(HERE.parent / 'pipeline')); sys.path.insert(2, str(HERE.parent.parent))
import fm4ai
import fm4col
import fm4mesh
import fm4pvs
import fm4rmb
import fm4sky
import fm4ui
import fm4zip
import dds
import tex
import xenos
from daewrite import write_lod
from kn5port import beamng
from scipy.spatial import cKDTree

FM4 = Path(os.environ.get('FM4_DUMP', r'D:\Roms\Xbox 360\Xenia\FM4XeniaFix'))
MEDIA = FM4 / 'Media'
TRACK = sys.argv[1] if len(sys.argv) > 1 else 'Tsukuba'
TRACK = next((d.name for d in (MEDIA / 'Tracks').iterdir() if d.name.lower() == TRACK.lower()), TRACK)     # the build queue's lane gives it in lower case
LVL = f'fm4_{TRACK.lower()}'
WORK = Path(os.environ.get('FM4_WORK') or HERE) / TRACK.lower()       # FM4_WORK: the app's work folder
ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(beamng.find_mods()) / 'unpacked' / LVL
LEVEL = ROOT / 'levels' / LVL; REL = f'/levels/{LVL}'
T0 = time.time()

# game mesh frame (x east, y up, z south) -> BeamNG (x, -z, y); a draw's frame has z flipped, so draw space -> BeamNG is (x, z, y)
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); P = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0.]])
NOUSE = re.compile(r'occluder|shadow(?!ed)|^SKY_|proxy|collision|_CAGE|reflect', re.I)      # VEG_*_cluster_shadowed_* are bushes
# The sky dome (sky_*) isn't placed: fm4sky.py bakes it, with the backdrop painted into it, into the level's SkyBox
NODRAW = re.compile(r'^(sun_contribution|add_anim_camera_flash|flare_|sky_)')
# *_nolm_vclr_* shaders light by vertex colour where others use a lightmap: tex * lerp(V2LightmapColor1, V2LightmapColor2, vertex colour), the
# two colours taken at their shader defaults, 0 and 1 (their runtime values aren't read yet). The tree walls that ring the circuit (VEG_*_TreeWall_*,
# cards up to 430 m long) use one and carry no normals, as nothing in them is lit; they get up-facing normals here. clr_0 draws a flat colour,
# the material's pixel constant c0 (power lines, wires)
VCLR = re.compile(r'nolm_vclr')
NOCOLLIDE = re.compile(r'^(grass|bush|tree|anim_flag|rdline|flare|sky|clr_|chain_diff_opac_2)')
NOCOLLIDE_NAME = re.compile(r'^VEG_|^OBJ_Flag|crowd|spectator|_Tree|Bush|Grass|^RCEDAY_CameraFlash', re.I)
# FM4's track collision (<track>_track_NN.col) is the drivable surface and barriers. Its surface ids index Media/physics/surfaceTypes.xml;
# where it exists, these visual families don't collide, so two coincident surfaces don't disagree on ground type
COLFAMILY = re.compile(r'^(road_|shldr_|rdedg_)')
WALLS = os.environ.get('FM4_WALLS', '0') == '1'      # FM4's invisible track-limit walls (surface 9, about 117 m tall)
COLGROUND = [(r'Invisible', None), (r'Cobble', 'COBBLESTONE'), (r'^Dirt', 'DIRT'), (r'Grass(?!crete)|SlowGrass', 'GRASS'), (r'Gravel', 'GRAVEL'),
             (r'Sand', 'SAND'), (r'Rumble|SpeedBump', 'RUMBLE_STRIP'), (r'GuardRail|WireFence', 'METAL'), (r'RubberTire|FoamBarrier', 'RUBBER'), (r'FoamSign|BrakeMarker', 'PLASTIC'),
             (r'Wood|HayBail', 'WOOD'), (r'SafetyPlastic', 'PLASTIC'), (r'Grasscrete', 'GRASS'), (r'', 'ASPHALT')]
GROUND = [(re.compile(r'^road_'), 'ASPHALT'), (re.compile(r'^shldr_'), 'ASPHALT'), (re.compile(r'^terr_'), 'GRASS'), (re.compile(r'^rdedg_'), 'DIRT')]
DIFFUSE = ('Diffuse_TextureSampler', 'DiffuseSampler', 'Blend_ASampler', 'Blend_BSampler')
# Lightmaps (docs/fm4.md, Lightmaps). Lit objects have no sun term: colour = diffuse * lerp(V2LightmapColor1, V2LightmapColor2, lightmap), the two
# colours TrackSettings' DarkColor and LightColor (read live from FM4's GPU registers c247, c248 on Tsukuba). The lightmap is sampled at texcoord2
# through the UV box, then its texture record's scale and offset (Light_Map_uvOS), from the record's own file or else its parent atlas. Here it's
# an ambientOcclusionMap on UV1, as FH1's AO maps: lum(lerp(Dark, Light, lightmap)) / lum(Light), so fully lit is 1 and BeamNG's light supplies the rest
LM_SAMPLER = 'Light_MapSampler'
# Fog (docs/fm4.md, Fog): FM4's vertex shaders fog by 1 - 2^(-FogConsts.w * max(d - FogConsts.x, 0)) towards FogColor. Read live on Tsukuba:
# FogConsts = (Settings0 Near, FarDensity / (Far - Near), NearDensity, 0.0002), FogColor = Settings0 Color. The 0.0002 isn't a TrackSettings value
# (another fog record in memory carries it too); it's FOG_W. BeamNG's fog is exp(-density * (d - offset)): density = FOG_W ln 2
FOG_W = 0.0002


def lap(what):
    print(f'[{time.time() - T0:6.1f}s] {what}', flush=True)


# ---- game data
if not (WORK / 'x').exists():
    fm4zip.main(str(MEDIA / 'Tracks' / TRACK / 'bin.zip'), str(WORK))
tex.X = str(WORK / 'x')
tex.STX = True      # textures found only in <Track>.stx.bin (texture-table kind 1), docs/fm4.md, Texture pack
# Every layout (ribbon) goes into the one level. A ribbon's FPVS places what is visible from that layout, so the level places the union of
# their draws. Layouts whose texture, shader and mesh tables are equal share a table group (all but some of Alps' and Maple Valley's).
ROWS = fm4ui.layouts(TRACK)
RDIRS = {int(d.name[7:]): d for d in sorted((MEDIA / 'Tracks' / TRACK).glob('Ribbon_*'))}
PVS = {ri: fm4pvs.tables(next(d.glob('*.pvs')).read_bytes()) for ri, d in RDIRS.items() if any(d.glob('*.pvs'))}
# A ribbon whose FPVS places meshes bin.zip doesn't have was made against other data: Alps' reverse ribbons 3 and 4 have 1,502 mesh records
# to bin.zip's 1,301 and place 88 missing meshes. Their FPVS and .col are left out, so those layouts take their geometry from the other
# ribbons, as a layout with no FPVS does; their spawns and AI lines stay
HAVE = {int(f_.name.split('.')[1]) for f_ in (WORK / 'x').glob('*out.*.rmb.bin')}
STALE = {ri: n for ri, pv in PVS.items() for n in [len({d['mesh'] for d in pv['draws']} - HAVE)] if n}
for ri in STALE: del PVS[ri]
TG = []; RG = {}
for ri, pv in PVS.items():
    sig = (pv['textures'], pv['shaders'], [m['tex'] for m in pv['meshes']])
    RG[ri] = next((g for g, t in enumerate(TG) if (t['textures'], t['shaders'], [m['tex'] for m in t['meshes']]) == sig), len(TG))
    if RG[ri] == len(TG): TG.append(pv)
RIB0 = min(PVS); PV = PVS[RIB0]; TT = PV['textures']; MR = PV['meshes']
FETCH = fm4mesh.fetch_lists(str(WORK))
SAMPLERS = {}
for f_ in (WORK / 'x').glob('shaders_track_*.fxobj'):
    ps = [s for s in xenos.shaders(f_.read_bytes()) if s['kind'] == 'ps']
    if ps: SAMPLERS[f_.stem[14:].lower()] = {c[0]: c[2] for c in ps[0]['consts'] if c[1] == 3}
STEM = next((WORK / 'x').glob('*out.*.rmb.bin')).name.split('.')[0]      # the mesh files' prefix; AmalfiGP's start at 00001
TS = fm4sky.settings(MEDIA / 'Tracks' / TRACK / 'TrackSettings.xml')
if STALE: lap('ribbons left out, placing meshes bin.zip lacks: ' + ', '.join(f'{ri} ({n})' for ri, n in STALE.items()))
lap(f'{TRACK}: layouts {sorted(PVS)} placed, {len(ROWS)} in gamedb, {len(TG)} table groups; ' +
    ', '.join(f'ribbon {ri} {len(pv["draws"])} draws' for ri, pv in PVS.items()) + f'; {len(SAMPLERS)} shaders')


def lod_of(name):
    m = re.search(r'_LOD(\d+)', name); return int(m.group(1)) if m else 0


FILES = {}


def mesh_file(no):
    if no not in FILES:
        b = (WORK / 'x' / f'{STEM}.{no:05d}.rmb.bin').read_bytes(); p = fm4rmb.parse(b)
        FILES[no] = (b, p, fm4rmb.file_shaders(b, p), fm4mesh.materials(b, p))
    return FILES[no]


# the union of every layout's draws, each with the layouts that place it; then the most detailed LOD of each object (LODs are separate
# draws at one spot)
UNION = {}
for ri, pv in PVS.items():
    for d in pv['draws']:
        k = (RG[ri], d['mesh'], tuple(np.round(d['pos'], 2)), tuple(np.round(d['rot'].ravel(), 2)))
        UNION.setdefault(k, dict(d, g=RG[ri], ribs=set()))['ribs'].add(ri)
groups = collections.defaultdict(list)
for k, d in UNION.items():
    _, p, _, _ = mesh_file(d['mesh']); nm = p['meshes'][0]['name']
    groups[(k[0], k[2], k[3], re.sub(r'_LOD\d+', '', nm))].append((lod_of(nm), d))
DRAWS = []
for v in groups.values():
    d = min(v, key=lambda x: x[0])[1]; d['ribs'] = set().union(*(x[1]['ribs'] for x in v)); DRAWS.append(d)
lap(f'{len(UNION)} draws over all layouts, {len(DRAWS)} after keeping LOD0, {sum(len(d["ribs"]) < len(PVS) for d in DRAWS)} in only some layouts')

# Stand-ins: a mesh named ..LOD_<n> (not _LODnn) can be a whole copy of finer meshes, which FM4 never draws with them but nothing
# decoded says so (its draw's LOD bits are clear, like any mesh's): the Alps' ROAD_Alps_InfieldLOD_003 is the infield road again, at the
# same height, and z-fought it. One is left out when nearly all its vertices (STANDIN_SHARED, to 1 cm) are other draws' vertices; one
# that stands alone stays (Laguna Seca's only bleacher is BLDG_LGNA_Blcher_LOD_001)
STANDIN = re.compile(r'LOD_\d+$'); STANDIN_SHARED = .95


def draw_verts(d):
    """a draw's vertices in draw space, rows of (x, y, z)"""
    _, p, shs, _ = mesh_file(d['mesh']); out = []
    for m in p['meshes']:
        lay = fm4mesh.mesh_layout(m, shs, FETCH)
        if lay is not None: out.append(fm4mesh.attr(m, lay, 'position0').astype('f8') @ d['rot'] + d['pos'])
    return np.vstack(out) if out else np.zeros((0, 3))


_si = [d for d in DRAWS if STANDIN.search(mesh_file(d['mesh'])[1]['meshes'][0]['name'])]
if _si:
    _keys = set()
    _ids = {id(d) for d in _si}
    for d in DRAWS:
        if id(d) not in _ids: _keys.update(map(tuple, np.rint(draw_verts(d) * 100).astype(np.int64)))
    for d in _si:
        v = np.rint(draw_verts(d) * 100).astype(np.int64); f = np.mean([tuple(x) in _keys for x in v]) if len(v) else 0
        nm = mesh_file(d['mesh'])[1]['meshes'][0]['name']
        if f >= STANDIN_SHARED: DRAWS[:] = [x for x in DRAWS if x is not d]; lap(f'{nm}: left out, a copy of other meshes ({f:.1%} of its vertices are theirs)')
        else: lap(f'{nm}: kept, {f:.1%} of its vertices are other meshes\'')

# ---- textures
TEXD = LEVEL / 'art' / 'textures'; TEXD.mkdir(parents=True, exist_ok=True)
TINFO = {}


def bc4_decode(i):
    t = tex.bix(i); bw = (t['w'] + 3) // 4; bh = (t['h'] + 3) // 4
    blk = np.frombuffer(tex.untile(tex.top_mip(t), bw, bh, 8), '>u2').astype('<u2').view('u1').reshape(-1, 8)
    v = tex.bc4(blk).reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(bh * 4, bw * 4)[:t['h'], :t['w']].astype('u1')
    return np.dstack([v, v, v, np.full_like(v, 255)])


def decode(i):
    """texture i as an (h, w, 4) array, or None"""
    t = tex.bix(i)
    if not t: return None
    try:
        return bc4_decode(i) if t['fmt'] == 0x7b else tex.decode(tex.top_mip(t), t['w'], t['h'], t['fmt'])
    except Exception as e:
        print('texture', hex(i), 'failed:', e); return None


def data_map(name, a):
    """a 0..1 array as a BC5 .data.dds, whose red BeamNG reads as opacity (8 bits, where DXT1 keeps 5)"""
    p_ = TEXD / f'{name}.data.dds'
    if not p_.exists():
        v = (np.clip(a, 0, 1) * 255 + .5).astype('u1'); dds.save_bc5(p_, np.dstack([v, v]))
    return f'{REL}/art/textures/{p_.name}'


def srgb(lin):
    return np.where(lin <= .0031308, lin * 12.92, 1.055 * np.power(np.maximum(lin, 0), 1 / 2.4) - .055)


def half_color(i):
    """texture i at half its linear value, for a layer's detail map at strength 1 (saturate(2 d)): the layer's own colour at its own
    scale, as FH1's build3.half_color"""
    p_ = TEXD / f'fm4_{i:x}_h.color.dds'
    if not p_.exists():
        c = decode(i)[..., :3] / 255; lin = np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)
        dds.save(p_, (srgb(lin / 2) * 255 + .5).astype('u1'))
    return f'{REL}/art/textures/{p_.name}'


LMAO = {}


def lightmap(shader, slots, rec, tt):
    """-> (texture id, (su, sv, ou, ov)) of a section's lightmap, or None (no sampler, empty slot, or one the draw supplies)"""
    r = SAMPLERS.get(shader, {}).get(LM_SAMPLER)
    if r is None or r >= len(slots) or slots[r] >= len(rec['tex']): return None
    t = tt[rec['tex'][slots[r]]]; tid = t[0] if tex.bix(t[0]) else t[1]
    b = tex.bix(tid)
    if not b or b['fmt'] not in (0x52, 0x53, 0x54): return None
    return tid, t[2:6]


def ao_map(tid):
    """the lightmap texture as an AO data map, see LM_SAMPLER"""
    if tid not in LMAO:
        a = decode(tid)
        if a is None: LMAO[tid] = None
        else:
            lm = fm4sky.xenos_degamma(a[..., :3].astype('f8') / 255)
            dark = np.array([TS['DarkColor'][k] for k in 'rgb']); light = np.array([TS['LightColor'][k] for k in 'rgb'])
            lum = np.array([.2126, .7152, .0722])
            LMAO[tid] = data_map(f'fm4_{tid:x}_ao', ((dark + (light - dark) * lm) @ lum) / (light @ lum))
    return LMAO[tid]


def texture(i):
    """write fm4_<id>.color.dds (and _o.data.dds when it has cut-out alpha) -> info or None"""
    if i in TINFO: return TINFO[i]
    t = tex.bix(i); info = None
    if t:
        a = decode(i)
        if a is not None:
            name = f'fm4_{i:x}'
            dds.save(TEXD / f'{name}.color.dds', a[..., :3].astype('u1'), blocks=tex.blocks(i) if t['fmt'] == 0x52 else None)
            al = a[..., 3].astype('u1'); cut = float((al < 128).mean())
            if cut > 0.002: dds.save(TEXD / f'{name}_o.data.dds', al)
            info = dict(name=name, cut=cut, rgb=a[..., :3].reshape(-1, 3).mean(0))
    TINFO[i] = info
    return info


# ---- materials
MATS = {}


def flat_material(ps):
    c = [round(float(x), 3) for x in (ps[0][:3] if len(ps) else (.5, .5, .5))]
    key = 'fm4_clr_' + '_'.join(f'{int(x * 1000)}' for x in c)
    if key not in MATS:
        MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'materialTag0': 'beamng',
                     'Stages': [{'baseColorFactor': c + [1], 'roughnessFactor': .85, 'metallicFactor': 0}, {}, {}, {}]}
    return key


def material(shader, slots, rec, tt, lm=None):
    """-> material name for a section, from its shader's diffuse sampler and the mesh record's texture list. lm: its lightmap's texture id"""
    smp = SAMPLERS.get(shader, {}); reg = next((smp[s] for s in DIFFUSE if s in smp), None)
    tid = None
    if reg is not None and reg < len(slots) and slots[reg] < len(rec['tex']):
        tid = tt[rec['tex'][slots[reg]]][0]
    t = texture(tid) if tid is not None else None
    ground = next((g for r, g in GROUND if r.match(shader)), None)
    two = '2sd' in shader; alpha = 'opac' in shader and t is not None and t['cut'] > 0.002
    vc = bool(VCLR.search(shader))
    ao = ao_map(lm) if lm is not None else None
    key = (f"fm4_{t['name'][4:]}" if t else 'fm4_grey') + ('_a' if alpha else '') + ('_2' if two else '') + ('_v' if vc else '') + (f'_{ground}' if ground else '') + (f'_lm{lm:x}' if ao else '')
    if key not in MATS:
        st = {'roughnessFactor': .85, 'metallicFactor': 0}
        if t: st['baseColorMap'] = f"{REL}/art/textures/{t['name']}.color.dds"
        else: st['baseColorFactor'] = [.5, .5, .5, 1]
        m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng'}
        if alpha:
            st['opacityMap'] = f"{REL}/art/textures/{t['name']}_o.data.dds"; m['alphaTest'] = True; m['alphaRef'] = 90
        if two or alpha: m['doubleSided'] = True
        if ground: m['groundType'] = ground
        if vc: st['vertColor'] = True
        if ao: st['ambientOcclusionMap'] = ao; st['ambientOcclusionMapUseUV'] = 1
        MATS[key] = m
    return key


# Ground shaders blend layers by the green of a Blend_Value mask (docs/fm4.md, Ground blending). Every UV set goes through the section's
# UV box. Per family: (layer UV set, A's scale, B's scale, mask UV set, mask scale), the scales as (constant row, first component) of c360:
#   road_*         colour = B(world x, z * c0.x) * mask.g(texcoord2 * c0.yz)        no A: B is layer 0, then a black layer at 1 - mask.g
#   terr_*         colour = lerp(A(tc0 * c1.xy) * c0.rgb, B(tc0 * c2.xy), mask.g(tc2))
#   shldr_blnd_*   colour = lerp(A(tc1 * c0.xy), B(tc1 * c1.xy), mask.g(tc0)) * vertex colour * c0.z
#   shldr_blnd_*decal*: A at tc1 * c0.yz, vertex colour * c0.x, and a third layer C (tc2 * c2.xy) lerped into B by vertex alpha, left out for now
def ground_plan(shader, slots, rec, ps, tt):
    """-> dict(layers=[(texture id, uv scale)], uv0=(set, scale), mask=(texture id, set, scale), road, tint, vcolor) or None"""
    smp = SAMPLERS.get(shader, {})

    def tid(name):
        r = smp.get(name)
        if r is None or r >= len(slots) or slots[r] >= len(rec['tex']): return None
        i = tt[rec['tex'][slots[r]]][0]
        return i if tex.bix(i) else None
    c = lambda row, k, n=2: np.array(ps[row][k:k + n]) if row < len(ps) else np.ones(n)
    A, Bt, V = tid('Blend_ASampler'), tid('Blend_BSampler'), tid('Blend_ValueSampler')
    if shader.startswith('road_blnd') and Bt is not None:
        # the _4 variants (Tsukuba, Indianapolis, Infineon) scale B by c0.x and the mask by c0.yz; road_blnd_2 and road_blnd_trilin_2
        # (17 tracks) by c1.x and c1.yz, their c0 the colour weight, 0 (docs/fm4.md, road_blnd)
        r = 1 if shader.endswith('_2') else 0
        return dict(layers=[(Bt, np.ones(2))], uv0=('world', np.full(2, c(r, 0, 1)[0])), mask=(V, 'texcoord2', c(r, 1)), road=True, tint=None, vcolor=False)
    if shader.startswith('terr_blnd') and A is not None and Bt is not None:
        return dict(layers=[(A, c(1, 0)), (Bt, c(2, 0))], uv0=('texcoord0', c(1, 0)), mask=(V, 'texcoord2', np.ones(2)), road=False,
                    tint=c(0, 0, 3), vcolor=False)
    if shader.startswith('shldr_blnd') and A is not None and Bt is not None:
        sA = c(0, 1) if 'decal' in shader else c(0, 0)
        return dict(layers=[(A, sA), (Bt, c(1, 0))], uv0=('texcoord1', sA), mask=(V, 'texcoord0', np.ones(2)), road=False, tint=None,
                    vcolor=float(c(0, 0 if 'decal' in shader else 2, 1)[0]))
    # Families whose colour sampler has another name, read from their ucode (docs/fm4.md, Other colour samplers). One layer each, for now
    def one(t, uv, tint=None, vcolor=False, opacity=None):
        """one layer at uv0 = (UV set or 'world', scale); opacity = (texture id, UV set, scale): its alpha, on UV1"""
        return dict(layers=[(t, np.ones(2))], uv0=uv, mask=(None, opacity[1], opacity[2]) if opacity else (None, 'texcoord0', np.ones(2)),
                    road=False, tint=tint, vcolor=vcolor, opacity=opacity[0] if opacity else None)
    # diff_spec_deposit_4 / _uv2_4: lerp(Base(tc0 * c0.yz), Deposit(D * c1.xy), w) * vertex colour * c0.x, D = tc1 for _uv2, else tc0;
    # w = 1 / (1 + 2^(-c0.w * (vertex alpha - height.g(tc0 * c2.xy)))). Built by deposit_parts, cut into bands of vertex alpha
    if shader.startswith('diff_spec_deposit') and (t := tid('BaseTextureSampler')) is not None:
        d, h = tid('DepositTextureSampler'), tid('heightMapSampler')
        if d is not None and h is not None:
            return dict(deposit=True, layers=[(t, c(0, 1))], dep=d, height=h, sdep=c(1, 0), sh=c(2, 0), k=float(c(0, 3, 1)[0]),
                        uv0=('texcoord0', c(2, 0)), mask=(None, 'texcoord1' if '_uv2' in shader else 'texcoord0', c(1, 0)), road=False,
                        tint=None, vcolor=float(c(0, 0, 1)[0]))
    if 'deposit' in shader and (t := tid('BaseTextureSampler')) is not None:
        return one(t, ('texcoord0', c(0, 1)), vcolor=float(c(0, 0, 1)[0]))       # the shoulder deposits: Deposit not yet
    if shader.startswith('shldr_4clr_blnd_mask') and (t := tid('Diff_SpecSampler')) is not None:
        # lerp(D * c0, D + c4, Blend_Value.rgb), D = sat(Diff + paint mask * c2): c0 and c4 are 0 on every Alps material, so D * Blend_Value.
        # The paint (c2 where the mask is set) and the Blend_Value multiply aren't reproduced yet
        return one(t, ('world', np.full(2, c(1, 0, 1)[0])))
    if shader.startswith('rdedg_blnd_diff_') and 'bump' in shader and (t := tid('ShoulderSampler')) is not None:
        return one(t, ('texcoord0', c(0, 0)))                                     # the road layer (world x, z * c0.z) and its blend not yet
    if shader.startswith('sign_spec_refl_norm') and (t := tid('DiffOpacSampler')) is not None:
        return one(t, ('texcoord0', c(1, 0)))
    # road_blnd_diff_* without Blend_B (Crown Jewel, Fujimi Kaido, Top Gear): Road at world x, z * c2.x, (Road + c1.rgb) then mixed with a
    # term weighted by c0 (0 or 0.012); the small c1 add, the bump blend and the skids aren't reproduced yet
    if shader.startswith('road_blnd_diff') and (t := tid('RoadSampler')) is not None:
        return one(t, ('world', np.full(2, c(2, 0, 1)[0])))
    # rdedg_blnd_diff_opac_3: an overlay strip; DetailGrain at world x, z * c1.x, its alpha the Opacity texture's at texcoord0 * c3.xy
    if shader.startswith('rdedg_blnd_diff_opac') and (t := tid('Diff_Texture_DetailGrainSampler')) is not None and (o := tid('OpacitySampler')) is not None:
        return one(t, ('world', np.full(2, c(1, 0, 1)[0])), opacity=(o, 'texcoord0', c(3, 0)))
    # road_2blnd_uv3_3: Color at texcoord2 * c1.yz, a detail grain at world x, z * c1.x and Blend_Value (texcoord2 * c3.xy) not yet
    if shader.startswith('road_2blnd_uv3') and (t := tid('Diff_Texture_ColorSampler')) is not None:
        return one(t, ('texcoord2', c(1, 1)))
    # *_ovly*: Photoshop overlay of a second texture on this base (2 B O where B < 1/2, else 1 - 2 (1 - B)(1 - O)), the overlay not yet
    if shader.startswith('road_diff_spec_ovly_detailscale') and (t := tid('Diff_Spec02Sampler')) is not None:
        return one(t, ('texcoord2', c(0, 1)))
    if shader.startswith('road_diff_spec_ovly') and (t := tid('Diff_Spec02Sampler')) is not None:
        return one(t, ('texcoord2', c(1, 0)))
    if shader.startswith('shldr_diff_spec_ovly_vendor') and (t := tid('Diff_specSampler')) is not None:
        return one(t, ('world', np.full(2, c(0, 2, 1)[0])))
    return None


def ground_material(plan, ground):
    """the BeamNG material for a ground_plan: layer 0 on UV0, layer 1 masked by mask.g over UV1 (a detail map when its scale differs
    from layer 0's), roads a black layer at 1 - mask.g"""
    (t0, s0) = plan['layers'][0]; mt = plan['mask'][0]
    key = f'fm4_g{t0:x}_' + '_'.join(f'{t:x}x{s[0]:g}' for t, s in plan['layers'][1:]) + (f'_m{mt:x}' if mt is not None else '') + \
        ('_r' if plan['road'] else '') + ('_v' if plan['vcolor'] else '') + (f'_{ground}' if ground else '') + \
        ('_t' + '_'.join(f'{int(round(float(v) * 1000))}' for v in plan['tint']) if plan['tint'] is not None else '') + \
        (f"_o{plan['opacity']:x}" if plan.get('opacity') is not None else '')
    if key in MATS: return key
    if texture(t0) is None: return None
    st = [{'baseColorMap': f"{REL}/art/textures/{texture(t0)['name']}.color.dds", 'roughnessFactor': .9, 'metallicFactor': 0}]
    if plan['tint'] is not None: st[0]['baseColorFactor'] = [round(float(v), 3) for v in plan['tint']] + [1]
    g = None
    if mt is not None:
        a = decode(mt); g = a[..., 1] / 255 if a is not None else None
    for t, s in plan['layers'][1:]:
        if g is None or texture(t) is None: break
        d = s / s0
        l = {'roughnessFactor': .9, 'metallicFactor': 0, 'opacityMap': data_map(f'fm4_m{mt:x}', g), 'opacityMapUseUV': 1}
        if np.allclose(d, 1): l['baseColorMap'] = f"{REL}/art/textures/{texture(t)['name']}.color.dds"
        else: l.update({'detailMap': half_color(t), 'detailBaseColorMapStrength': 1, 'detailScale': [round(float(v), 4) for v in d]})
        st.append(l)
    if plan['road'] and g is not None:
        st.append({'baseColorFactor': [0, 0, 0, 1], 'roughnessFactor': .9, 'metallicFactor': 0, 'opacityMap': data_map(f'fm4_w{mt:x}', 1 - g),
                   'opacityMapUseUV': 1})
    if plan['vcolor']:
        for l in st: l['vertColor'] = True
    blend = {'translucentBlendOp': 'None'}
    if plan.get('opacity') is not None:        # a strip laid over another surface: its alpha, on UV1
        a = decode(plan['opacity'])
        if a is not None:
            st[0].update(opacityMap=data_map(f"fm4_{plan['opacity']:x}_a", a[..., 3] / 255), opacityMapUseUV=1)
            blend = {'translucent': True, 'translucentBlendOp': 'LerpAlpha', 'translucentZWrite': False}
    n = len(st)
    MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st + [{}] * (4 - n), 'activeLayers': n,
                 'materialTag0': 'beamng', **blend, **({'groundType': ground} if ground else {})}
    stats['ground materials'] += 1
    return key


# The deposit weight needs the vertex alpha per pixel, and a BeamNG layer's opacity comes only from textures (shaders/common/material/
# shadergen/defaultMat.hlsl). So deposit sections are cut along lines of equal vertex alpha into bands, each band drawn with one
# representative alpha a: its deposit layer's opacity map is w = 1 / (1 + 2^(-c0.w (a - height.g))), exact for that a. Representatives
# j / (BANDS - 1), cuts halfway between: alpha 0 and 1 (about half the Alps' vertices) come out exact, others within 1 / (2 (BANDS - 1))
BANDS = 8


def deposit_material(plan, b, ground):
    """the BeamNG material for a deposit plan's band b: UV0 = tc0 * c2.xy (the height map's), UV1 = the deposit's UV * c1.xy.
    Layer 0 the base (a detail map when its scale isn't the height map's), layer 1 the deposit, its opacity the band's weight"""
    (t0, sb) = plan['layers'][0]; r = sb / plan['sh']
    key = f"fm4_dep{t0:x}_{plan['dep']:x}_{plan['height']:x}_k{plan['k']:g}_s{r[0]:g}x{r[1]:g}_b{b}" + (f'_{ground}' if ground else '')
    if key in MATS: return key
    if texture(t0) is None or texture(plan['dep']) is None: return None
    hm = decode(plan['height'])
    if hm is None: return None
    a = b / (BANDS - 1); w = 1 / (1 + np.exp2(-plan['k'] * (a - hm[..., 1] / 255)))
    st = [{'roughnessFactor': .9, 'metallicFactor': 0}]
    if np.allclose(r, 1): st[0]['baseColorMap'] = f"{REL}/art/textures/{texture(t0)['name']}.color.dds"
    else: st[0].update(detailMap=half_color(t0), detailBaseColorMapStrength=1, detailScale=[round(float(v), 4) for v in r])
    if w.max() > .5 / 255:          # a band with no deposit anywhere keeps the base alone
        st.append({'baseColorMap': f"{REL}/art/textures/{texture(plan['dep'])['name']}.color.dds", 'diffuseMapUseUV': 1,
                   'roughnessFactor': .9, 'metallicFactor': 0,
                   'opacityMap': data_map(f"fm4_dep{plan['height']:x}_k{plan['k']:g}_b{b}", w)})
    if plan['vcolor']:
        for l in st: l['vertColor'] = True
    n = len(st)
    MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st + [{}] * (4 - n), 'activeLayers': n,
                 'materialTag0': 'beamng', 'translucentBlendOp': 'None', **({'groundType': ground} if ground else {})}
    stats['deposit materials'] += 1
    return key


def _clip(poly, v):
    """the part of a polygon (list of vertex rows, the last column alpha) where alpha >= v"""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]; fp, fq = p[-1] - v, q[-1] - v
        if fp >= 0: out.append(p)
        if (fp >= 0) != (fq >= 0): out.append(p + (q - p) * (fp / (fp - fq)))
    return out


def deposit_parts(m, lay, s, used, tri, remap, pos, nrm, plan, ground):
    """a deposit section cut into alpha bands -> [(material, pos, nrm, uv, idx, uv1)] or None"""
    cb = fm4mesh.attr(m, lay, 'color0')[used].astype('f8') / 255
    V = np.c_[pos[used], nrm[used], box_uv(m, lay, s, 'texcoord0')[used] * plan['sh'], box_uv(m, lay, s, plan['mask'][1])[used] * plan['sdep'],
              cb[:, 1:4].mean(1) * plan['vcolor'], cb[:, 0]]         # 0-2 position, 3-5 normal, 6-7 UV0, 8-9 UV1, 10 grey, 11 alpha
    T = remap[tri]; cut = (np.arange(BANDS - 1) + .5) / (BANDS - 1)
    band = np.searchsorted(cut, V[:, 11], side='right')                # each vertex's band
    tb = band[T]; whole = tb.min(1) == tb.max(1)
    rows = {b: [] for b in range(BANDS)}; tris = {b: [] for b in range(BANDS)}
    for b in range(BANDS):          # triangles inside one band keep their vertices
        sel = T[whole & (tb[:, 0] == b)]
        if len(sel): rows[b].append(V); tris[b].append(sel)
    for t in T[~whole]:             # the rest are clipped to each band they cross
        poly0 = [V[i] for i in t]
        for b in range(band[t].min(), band[t].max() + 1):
            poly = _clip(poly0, cut[b - 1]) if b > 0 else poly0
            if b < BANDS - 1 and poly:      # alpha <= cut[b]: the same clip on negated alpha
                flip = lambda ps: [np.r_[p[:-1], -p[-1]] for p in ps]
                poly = flip(_clip(flip(poly), -cut[b]))
            if len(poly) < 3: continue
            P = np.array(poly); base = sum(len(r) for r in rows[b])
            rows[b].append(P); tris[b].append(base + np.array([[0, k, k + 1] for k in range(1, len(P) - 1)]))
            stats['deposit triangles cut'] += 1
    out = []
    for b in range(BANDS):
        if not tris[b]: continue
        mat = deposit_material(plan, b, ground)
        if mat is None: return None
        R = np.vstack(rows[b]); I = np.vstack(tris[b])
        keep, I = np.unique(I, return_inverse=True); R = R[keep]; I = I.reshape(-1, 3)      # only the vertices this band uses
        n_ = R[:, 3:6]; n_ = n_ / np.maximum(np.linalg.norm(n_, axis=1, keepdims=True), 1e-9)
        out.append((mat, R[:, 0:3], n_, R[:, 6:8], I.ravel(), R[:, 8:11]))
    return out


def box_uv(m, lay, s, name):
    fl = s['fl']; return np.array(fl[4:6]) + fm4mesh.attr(m, lay, name) * np.array(fl[6:8])


COLFILES = {ri: f for ri, d in RDIRS.items() if ri not in STALE for f in [next(d.glob('*_track_*.col'), None)] if f}
COLFILE = bool(COLFILES)

# ---- shapes
SHAPES = LEVEL / 'art' / 'shapes' / 'fm4'; SHAPES.mkdir(parents=True, exist_ok=True)
SHAPE = {}; COLPTS = {}
COLL = 5        # a level number no detail level uses: groups carrying it go into the collision mesh only (daewrite.write_lod)
stats = collections.Counter()


def shape(g, no):
    """art/shapes/fm4/m<no>[_<g>].dae, mesh file no with table group g's textures -> (path, collides) or None. COLPTS[(g, no)] gets the
    game-frame vertices of its colliding sections that aren't ground (road, shoulder, terrain, verge), for the layout test below."""
    if (g, no) in SHAPE: return SHAPE[(g, no)]
    b, p, shs, mats = mesh_file(no); tt = TG[g]['textures']; mr = TG[g]['meshes']; rec = mr[no] if no < len(mr) else {'tex': ()}
    out = []; cpts = []
    for m in p['meshes']:
        if NOUSE.search(m['name']): continue
        lay = fm4mesh.mesh_layout(m, shs, FETCH)
        if lay is None: stats['meshes without layout'] += 1; continue
        pos = fm4mesh.attr(m, lay, 'position0') @ B.T
        if 'normal0' in lay: nrm = fm4mesh.attr(m, lay, 'normal0') @ B.T
        else: nrm = np.tile([0., 0., 1.], (len(pos), 1)); stats['meshes without normals (up)'] += 1
        uvn = next((k for k in ('texcoord0', 'texcoord2', 'texcoord1') if k in lay), None)
        for s in m['secs']:
            mi = s['ints'][2]
            if mi >= len(mats): stats['sections without material'] += 1; continue
            shi, slots = mats[mi][0], mats[mi][1]; shader = shs[shi] if shi < len(shs) else ''
            if NODRAW.match(shader): continue
            tri = fm4rmb.tris(s['ib'], m['nv'], s['ints'][1])
            if not len(tri): continue
            used = np.unique(tri); remap = np.full(m['nv'], -1); remap[used] = np.arange(len(used))
            uv = box_uv(m, lay, s, uvn)[used] if uvn else np.zeros((len(used), 2)); uv1 = None
            ground = next((g for r, g in GROUND if r.match(shader)), None)
            plan = ground_plan(shader, slots, rec, mats[mi][2], tt); mat = None; parts = None
            if plan and plan.get('deposit'):
                if all(k in lay for k in ('texcoord0', plan['mask'][1], 'color0')):
                    parts = deposit_parts(m, lay, s, used, tri, remap, pos, nrm, plan, ground)
                if parts: mat = parts[0][0]; plan = None; stats['deposit sections'] += 1
                else:       # the base alone, as before
                    plan = {**plan, 'deposit': False, 'uv0': ('texcoord0', plan['layers'][0][1]), 'mask': (None, 'texcoord0', np.ones(2))}
            # UV1 carries the mask or the opacity; a plan with neither needs no UV set for it
            uses1 = plan is not None and (plan['mask'][0] is not None or plan.get('opacity') is not None)
            if plan and all(k in lay for k in (plan['uv0'][0], plan['mask'][1] if uses1 else 'world') if k != 'world'):
                mat = ground_material(plan, ground)
                if mat:
                    st_, sc_ = plan['uv0']
                    uv = (fm4mesh.attr(m, lay, 'position0')[used][:, [0, 2]] if st_ == 'world' else box_uv(m, lay, s, st_)[used]) * sc_
                    uv1 = box_uv(m, lay, s, plan['mask'][1])[used] * plan['mask'][2] if uses1 else np.zeros((len(used), 2))
                    if plan['vcolor'] and 'color0' in lay:
                        cb = fm4mesh.attr(m, lay, 'color0')[used].astype('f8')
                        uv1 = np.c_[uv1, cb[:, 1:4].mean(1) / 255 * plan['vcolor']]
                    stats['ground sections'] += 1
            if mat is None and shader.startswith('clr_'):
                mat = flat_material(mats[mi][2])
            if mat is None:
                lm = lightmap(shader, slots, rec, tt) if 'texcoord2' in lay else None
                mat = material(shader, slots, rec, tt, lm[0] if lm else None)
                if lm and LMAO.get(lm[0]):
                    su, sv, ou, ov = lm[1]; uv1 = box_uv(m, lay, s, 'texcoord2')[used] * [su, sv] + [ou, ov]; stats['lightmapped sections'] += 1
                if VCLR.search(shader) and 'color0' in lay:
                    cb = fm4mesh.attr(m, lay, 'color0')[used].astype('f8')
                    uv1 = np.c_[np.zeros((len(used), 2)), cb[:, 1:4].mean(1) / 255]; stats['vertex-coloured sections'] += 1
            collide = not (NOCOLLIDE.match(shader) or NOCOLLIDE_NAME.search(m['name']) or 'opac' in shader and 'barr' not in shader
                           or COLFILE and COLFAMILY.match(shader))
            for mat_, *geo in parts or [(mat, pos[used], nrm[used], uv, remap[tri].ravel(), uv1)]:
                out.append(((mat_, (0, COLL) if collide else (0,)), *geo))
            if collide and not ground: cpts.append(fm4mesh.attr(m, lay, 'position0')[used])
            stats['sections'] += 1; stats['triangles'] += len(tri)
    COLPTS[(g, no)] = np.vstack(cpts) if cpts else np.zeros((0, 3))
    if not out:
        SHAPE[(g, no)] = None; return None
    nm = f'm{no}' + (f'_{g}' if g else '')
    col = write_lod(SHAPES / f'{nm}.dae', out, np.zeros(3), (1,), COLL)
    SHAPE[(g, no)] = (f'{REL}/art/shapes/fm4/{nm}.dae', col)
    return SHAPE[(g, no)]


# ---- layouts' roads. Each layout's AI line is Media/AI/Tracks/<track>_<ribbon>[_R].geo (reverse layouts' run backwards), and its road is
# the strip between chi -1 and 1 of fNormal, over the stretch from StartLine to FinishLine (lap fractions: a stage uses part of the full line)
ROAD_BELOW, ROAD_ABOVE = 1.0, 2.0       # metres below and above the AI line that count as on its road: barriers, not bridges overhead
WALL_NZ = 0.5                           # a .col triangle steeper than 60 degrees is a wall, not a surface


def ai_file(ri):
    return next((p for p in sorted((MEDIA / 'AI' / 'Tracks').glob('*.geo')) if re.fullmatch(rf'{TRACK}_{ri:02d}(_R)?\.geo', p.name, re.I)), None)


ROADS = {}
for r_ in ROWS:
    f_ = ai_file(r_['RibbonIndex'])
    if f_ is None: continue
    W_, N_, _ = fm4ai.line(f_); s_ = np.r_[0, np.cumsum(np.linalg.norm(np.diff(W_, axis=0), axis=1))]; frac = s_ / s_[-1]
    a_, b_ = r_['StartLine'] or 0, r_['FinishLine'] if r_['FinishLine'] is not None else 1
    span = (frac >= a_) & (frac <= b_) if a_ <= b_ else (frac >= a_) | (frac <= b_)
    ROADS[r_['RibbonIndex']] = dict(W=W_, N=N_, frac=frac, tree=cKDTree(W_[span, :2]), idx=np.flatnonzero(span), row=r_)


def on_road(pts, ri):
    """which BeamNG points lie on layout ri's road"""
    R = ROADS[ri]; d, k = R['tree'].query(pts[:, :2]); i = R['idx'][k]
    n = R['N'][i, :2]; nn = (n * n).sum(1); chi = ((pts[:, :2] - R['W'][i, :2]) * n).sum(1) / nn
    dz = pts[:, 2] - R['W'][i, 2]
    return (np.abs(chi) < 1) & (dz > -ROAD_BELOW) & (dz < ROAD_ABOVE) & (d < np.sqrt(nn) + 2)


# A draw that some layouts don't place is left out when it stands on one of those layouts' roads: it closes that layout off (Tsukuba's
# full-circuit barriers across the short circuit's link, and the jersey barrier the short circuit puts across the full one). Ground pieces
# never block. Every layout stays drivable, the way free roam needs it.
items = []; blocked = collections.Counter()
for k, d in enumerate(DRAWS):
    sh = shape(d['g'], d['mesh'])
    if sh is None: continue
    if len(d['ribs']) < len(PVS) and len(COLPTS[(d['g'], d['mesh'])]):
        w = (COLPTS[(d['g'], d['mesh'])] @ d['rot'] + d['pos'])[:, [0, 2, 1]]
        on = [j for j in PVS if j not in d['ribs'] and j in ROADS and on_road(w, j).any()]
        if on:
            blocked[f"{mesh_file(d['mesh'])[1]['meshes'][0]['name']} (layouts {sorted(d['ribs'])}, on {on})"] += 1; continue
    M = P @ d['rot'].T @ B.T; sc = np.linalg.norm(M, axis=0); Rn = M / sc
    pos = P @ d['pos']; ct = 'Collision Mesh' if sh[1] else 'None'
    items.append({'class': 'TSStatic', 'name': f'fm4_d{k}', '__parent': 'fm4', 'shapeName': sh[0], 'position': [float(x) for x in pos],
                  'rotationMatrix': [float(x) for x in Rn.T.ravel()], 'scale': [float(x) for x in sc], 'collisionType': ct, 'decalType': ct})
lap(f'{sum(blocked.values())} draws left out for standing on another layout\'s road' + ''.join(f'\n    {n} x{c}' for n, c in blocked.most_common()))

# ---- FM4's own collision: every layout's .col, triangles that several share kept once, a layout's walls left out where they stand on
# another layout's road (as draws above). Written in 100 m tiles, each surface a material carrying its BeamNG ground type
COLCELL = 100.0
if COLFILES:
    import xml.etree.ElementTree as ET
    names = [e.tag for e in ET.parse(MEDIA / 'physics' / 'surfaceTypes.xml').getroot().find('SurfaceTypes')]
    def colground(sid):
        nm = names[sid] if sid < len(names) else ''
        return nm, next(g for r, g in COLGROUND if re.search(r, nm))
    TRI = {}; ncol = collections.Counter()
    for ri, cf in COLFILES.items():
        for Pc, Tc, Sc, Nc in fm4col.cells(cf):
            pb = Pc[:, [0, 2, 1]]; nb = Nc[:, [0, 2, 1]]          # draw frame -> BeamNG, a reflection: flip each triangle to keep it facing its normal
            cr = np.cross(pb[Tc[:, 1]] - pb[Tc[:, 0]], pb[Tc[:, 2]] - pb[Tc[:, 0]]); Tc = np.where((np.sum(cr * nb, 1) < 0)[:, None], Tc[:, [0, 2, 1]], Tc)
            tri = pb[Tc]; keys = np.round(tri * 100).astype(np.int64)
            for t, kk, sid, n in zip(tri, keys, Sc.tolist(), nb):
                key = tuple(sorted(map(tuple, kk.tolist())))
                if key not in TRI: TRI[key] = [t, sid, n, set()]
                TRI[key][3].add(ri)
    keep = []; nwall = 0
    for t, sid, n, ribs in TRI.values():
        if len(ribs) < len(COLFILES) and abs(n[2]) < WALL_NZ * np.linalg.norm(n):
            pts = np.vstack([t, t.mean(0)])
            if any(on_road(pts, j).any() for j in COLFILES if j not in ribs and j in ROADS): nwall += 1; continue
        nm, g = colground(sid)
        if g is None and not WALLS: ncol['left out: ' + nm] += 1; continue
        keep.append((t, nm, g)); ncol[nm] += 1
    tiles = collections.defaultdict(lambda: collections.defaultdict(list))
    for t, nm, g in keep:
        c = t.mean(0); tiles[(int(c[0] // COLCELL), int(c[1] // COLCELL))][(nm, g)].append(t)
    for ci, (cell, bysurf) in enumerate(sorted(tiles.items())):
        grp = []
        for (nm, g), ts in sorted(bysurf.items()):
            key = f'fm4col_{nm}'
            if key not in MATS:
                MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [{'baseColorFactor': [.5, .5, .5, 1]}, {}, {}, {}],
                             'materialTag0': 'beamng', **({'groundType': g} if g else {})}
            v = np.asarray(ts).reshape(-1, 3)
            grp.append(((key, (COLL,)), v, np.zeros_like(v), np.zeros((len(v), 2)), np.arange(len(v))))
        write_lod(SHAPES / f'col{ci}.dae', grp, np.zeros(3), (1,), COLL)
        items.append({'class': 'TSStatic', 'name': f'fm4_col{ci}', '__parent': 'fm4', 'shapeName': f'{REL}/art/shapes/fm4/col{ci}.dae',
                      'position': [0, 0, 0], 'collisionType': 'Collision Mesh', 'decalType': 'Collision Mesh'})
    lap(f'collision from {len(COLFILES)} layouts: {len(TRI)} distinct triangles, {nwall} walls on another layout\'s road left out, {len(tiles)} tiles: '
        + ', '.join(f'{k} {v}' for k, v in ncol.most_common()))

lap(f'{len(items)} TSStatics, {sum(1 for v in SHAPE.values() if v)} shapes, {len(MATS)} materials, {sum(1 for v in TINFO.values() if v)} textures; {dict(stats)}')

# ---- spawns: one per layout, on its grid. The AI waypoint at the layout's StartLine (a lap fraction along its own line, by distance), then
# the first grid slot of its DefaultStartGridPositionsId (metres back, metres right)
db = sqlite3.connect(MEDIA / 'db' / 'gamedb.slt')
ENVS = fm4ui.environments(ROWS); ENVNAME = {e['Id']: e['ShortDisplayName'] for e in ENVS}
spawns = []; points = []
PREV = (480, 360)
for r_ in ROWS:
    ri = r_['RibbonIndex']
    if ri not in ROADS: lap(f"layout {ri} {r_['name']}: no AI line, no spawn"); continue
    W = ROADS[ri]['W']; frac = ROADS[ri]['frac']
    i0 = int(np.argmin(np.abs(frac - (r_['StartLine'] or 0)))) % (len(W) - 1)
    g = db.execute('select MetersBackFromStartLine, MetersRightOfCenterLine, Yaw from StartGridPositions where id=? order by StartIndex',
                   (r_['DefaultStartGridPositionsId'],)).fetchone() or (0, 0, 0)
    fwd = W[i0 + 1] - W[i0]; fwd[2] = 0; fwd /= np.linalg.norm(fwd); right = np.array([fwd[1], -fwd[0], 0])
    back, rt, _ = g; pt = W[i0] - fwd * back + right * rt; pt[2] += .5
    yaw = math.atan2(fwd[0], -fwd[1]); c_, s_ = math.cos(yaw), math.sin(yaw)
    obj = f'spawn_{ri:02d}'
    spawns.append({'class': 'SpawnSphere', 'name': obj, '__parent': 'MissionGroup', 'dataBlock': 'SpawnSphereMarker',
                   'position': [float(x) for x in pt], 'rotationMatrix': [c_, -s_, 0, s_, c_, 0, 0, 0, 1], 'radius': 1})
    label = (f"{ENVNAME[r_['EnvironmentId']]}: " if len(ENVS) > 1 else '') + r_['name']
    points.append({'objectname': obj, 'translationId': label, 'preview': f'{obj}_preview.jpg'})
    try:
        fm4ui.fit(fm4ui.ribbon_art(r_), PREV, pad=(255, 255, 255)).save(LEVEL / f'{obj}_preview.jpg', quality=90)
    except KeyError:
        del points[-1]['preview']
    lap(f"spawn {obj} {label}: {[round(float(x), 1) for x in pt]}, waypoint {i0} of {len(W)}")

# ---- sky: the dome baked into a SkyBox cubemap through FM4's post (fm4sky.py), the sun from TrackSettings' ShadowLightDirection
SKYNO = next(d['mesh'] for d in PV['draws'] if any(x.startswith('sky_') for x in mesh_file(d['mesh'])[2]))
sky_slot = fm4sky.dome(str(WORK), STEM, SKYNO, FETCH)[3]
SKYMATS, skyinfo = fm4sky.bake(str(WORK), STEM, SKYNO, FETCH, TT[MR[SKYNO]['tex'][sky_slot]][0], TS, LEVEL / 'art' / 'skies' / 'fm4_sky', f'{REL}/art/skies/fm4_sky')
(LEVEL / 'art' / 'skies' / 'fm4_sky' / 'main.materials.json').write_text(json.dumps(SKYMATS, indent=1))
# ShadowLightDirection is the light's travel in the draw frame (x east, y up, z north): checked against the sun painted in the dome, at
# azimuth 306 where the light gives 307.5 (FM4 lights from 40.7 degrees up, the painted sun is at 20.5)
sl = np.array([TS['ShadowLightDirection'][k] for k in 'xyz']); sl = -sl / np.linalg.norm(sl)
SUN_AZ = float(np.degrees(np.arctan2(sl[0], sl[2])) % 360); SUN_EL = float(np.degrees(np.arcsin(sl[1])))
lap(f'sky: dome mesh {SKYNO}, key {skyinfo["key"]:.3f}, EV {skyinfo["ev"]:.2f}; sun azimuth {SUN_AZ:.1f}, elevation {SUN_EL:.1f}')

# FM4 switches between its enabled fog settings by camera heading (Angle); BeamNG has one fog, so the level takes the first enabled
fs = next((f for f in fm4sky.fog_settings(MEDIA / 'Tracks' / TRACK / 'TrackSettings.xml') if f['enabled']), None)
FOG = {}
if fs is not None:
    FOG = {'fogDensity': FOG_W * math.log(2), 'fogDensityOffset': fs['near'], 'fogAtmosphereHeight': 0, 'fogColor': [*fs['color'], 1]}
lap(f'fog: {FOG}')

# ---- level files
main = LEVEL / 'main'; mg = main / 'MissionGroup'; (mg / 'fm4').mkdir(parents=True, exist_ok=True)
(main / 'items.level.json').write_text(json.dumps({'name': 'MissionGroup', 'class': 'SimGroup', 'enabled': '1'}) + '\n')
# No TimeOfDay: FM4's tracks have one fixed sun, so ScatterSky keeps the azimuth and elevation set here. shadowDistance is TrackSettings'
# ShadowEnds InGameShadowEnd. The SkyBox draws FM4's sky over ScatterSky's, as FH1's does; the bake is also the reflection cubemap
obj = [{'class': 'LevelInfo', 'name': 'theLevelInfo', '__parent': 'MissionGroup', 'visibleDistance': 6000, 'gravity': -9.81,
        'globalEnviromentMap': 'fm4_sky_cubemap', **FOG},
       {'class': 'ScatterSky', 'name': 'sunsky', '__parent': 'MissionGroup', 'position': [0, 0, 0], 'azimuth': SUN_AZ, 'elevation': SUN_EL,
        'shadowDistance': TS['ShadowEnds']['InGameShadowEnd']},
       {'name': 'fm4_skybox', 'class': 'SkyBox', '__parent': 'MissionGroup', 'material': 'fm4_sky'},
       *spawns, {'name': 'fm4', 'class': 'SimGroup', '__parent': 'MissionGroup'}]
(mg / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in obj))
(mg / 'fm4' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in items))
(LEVEL / 'art' / 'shapes' / 'fm4' / 'main.materials.json').write_text(json.dumps(MATS, indent=1))
# ---- the freeroam menu: FM4's venue name, location and description (gamedb Environments, Environments.str), its photo
# (UI4/Track_Images) as the level's preview, and each layout's map (UI4/Logos/Track_Ribbons_LG) as its spawn's
venue = ' / '.join(e['DisplayName'] for e in ENVS) or TRACK
desc = '\n\n'.join(f"{e['DisplayName']}, {e['Location']}. {e['Description']}" if len(ENVS) > 1 else f"{e['Location']}. {e['Description']}" for e in ENVS)
layouts = ', '.join(p_['translationId'] for p_ in points)
previews = []
for i, e in enumerate(ENVS):
    ph = fm4ui.photo(e)
    if ph is None: continue
    w_ = round(ph.height * 4 / 3); nm = f'{LVL}_preview' + (f'_{i}' if i else '') + '.jpg'
    fm4ui.fit(ph, (w_, ph.height)).save(LEVEL / nm, quality=92); previews.append(nm)
ext = np.vstack([R['W'][:, :2] for R in ROADS.values()]) if ROADS else np.zeros((1, 2))
size = int(np.ceil((np.abs(ext).max() * 2 + 400) / 100) * 100)
(LEVEL / 'info.json').write_text(json.dumps({
    'title': f'{venue} (FM4)',
    'description': f'{desc}\n\nLayouts: {layouts}.\n\nForza Motorsport 4 (2011), converted from your own copy. Not for redistribution.',
    'authors': 'Turn 10 Studios (original); converted locally', 'size': [size, size], 'previews': previews,
    'spawnPoints': points, 'defaultSpawnPointName': points[0]['objectname'] if points else None}, indent=2, ensure_ascii=False), encoding='utf8')
beamng.write_notice(ROOT, f'Forza Motorsport 4, {venue}', 'Turn 10 Studios / Microsoft', LVL)
lap(f'done -> {LEVEL}')
