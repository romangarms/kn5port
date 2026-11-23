"""Build an FM4 track ribbon as a BeamNG level: one shape per mesh file, one TSStatic per draw, diffuse materials, mesh collision, a spawn on
the grid. First pass: most detailed LOD only, no blend layers, lightmaps or paint palettes. See docs/fm4.md.
python fm4build.py Tsukuba [ribbon] [output mod folder]
Work data (bin.zip extracted) goes in fh1/fm4/<track>/; the level goes to mods/unpacked/fm4_<track>[_<ribbon>]."""
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
import fm4zip
import dds
import tex
import xenos
from daewrite import write_lod
from kn5port import beamng

FM4 = Path(os.environ.get('FM4_DUMP', r'D:\Roms\Xbox 360\Xenia\FM4XeniaFix'))
MEDIA = FM4 / 'Media'
TRACK = sys.argv[1] if len(sys.argv) > 1 else 'Tsukuba'
TRACK = next((d.name for d in (MEDIA / 'Tracks').iterdir() if d.name.lower() == TRACK.lower()), TRACK)     # the build queue's lane gives it in lower case
RIB = int(sys.argv[2]) if len(sys.argv) > 2 else 0
LVL = f'fm4_{TRACK.lower()}' + (f'_{RIB:02d}' if RIB else '')
WORK = HERE / TRACK.lower()
ROOT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(beamng.find_mods()) / 'unpacked' / LVL
LEVEL = ROOT / 'levels' / LVL; REL = f'/levels/{LVL}'
T0 = time.time()

# game mesh frame (x east, y up, z south) -> BeamNG (x, -z, y); a draw's frame has z flipped, so draw space -> BeamNG is (x, z, y)
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); P = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0.]])
NOUSE = re.compile(r'occluder|shadow|^SKY_|proxy|collision|_CAGE|reflect', re.I)
# The sky dome (sky_*) isn't placed: fm4sky.py bakes it, with the backdrop painted into it, into the level's SkyBox
NODRAW = re.compile(r'^(clr_0|sun_contribution|add_anim_camera_flash|flare_|sky_)')
NOCOLLIDE = re.compile(r'^(grass|bush|tree|anim_flag|rdline|flare|sky|chain_diff_opac_2)')
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


def lap(what):
    print(f'[{time.time() - T0:6.1f}s] {what}', flush=True)


# ---- game data
if not (WORK / 'x').exists():
    fm4zip.main(str(MEDIA / 'Tracks' / TRACK / 'bin.zip'), str(WORK))
tex.X = str(WORK / 'x')
RDIR = MEDIA / 'Tracks' / TRACK / f'Ribbon_{RIB:02d}'
PV = fm4pvs.tables(next(RDIR.glob('*.pvs')).read_bytes())
TT = PV['textures']; MR = PV['meshes']
FETCH = fm4mesh.fetch_lists(str(WORK))
SAMPLERS = {}
for f_ in (WORK / 'x').glob('shaders_track_*.fxobj'):
    ps = [s for s in xenos.shaders(f_.read_bytes()) if s['kind'] == 'ps']
    if ps: SAMPLERS[f_.stem[14:].lower()] = {c[0]: c[2] for c in ps[0]['consts'] if c[1] == 3}
STEM = next((WORK / 'x').glob('*out.00000.rmb.bin')).name.split('.')[0]
lap(f'{TRACK} ribbon {RIB}: {len(PV["draws"])} draws, {len(MR)} mesh records, {len(TT)} textures, {len(SAMPLERS)} shaders')


def lod_of(name):
    m = re.search(r'_LOD(\d+)', name); return int(m.group(1)) if m else 0


FILES = {}


def mesh_file(no):
    if no not in FILES:
        b = (WORK / 'x' / f'{STEM}.{no:05d}.rmb.bin').read_bytes(); p = fm4rmb.parse(b)
        FILES[no] = (b, p, fm4rmb.file_shaders(b, p), fm4mesh.materials(b, p))
    return FILES[no]


# keep the most detailed LOD of each object: LODs are separate draws at one spot
groups = collections.defaultdict(list)
for d in PV['draws']:
    _, p, _, _ = mesh_file(d['mesh']); nm = p['meshes'][0]['name']
    groups[(tuple(np.round(d['pos'], 2)), tuple(np.round(d['rot'].ravel(), 2)), re.sub(r'_LOD\d+', '', nm))].append((lod_of(nm), d))
