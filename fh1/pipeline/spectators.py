"""FH1's 3D crowds, standing still: Spectators.zip's people where the track's crowd sets put them, facing where FH1 turns them, sitting where its crowd
class sits. FH1 animates them (cheers, idles); here each holds the pose its model is stored in.
Crowd sets are the track's `crowd*` .pgeo files (version 42, 1,741 of them, about 146k people; see docs/formats.md). Only the festival site's
stand here (SITE: stages, stalls, grandstands, campsites, the garage, area 3). Every other set belongs to an event (race routes, showcases,
multiplayer venues, hubs, outposts, activation zones), which FH1 shows only while it runs, and many stand on roads it closes for it: the festival
race crowds line Carson's streets. Names don't say where a set is (festival_main_crowd_002 is a Carson race). A person within SAME metres of one
already kept is dropped, as sets share spots. One forest item per person, of 38 shapes: 19 models standing and seated, FH1's 4 LODs.
Models (.skinbin, big-endian): u32 pairs (1, value) for a header, then vertices of 36 bytes: u32 1, position and normal as 3 floats each (y up, front
+z, feet at 0), U and V as u16 / 65535, two bone bytes and two weight bytes summing to 255. Then (1, index count) and a triangle list of u16. The
header's (first, count) pairs cut that list into 4 LODs. The vertices already hold the pose, standing or seated. Textures (spectators.xml ties model
to texture) are tiled DXT1 .xds, format byte 0x52.
Run after build3.py and grass.py (both write the forest item list): python spectators.py [fh1_colorado]. Safe to run twice."""
import glob, json, os, re, struct, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
import dds, frame, gamezip, tex
from daewrite import write_lod

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
SEED = 5; SAME = 0.3
HIPS = {'Stand': 0.8, 'Sit': 0.6}     # FH1 places a person by the hips: standing records sit a median 0.80 m over the ground; seated hips are 0.6 m up the model
PX = 1250.0; FARS = (20.0, 50.0, 120.0, 250.0)   # metres each LOD is drawn to, as build3.py sizes details: PX x radius / (distance + radius)
CUTOUT = 250            # a texture with more texels than 1 in CUTOUT under half alpha is cut out
S = np.eye(3)[[0, 2, 1]]   # FH1 x, up, z -> BeamNG x, y, up, as the world. It mirrors, so faces are turned back
NAME = re.compile(rb'\x00(crowd[A-Za-z0-9_\-\[\]]*)\x00')
SITE = re.compile(r'_festivalcrowd_|_festival_crowd_area03')
SIT = {0, 4}            # crowd class index (class id - 1): 001 Sit and 005 LoopingSit use the Sitting skeleton


def crowd_sets():
    """each crowd set: (name, [(x, y, height) BeamNG], yaw in radians, class index). The set: name after 0x5c, then its box again (some files keep
    8 bytes first), a count, 28 bytes, and 12-byte records: x, height, z as u16 / 65535 of the box (.pgeo z sign), a u16 whose high byte is the
    facing in 1/256 turns, clockwise from north, and whose low byte is the crowd class index, then a u16 flag and 2 zero bytes"""
    out = []; bad = 0
    for f in sorted(glob.glob('../x/*.pgeo')):
        b = open(f, 'rb').read()
        m = NAME.search(b, 0x5c, 0x180) if b[:4] == b'OEGP' else None
        if not m:
            continue
        B = np.array(struct.unpack_from('>8f', b, 0x10)); o = (m.end(1) + 1 + 3) & ~3
        while o < min(len(b) - 36, 0x240) and not (np.abs(np.array(struct.unpack_from('>8f', b, o)) - B).max() < 10 and b[o + 12:o + 16] == bytes(4)):
            o += 4
        n = struct.unpack_from('>I', b, o + 32)[0] if o < len(b) - 36 else -1
        if len(b) - (o + 64) != 12 * n:
            bad += 1
            continue
        r = np.frombuffer(b, '>u2', 6 * n, o + 64).reshape(n, 6).astype('i8')
        p = B[:3] + r[:, :3] / 65535 * (B[4:7] - B[:3])
        out.append((m.group(1).decode(), np.c_[p[:, 0] - CX, p[:, 2] + CZ, p[:, 1] + LIFT], (r[:, 3] >> 8) * 2 * np.pi / 256, r[:, 3] & 255))   # positions are the hips (HIPS)
    return out, bad


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


