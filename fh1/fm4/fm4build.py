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
import fm4mesh
import fm4pvs
import fm4rmb
import fm4zip
import dds
import tex
import xenos
from daewrite import write_lod
from kn5port import beamng

FM4 = Path(os.environ.get('FM4_DUMP', r'D:\Roms\Xbox 360\Xenia\FM4XeniaFix'))
MEDIA = FM4 / 'Media'
TRACK = sys.argv[1] if len(sys.argv) > 1 else 'Tsukuba'
RIB = int(sys.argv[2]) if len(sys.argv) > 2 else 0
LVL = f'fm4_{TRACK.lower()}' + (f'_{RIB:02d}' if RIB else '')
WORK = HERE / TRACK.lower()
ROOT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(beamng.find_mods()) / 'unpacked' / LVL
LEVEL = ROOT / 'levels' / LVL; REL = f'/levels/{LVL}'
T0 = time.time()

# game mesh frame (x east, y up, z south) -> BeamNG (x, -z, y); a draw's frame has z flipped, so draw space -> BeamNG is (x, z, y)
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); P = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0.]])
NOUSE = re.compile(r'occluder|shadow|^SKY_|proxy|collision|_CAGE|reflect', re.I)
NODRAW = re.compile(r'^(clr_0|sun_contribution|add_anim_camera_flash|flare_|sky_)')
NOCOLLIDE = re.compile(r'^(grass|bush|tree|anim_flag|rdline|flare|sky|chain_diff_opac_2)')
NOCOLLIDE_NAME = re.compile(r'^VEG_|^OBJ_Flag|crowd|spectator|_Tree|Bush|Grass|^RCEDAY_CameraFlash', re.I)
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


def texture(i):
    """write fm4_<id>.color.dds (and _o.data.dds when it has cut-out alpha) -> info or None"""
    if i in TINFO: return TINFO[i]
    t = tex.bix(i); info = None
    if t:
        try:
            a = bc4_decode(i) if t['fmt'] == 0x7b else tex.decode(tex.top_mip(t), t['w'], t['h'], t['fmt'])
        except Exception as e:
            print('texture', hex(i), 'failed:', e); a = None
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
            fl = s['fl']
            uv = (np.array(fl[4:6]) + fm4mesh.attr(m, lay, uvn)[used] * np.array(fl[6:8])) if uvn else np.zeros((len(used), 2))
            mat = material(shader, slots, rec)
            collide = not (NOCOLLIDE.match(shader) or NOCOLLIDE_NAME.search(m['name']) or 'opac' in shader and 'barr' not in shader)
            out.append(((mat, (0, COLL) if collide else (0,)), pos[used], nrm[used], uv, remap[tri].ravel()))
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

# ---- level files
main = LEVEL / 'main'; mg = main / 'MissionGroup'; (mg / 'fm4').mkdir(parents=True, exist_ok=True)
(main / 'items.level.json').write_text(json.dumps({'name': 'MissionGroup', 'class': 'SimGroup', 'enabled': '1'}) + '\n')
obj = [{'class': 'LevelInfo', 'name': 'theLevelInfo', '__parent': 'MissionGroup', 'visibleDistance': 6000, 'gravity': -9.81, 'canvasClearColor': [.5, .6, .7, 1]},
       {'class': 'TimeOfDay', 'name': 'tod', '__parent': 'MissionGroup', 'time': .1, 'play': False, 'azimuthOverride': 0, 'startTime': .1},
       {'class': 'ScatterSky', 'name': 'sunsky', '__parent': 'MissionGroup', 'position': [0, 0, 0], 'shadowDistance': 1600, 'shadowSoftness': .2, 'brightness': 1.0},
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