DRAWS = [min(v, key=lambda x: x[0])[1] for v in groups.values()]
lap(f'{len(DRAWS)} draws after keeping LOD0')

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


def material(shader, slots, rec):
    """-> material name for a section, from its shader's diffuse sampler and the mesh record's texture list"""
    smp = SAMPLERS.get(shader, {}); reg = next((smp[s] for s in DIFFUSE if s in smp), None)
    tid = None
    if reg is not None and reg < len(slots) and slots[reg] < len(rec['tex']):
        tid = TT[rec['tex'][slots[reg]]][0]
    t = texture(tid) if tid is not None else None
    ground = next((g for r, g in GROUND if r.match(shader)), None)
    two = '2sd' in shader; alpha = 'opac' in shader and t is not None and t['cut'] > 0.002
    key = (f"fm4_{t['name'][4:]}" if t else 'fm4_grey') + ('_a' if alpha else '') + ('_2' if two else '') + (f'_{ground}' if ground else '')
    if key not in MATS:
        st = {'roughnessFactor': .85, 'metallicFactor': 0}
        if t: st['baseColorMap'] = f"{REL}/art/textures/{t['name']}.color.dds"
        else: st['baseColorFactor'] = [.5, .5, .5, 1]
        m = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], 'materialTag0': 'beamng'}
        if alpha:
            st['opacityMap'] = f"{REL}/art/textures/{t['name']}_o.data.dds"; m['alphaTest'] = True; m['alphaRef'] = 90
        if two or alpha: m['doubleSided'] = True
        if ground: m['groundType'] = ground
        MATS[key] = m
    return key


# Ground shaders blend layers by the green of a Blend_Value mask (docs/fm4.md, Ground blending). Every UV set goes through the section's
# UV box. Per family: (layer UV set, A's scale, B's scale, mask UV set, mask scale), the scales as (constant row, first component) of c360:
#   road_*         colour = B(world x, z * c0.x) * mask.g(texcoord2 * c0.yz)        no A: B is layer 0, then a black layer at 1 - mask.g
#   terr_*         colour = lerp(A(tc0 * c1.xy) * c0.rgb, B(tc0 * c2.xy), mask.g(tc2))
#   shldr_blnd_*   colour = lerp(A(tc1 * c0.xy), B(tc1 * c1.xy), mask.g(tc0)) * vertex colour * c0.z
#   shldr_blnd_*decal*: A at tc1 * c0.yz, vertex colour * c0.x, and a third layer C (tc2 * c2.xy) lerped into B by vertex alpha, left out for now
def ground_plan(shader, slots, rec, ps):
    """-> dict(layers=[(texture id, uv scale)], uv0=(set, scale), mask=(texture id, set, scale), road, tint, vcolor) or None"""
    smp = SAMPLERS.get(shader, {})

    def tid(name):
        r = smp.get(name)
        if r is None or r >= len(slots) or slots[r] >= len(rec['tex']): return None
        i = TT[rec['tex'][slots[r]]][0]
        return i if tex.bix(i) else None
    c = lambda row, k, n=2: np.array(ps[row][k:k + n]) if row < len(ps) else np.ones(n)
    A, Bt, V = tid('Blend_ASampler'), tid('Blend_BSampler'), tid('Blend_ValueSampler')
    if shader.startswith('road_blnd') and Bt is not None:
        return dict(layers=[(Bt, np.ones(2))], uv0=('world', np.full(2, c(0, 0, 1)[0])), mask=(V, 'texcoord2', c(0, 1)), road=True, tint=None, vcolor=False)
    if shader.startswith('terr_blnd') and A is not None and Bt is not None:
        return dict(layers=[(A, c(1, 0)), (Bt, c(2, 0))], uv0=('texcoord0', c(1, 0)), mask=(V, 'texcoord2', np.ones(2)), road=False,
                    tint=c(0, 0, 3), vcolor=False)
    if shader.startswith('shldr_blnd') and A is not None and Bt is not None:
        sA = c(0, 1) if 'decal' in shader else c(0, 0)
        return dict(layers=[(A, sA), (Bt, c(1, 0))], uv0=('texcoord1', sA), mask=(V, 'texcoord0', np.ones(2)), road=False, tint=None,
                    vcolor=float(c(0, 0 if 'decal' in shader else 2, 1)[0]))
    return None


