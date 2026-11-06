"""FH1's spectators, standing still: Spectators.zip's people at the soundscape tiles' Crowd points. 615 cover the festival ground, about 11 m apart, with no
route; the rest line race routes (routeID), where FH1 shows them on race day. Here all of them stand all the time. A point is a bare position on the
ground (97% within half a metre of it): no turn, no crowd class, no count. So each is one standing person, model picked by a fixed seed, turned to face
the nearest road point (fh1_nav), as spectators watch the road.
Models (.skinbin, big-endian): u32 pairs (1, value) for a header, then vertices of 36 bytes: u32 1, position and normal as 3 floats each (y up, front
+z), U and V as u16 / 65535, two bone bytes and two weight bytes summing to 255. Then (1, index count) and a triangle list of u16. The header's (first,
count) pairs cut that list into 4 LODs. The vertices already hold the pose (_Stand standing, _Sit seated), so no skeleton or animation is read: the
.anim.bin cheers and idles aren't decoded. Textures (spectators.xml ties model to texture) are tiled DXT1 .xds, format byte 0x52.
Run after build3.py, which wipes the level: python spectators.py [fh1_colorado]. Safe to run twice."""
import glob, json, os, re, struct, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import dds, frame, gamezip, mission, soundscape, tex
from daewrite import write_lod

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
SEED = 5
PX = 1250.0; FARS = (20.0, 50.0, 120.0, 250.0)   # metres each LOD is drawn to, as build3.py sizes details: PX x radius / (distance + radius)
CUTOUT = 250            # a texture with more texels than 1 in CUTOUT under half alpha is cut out
S = np.eye(3)[[0, 2, 1]]   # FH1 x, up, z -> BeamNG x, y, up, as the world. It mirrors, so faces are turned back


def points():
    """each Crowd point once, in BeamNG: (x, y, height, routeID)"""
    out = set()
    for f in glob.glob(soundscape.TILES):
        for m in re.finditer(r'<Crowd routeID="([^"]*)" position.x="([^"]*)" position.y="([^"]*)" position.z="([^"]*)"', open(f, 'rb').read().decode('utf8', 'ignore')):
            out.add((float(m.group(2)) - CX, float(m.group(4)) + CZ, float(m.group(3)) + LIFT, m.group(1)))
    return sorted(out)


def models():
    """{model: texture} from spectators.xml"""
    root = ET.fromstring(gamezip.read('Spectators.zip', 'spectators.xml').decode('utf8', 'ignore').lstrip('﻿'))
    return {m.get('name'): m.find('texture').get('name') for m in root.find('models').iter('model')}