def main():
    from kn5port import beamng
    level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
    if not (level / 'art' / 'forest' / 'managedItemData.json').exists():
        sys.exit(f'{level} has no forest items: run build3.py first')
    sdir = level / 'art' / 'forest' / 'spectators'; sdir.mkdir(parents=True, exist_ok=True); fdir = level / 'forest'; tdir = level / 'art' / 'textures'
    rel = f'/levels/{LVL}/art/forest/spectators'
    for old in list(sdir.glob('*')) + list(fdir.glob('fh1_spectator_*.forest4.json')) + list(tdir.glob('spectator_*.dds')):
        old.unlink()
    sets, bad = crowd_sets(); stats = {'sets': len(sets), 'unreadable sets': bad}
    P = []; A = []; C = []; K = []
    for nm, p, a, c in sets:
        if SITE.search(nm):
            P.append(p); A.append(a); C.append(c); stats['festival sets'] = stats.get('festival sets', 0) + 1
    P = np.concatenate(P); A = np.concatenate(A); C = np.concatenate(C)
    order = np.where((np.abs(P[:, :2]) < HALF - 1).all(1))[0]
    pairs = cKDTree(P[order]).query_pairs(SAME, output_type='ndarray'); dup = np.zeros(len(order), bool); dup[pairs.max(1)] = True
    stats['repeats dropped'] = int(dup.sum()); idx = order[~dup]
    managed = {k: v for k, v in json.loads((level / 'art' / 'forest' / 'managedItemData.json').read_text()).items() if not k.startswith('fh1_spectator_')}
    items = {}; mats = {}
    for model, tx in sorted(models().items()):
        a = xds(tx); base = f'spectator_{tx}'; cut = (a[..., 3] < 128).mean() > 1 / CUTOUT; mat = f'fh1_{base}'
        if not (tdir / f'{base}.color.dds').exists():
            dds.save(tdir / f'{base}.color.dds', a)
            if cut:
                dds.save(tdir / f'{base}_o.data.dds', a[..., 3])
        for pose in ('Stand', 'Sit'):
            Pm, Nm, uv, lods = skin(f'{model}_{pose}'); R_ = float(np.linalg.norm(Pm - (Pm.min(0) + Pm.max(0)) / 2, axis=1).max())
            groups = []
            for k, t in enumerate(lods):
                used, inv = np.unique(t, return_inverse=True)
                groups.append(((mat, (k,)), Pm[used], Nm[used], uv[used], inv.ravel()))
            item = f'fh1_spectator_{model}_{pose}'
            write_lod(sdir / f'{item}.dae', groups, np.zeros(3), tuple(max(int(R_ / (d + R_) * PX), 1) for d in FARS), None)
            managed[item] = {'name': item, 'internalName': item, 'class': 'TSForestItemData', 'annotation': 'NATURE', 'branchAmp': 0, 'detailAmp': 0,
                             'detailFreq': 0, 'mass': 1, 'rigidity': 1, 'shapeFile': f'{rel}/{item}.dae', 'windScale': 0}
            items[(model, pose)] = item
        st = {'baseColorMap': f'/levels/{LVL}/art/textures/{base}.color.dds', 'roughnessFactor': .8, 'metallicFactor': 0}
        if cut:
            st['opacityMap'] = f'/levels/{LVL}/art/textures/{base}_o.data.dds'
        mats[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}], **({'alphaTest': True, 'alphaRef': 100} if cut else {})}
    (sdir / 'main.materials.json').write_text(json.dumps(mats, indent=1))
    names = sorted(models()); pick = np.random.default_rng(SEED).integers(len(names), size=len(P)); rows = {}
    for i in idx:
        pose = 'Sit' if C[i] in SIT else 'Stand'; item = items[(names[pick[i]], pose)]
        c, s_ = np.cos(-A[i]), np.sin(-A[i]); M = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])      # front (+y) turned clockwise from north
        rows.setdefault(item, []).append({'ctxid': 0, 'pos': [round(float(v), 3) for v in P[i] - [0, 0, HIPS[pose]]], 'rotationMatrix': [round(float(v), 5) for v in M.T.ravel()],
                                          'scale': 1, 'type': item})
        stats['seated' if pose == 'Sit' else 'standing'] = stats.get('seated' if pose == 'Sit' else 'standing', 0) + 1
    for item, r_ in rows.items():
        (fdir / f'{item}.forest4.json').write_text(''.join(json.dumps(o) + '\n' for o in r_))
    (level / 'art' / 'forest' / 'managedItemData.json').write_text(json.dumps(managed, indent=2))
    stats['placed'] = len(idx)
    stats['on the festival ground'] = int(((P[idx, 0] > -1614) & (P[idx, 0] < -18) & (P[idx, 1] > -432) & (P[idx, 1] < 651)).sum())
    print(stats)
    if (level / 'art' / 'textures_ungraded').exists():       # grade.py already ran: grade the new textures too. It grades from ungraded copies, so rerunning is safe
        subprocess.run([sys.executable, 'grade.py', LVL], check=True)
    print('done ->', level)


if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath('../..'))
    main()