def ground_material(plan, ground):
    """the BeamNG material for a ground_plan: layer 0 on UV0, layer 1 masked by mask.g over UV1 (a detail map when its scale differs
    from layer 0's), roads a black layer at 1 - mask.g"""
    (t0, s0) = plan['layers'][0]; mt = plan['mask'][0]
    key = f'fm4_g{t0:x}_' + '_'.join(f'{t:x}x{s[0]:g}' for t, s in plan['layers'][1:]) + (f'_m{mt:x}' if mt is not None else '') + \
        ('_r' if plan['road'] else '') + ('_v' if plan['vcolor'] else '') + (f'_{ground}' if ground else '')
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
    n = len(st)
    MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': st + [{}] * (4 - n), 'activeLayers': n,
                 'materialTag0': 'beamng', 'translucentBlendOp': 'None', **({'groundType': ground} if ground else {})}
    stats['ground materials'] += 1
    return key


def box_uv(m, lay, s, name):
    fl = s['fl']; return np.array(fl[4:6]) + fm4mesh.attr(m, lay, name) * np.array(fl[6:8])


COLFILE = next(RDIR.glob('*_track_*.col'), None)

# ---- shapes
SHAPES = LEVEL / 'art' / 'shapes' / 'fm4'; SHAPES.mkdir(parents=True, exist_ok=True)
SHAPE = {}
COLL = 5        # a level number no detail level uses: groups carrying it go into the collision mesh only (daewrite.write_lod)
stats = collections.Counter()


def shape(no):
    """art/shapes/fm4/m<no>.dae -> (path, collides) or None"""
    if no in SHAPE: return SHAPE[no]
    b, p, shs, mats = mesh_file(no); rec = MR[no] if no < len(MR) else {'tex': ()}
    out = []
    for m in p['meshes']:
        if NOUSE.search(m['name']): continue
        lay = fm4mesh.mesh_layout(m, shs, FETCH)
        if lay is None or 'normal0' not in lay: stats['meshes without layout or normals'] += 1; continue
        pos = fm4mesh.attr(m, lay, 'position0') @ B.T; nrm = fm4mesh.attr(m, lay, 'normal0') @ B.T
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
            plan = ground_plan(shader, slots, rec, mats[mi][2]); mat = None
            if plan and all(k in lay for k in (plan['uv0'][0], plan['mask'][1]) if k != 'world'):
                mat = ground_material(plan, ground)
                if mat:
                    st_, sc_ = plan['uv0']
                    uv = (fm4mesh.attr(m, lay, 'position0')[used][:, [0, 2]] if st_ == 'world' else box_uv(m, lay, s, st_)[used]) * sc_
                    uv1 = box_uv(m, lay, s, plan['mask'][1])[used] * plan['mask'][2]
                    if plan['vcolor'] and 'color0' in lay:
                        cb = fm4mesh.attr(m, lay, 'color0')[used].astype('f8')
                        uv1 = np.c_[uv1, cb[:, 1:4].mean(1) / 255 * plan['vcolor']]
                    stats['ground sections'] += 1
            if mat is None:
                mat = material(shader, slots, rec)
            collide = not (NOCOLLIDE.match(shader) or NOCOLLIDE_NAME.search(m['name']) or 'opac' in shader and 'barr' not in shader
                           or COLFILE and COLFAMILY.match(shader))
            out.append(((mat, (0, COLL) if collide else (0,)), pos[used], nrm[used], uv, remap[tri].ravel(), uv1))
            stats['sections'] += 1; stats['triangles'] += len(tri)
    if not out:
        SHAPE[no] = None; return None
    path = SHAPES / f'm{no}.dae'
    col = write_lod(path, out, np.zeros(3), (1,), COLL)
    SHAPE[no] = (f'{REL}/art/shapes/fm4/m{no}.dae', col)
    return SHAPE[no]


items = []
for k, d in enumerate(DRAWS):
    sh = shape(d['mesh'])
    if sh is None: continue
    M = P @ d['rot'].T @ B.T; sc = np.linalg.norm(M, axis=0); Rn = M / sc
    pos = P @ d['pos']; ct = 'Collision Mesh' if sh[1] else 'None'
    items.append({'class': 'TSStatic', 'name': f'fm4_d{k}', '__parent': 'fm4', 'shapeName': sh[0], 'position': [float(x) for x in pos],
                  'rotationMatrix': [float(x) for x in Rn.T.ravel()], 'scale': [float(x) for x in sc], 'collisionType': ct, 'decalType': ct})