def skin(name):
    """a .skinbin -> positions, normals (BeamNG axes), uv, [triangles per LOD, wound for BeamNG]"""
    b = gamezip.read('Spectators.zip', f'models/{name}.skinbin')
    n = struct.unpack_from('>I', b, 4)[0]; h = struct.unpack_from(f'>{2 * n}I', b, 16)[1::2]
    nv = struct.unpack_from('>I', b, 16 + 8 * n + 8)[0]             # after the LOD pairs, (1, 1) and the vertex count
    o = 16 + 8 * n + 12; V = np.frombuffer(b[o:o + 36 * nv], '>u4').reshape(nv, 9)
    if (V[:, 0] != 1).any():
        raise ValueError(f'{name}: vertex records out of step')
    P = V[:, 1:4].view('>f4').astype('f8') @ S.T; N = V[:, 4:7].view('>f4').astype('f8') @ S.T
    uv = np.frombuffer(V[:, 7].astype('>u4').tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535
    o += 36 * nv; ni = struct.unpack_from('>I', b, o + 4)[0]; I = np.frombuffer(b[o + 8:o + 8 + 2 * ni], '>u2').astype('i8')
    lods = [I[(r >> 16):(r >> 16) + (r & 0xffff)].reshape(-1, 3)[:, ::-1] for r in h]
    if sum(len(t) * 3 for t in lods) != ni or I.max() >= nv:
        raise ValueError(f'{name}: LOD ranges cover {sum(len(t) * 3 for t in lods)} of {ni} indices')
    return P, N, uv, lods


def xds(name):
    """a texture from Spectators.zip/textures, as RGBA: tiled DXT1, the size in the fetch constant at 0x24"""
    b = gamezip.read('Spectators.zip', f'textures/{name}.dds.xds'); size = struct.unpack_from('>I', b, 0x24)[0]
    w = (size & 0x1fff) + 1; h = ((size >> 13) & 0x1fff) + 1; f = {0x52: (0x52, 8), 0x54: (0x54, 16)}[b[0x23]]
    return tex.decode(b[52:52 + ((w + 3) // 4) * ((h + 3) // 4) * f[1]], w, h, f[0])


def roads(mg):
    p = [o['position'] for o in map(json.loads, filter(str.strip, (mg / 'fh1_nav' / 'items.level.json').read_text().splitlines())) if 'position' in o]
    return np.array(p, 'f8')[:, :2]


def nearest(R, q):
    """index of the nearest of points R to each of q"""
    from scipy.spatial import cKDTree
    return cKDTree(R).query(q)[1]


def main():
    from kn5port import beamng
    level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
    mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
    if not items.exists():
        sys.exit(f'{items} is missing: run build3.py first')
    out = level / 'art' / 'shapes' / 'fh1_spectators'; out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*'):
        old.unlink()
    tdir = level / 'art' / 'textures'; rel = f'/levels/{LVL}/art/shapes/fh1_spectators'
    for old in tdir.glob('spectator_*.dds'):
        old.unlink()
    pts = [p for p in points() if abs(p[0]) < HALF and abs(p[1]) < HALF]
    mats = {}; names = []
    for model, tx in sorted(models().items()):
        P, N, uv, lods = skin(f'{model}_Stand')
        a = xds(tx); base = f'spectator_{tx}'; cut = (a[..., 3] < 128).mean() > 1 / CUTOUT
        if not (tdir / f'{base}.color.dds').exists():
            dds.save(tdir / f'{base}.color.dds', a)
            if cut:
                dds.save(tdir / f'{base}_o.data.dds', a[..., 3])
        mat = f'fh1_{base}'
        st = {'baseColorMap': f'/levels/{LVL}/art/textures/{base}.color.dds', 'roughnessFactor': .8, 'metallicFactor': 0}
        if cut:
            st['opacityMap'] = f'/levels/{LVL}/art/textures/{base}_o.data.dds'
        mats[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], **({'alphaTest': True, 'alphaRef': 100} if cut else {})}
        R_ = float(np.linalg.norm(P - (P.min(0) + P.max(0)) / 2, axis=1).max())
        groups = []
        for k, t in enumerate(lods):
            used, inv = np.unique(t, return_inverse=True)
            groups.append(((mat, (k,)), P[used], N[used], uv[used], inv.ravel()))
        write_lod(out / f'{model}.dae', groups, np.zeros(3), tuple(max(int(R_ / (d + R_) * PX), 1) for d in FARS), None)
        names.append(model)
    rng = np.random.default_rng(SEED); pick = rng.integers(len(names), size=len(pts))
    xy = np.array([p[:2] for p in pts]); R = roads(mg); d = R[nearest(R, xy)] - xy
    turn = np.where(np.hypot(*d.T) > 0.1, np.arctan2(-d[:, 0], d[:, 1]), rng.uniform(-np.pi, np.pi, len(pts)))   # front (+y) to the road
    rows = []
    for i, ((x, y, z, route), m, a) in enumerate(zip(pts, pick, turn)):
        c, s_ = np.cos(a), np.sin(a); Rm = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
        rows.append({'class': 'TSStatic', 'name': f'fh1_spectator_{i}', '__parent': 'fh1_spectators', 'shapeName': f'{rel}/{names[m]}.dae',
                     'position': [round(x, 3), round(y, 3), round(z, 3)], 'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()],
                     'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True, **({'internalName': 'route ' + route} if route else {})})
    (out / 'main.materials.json').write_text(json.dumps(mats, indent=1))
    (mg / 'fh1_spectators').mkdir(exist_ok=True)
    (mg / 'fh1_spectators' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))
    mission.add_groups(items, 'fh1_spectators')
    print(len(names), 'models,', len(rows), 'spectators,', sum(1 for p in pts if not p[3]), 'with no route (the festival and a few gatherings),', len(mats), 'materials')
    if (level / 'art' / 'textures_ungraded').exists():       # grade.py already ran: grade the new textures too. It grades from ungraded copies, so rerunning is safe
        subprocess.run([sys.executable, 'grade.py', LVL], check=True)
    print('done ->', level)


if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath('../..'))
    main()