# ---- FM4's own collision: one collision-only shape per .col cell, each surface a material carrying its BeamNG ground type
if COLFILE:
    import xml.etree.ElementTree as ET
    names = [e.tag for e in ET.parse(MEDIA / 'physics' / 'surfaceTypes.xml').getroot().find('SurfaceTypes')]
    def colground(sid):
        nm = names[sid] if sid < len(names) else ''
        return nm, next(g for r, g in COLGROUND if re.search(r, nm))
    cells = fm4col.cells(COLFILE); ncol = collections.Counter()
    for ci, (Pc, Tc, Sc, Nc) in enumerate(cells):
        pb = Pc[:, [0, 2, 1]]; nb = Nc[:, [0, 2, 1]]          # draw frame -> BeamNG, a reflection: flip each triangle to keep it facing its normal
        cr = np.cross(pb[Tc[:, 1]] - pb[Tc[:, 0]], pb[Tc[:, 2]] - pb[Tc[:, 0]]); Tc = np.where((np.sum(cr * nb, 1) < 0)[:, None], Tc[:, [0, 2, 1]], Tc)
        grp = []
        for sid in sorted(set(Sc.tolist())):
            nm, g = colground(sid)
            if g is None and not WALLS: ncol['left out: ' + nm] += int((Sc == sid).sum()); continue
            key = f'fm4col_{nm}'
            if key not in MATS:
                MATS[key] = {'name': key, 'mapTo': key, 'class': 'Material', 'version': 1.5, 'Stages': [{'baseColorFactor': [.5, .5, .5, 1]}, {}, {}, {}],
                             'materialTag0': 'beamng', **({'groundType': g} if g else {})}
            k = Sc == sid; grp.append(((key, (COLL,)), pb, np.zeros_like(pb), np.zeros((len(pb), 2)), Tc[k].ravel()))
            ncol[nm] += int(k.sum())
        if not grp: continue
        write_lod(SHAPES / f'col{ci}.dae', grp, np.zeros(3), (1,), COLL)
        items.append({'class': 'TSStatic', 'name': f'fm4_col{ci}', '__parent': 'fm4', 'shapeName': f'{REL}/art/shapes/fm4/col{ci}.dae',
                      'position': [0, 0, 0], 'collisionType': 'Collision Mesh', 'decalType': 'Collision Mesh'})
    lap(f'{len(cells)} collision cells from {COLFILE.name}: ' + ', '.join(f'{k} {v}' for k, v in ncol.most_common()))

lap(f'{len(items)} TSStatics, {sum(1 for v in SHAPE.values() if v)} shapes, {len(MATS)} materials, {sum(1 for v in TINFO.values() if v)} textures; {dict(stats)}')

# ---- spawn: AI waypoint at the start line, then the first grid slot (metres back, metres right)
db = sqlite3.connect(MEDIA / 'db' / 'gamedb.slt')
row = db.execute('select id, DefaultStartGridPositionsId, StartLine, DisplayName from Tracks where MediaName=? and RibbonIndex=?', (TRACK, RIB)).fetchone()
grid = db.execute('select MetersBackFromStartLine, MetersRightOfCenterLine, Yaw from StartGridPositions where id=? order by StartIndex', (row[1],)).fetchall()
AI = MEDIA / 'AI' / 'Tracks' / f'{TRACK.lower()}_{RIB:02d}.geo'
W, _, _ = fm4ai.line(AI)
i0 = int(round((row[2] or 0) * len(W))) % len(W)
fwd = W[(i0 + 1) % len(W)] - W[i0]; fwd[2] = 0; fwd /= np.linalg.norm(fwd); right = np.array([fwd[1], -fwd[0], 0])
spawns = []
for g in grid[:1] or [(0, 0, 0)]:
    back, rt, _ = g; pt = W[i0] - fwd * back + right * rt; pt[2] += .5
    yaw = math.atan2(fwd[0], -fwd[1]); c_, s_ = math.cos(yaw), math.sin(yaw)
    spawns.append({'class': 'SpawnSphere', 'name': 'spawn_grid', '__parent': 'MissionGroup', 'dataBlock': 'SpawnSphereMarker',
                   'position': [float(x) for x in pt], 'rotationMatrix': [c_, -s_, 0, s_, c_, 0, 0, 0, 1], 'radius': 1})
lap(f'spawn at {spawns[0]["position"]}, start waypoint {i0} of {len(W)}')

# ---- sky: the dome baked into a SkyBox cubemap through FM4's post (fm4sky.py), the sun from TrackSettings' ShadowLightDirection
TS = fm4sky.settings(MEDIA / 'Tracks' / TRACK / 'TrackSettings.xml')
SKYNO = next(d['mesh'] for d in PV['draws'] if any(x.startswith('sky_') for x in mesh_file(d['mesh'])[2]))
sky_slot = fm4sky.dome(str(WORK), STEM, SKYNO, FETCH)[3]
SKYMATS, skyinfo = fm4sky.bake(str(WORK), STEM, SKYNO, FETCH, TT[MR[SKYNO]['tex'][sky_slot]][0], TS, LEVEL / 'art' / 'skies' / 'fm4_sky', f'{REL}/art/skies/fm4_sky')
(LEVEL / 'art' / 'skies' / 'fm4_sky' / 'main.materials.json').write_text(json.dumps(SKYMATS, indent=1))
# ShadowLightDirection is the light's travel in the draw frame (x east, y up, z north): checked against the sun painted in the dome, at
# azimuth 306 where the light gives 307.5 (FM4 lights from 40.7 degrees up, the painted sun is at 20.5)
sl = np.array([TS['ShadowLightDirection'][k] for k in 'xyz']); sl = -sl / np.linalg.norm(sl)
SUN_AZ = float(np.degrees(np.arctan2(sl[0], sl[2])) % 360); SUN_EL = float(np.degrees(np.arcsin(sl[1])))
lap(f'sky: dome mesh {SKYNO}, key {skyinfo["key"]:.3f}, EV {skyinfo["ev"]:.2f}; sun azimuth {SUN_AZ:.1f}, elevation {SUN_EL:.1f}')

# ---- level files
main = LEVEL / 'main'; mg = main / 'MissionGroup'; (mg / 'fm4').mkdir(parents=True, exist_ok=True)
(main / 'items.level.json').write_text(json.dumps({'name': 'MissionGroup', 'class': 'SimGroup', 'enabled': '1'}) + '\n')
# No TimeOfDay: FM4's tracks have one fixed sun, so ScatterSky keeps the azimuth and elevation set here. shadowDistance is TrackSettings'
# ShadowEnds InGameShadowEnd. The SkyBox draws FM4's sky over ScatterSky's, as FH1's does; the bake is also the reflection cubemap
obj = [{'class': 'LevelInfo', 'name': 'theLevelInfo', '__parent': 'MissionGroup', 'visibleDistance': 6000, 'gravity': -9.81,
        'globalEnviromentMap': 'fm4_sky_cubemap'},
       {'class': 'ScatterSky', 'name': 'sunsky', '__parent': 'MissionGroup', 'position': [0, 0, 0], 'azimuth': SUN_AZ, 'elevation': SUN_EL,
        'shadowDistance': TS['ShadowEnds']['InGameShadowEnd']},
       {'name': 'fm4_skybox', 'class': 'SkyBox', '__parent': 'MissionGroup', 'material': 'fm4_sky'},
       *spawns, {'name': 'fm4', 'class': 'SimGroup', '__parent': 'MissionGroup'}]
(mg / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in obj))
(mg / 'fm4' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in items))
(LEVEL / 'art' / 'shapes' / 'fm4' / 'main.materials.json').write_text(json.dumps(MATS, indent=1))
(LEVEL / 'info.json').write_text(json.dumps({
    'title': f'FM4 {TRACK}' + (f' (layout {RIB})' if RIB else ''),
    'description': 'Personal conversion of a Forza Motorsport 4 (2011) track from your own copy. Not for redistribution.',
    'authors': 'Turn 10 (original); converted locally', 'size': [2000, 2000],
    'spawnPoints': [{'objectname': 'spawn_grid', 'translationId': 'Grid'}], 'defaultSpawnPointName': 'spawn_grid'}, indent=2))
beamng.write_notice(ROOT, f'Forza Motorsport 4, {TRACK}', 'Turn 10 Studios / Microsoft', LVL)
lap(f'done -> {LEVEL}')
