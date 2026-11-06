"""FH1's people, standing still: the characters its cutscenes place in Colorado (the diner, the street-race hubs, the festival's wristband stand,
the title-screen vignettes), each held at the first frame of its animation. FH1 shows them only while their cutscene plays; here they stay.
A cutscene's CCutsceneAnimationTrigger names an AnimatedObjects .pgeo (Fest_*, Street_*, Driver_*, Nemesis_*, Barn_find_*...) and where it stands:
x, y, z in the .pgeo z sign and a turn in degrees about up, as the banner plane's (animated.py). Each model's skinned meshes are posed on the CPU
from its bones at time 0, blending each vertex's four weights; rigid meshes (heads, phones, cameras, cups) follow their bone. Models with several
people keep them together, as Granny's InitialPlacement puts them. Spots repeat across cutscenes (the six wristband unlocks share theirs), so one
person stands at each spot. No collision.
Run after build3.py, which wipes the level: python people.py [fh1_colorado]. Safe to run twice. Needs no rebuild."""
import json, os, re, subprocess, sys, xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
import animcopy, dds, frame, gamezip, granny, granny2, mission
from daewrite import write_lod

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
CUTSCENES = ['Colorado/Cutscenes/cutscenes_default_freeroam.xml', 'Colorado/Cutscenes/cutscenes_press_start.xml', 'Colorado/Cutscenes/cutscenes_default_both.xml']
PEOPLE = re.compile(r'(Fest|Street|Driver|exhib|Nemesis|Misc|Barn_find|Host|Cameraman)_', re.I)
SAME_SPOT = 0.75; CLASH = 1.5; SAME_MODEL = 3.0     # metres: see spots
FAR = 200.0; PX = 1250.0  # drawn to FAR metres, as build3.py sizes details: PX x radius / (distance + radius)
CUTOUT = 250            # a texture with more texels than 1 in CUTOUT under half alpha is cut out (hair, lashes)
S = np.eye(4)[[0, 2, 1, 3]]   # Granny x, up, z -> BeamNG x, y, up. It mirrors, so faces are turned back


def triggers():
    """each person trigger: (model, x, y, z, turn, cutscene, event time), in file order"""
    out = []
    for fn in CUTSCENES:
        root = ET.fromstring(gamezip.read('gamemodes.zip', fn).decode('utf8', 'ignore').lstrip('﻿'))
        for cs in root.iter('Cutscene'):
            for ev in cs.iter('Event'):
                for e in ev.iter('EventTrigger'):
                    if e.get('id') == 'CCutsceneAnimationTrigger' and PEOPLE.match(e.get('animname', '')):
                        out.append((e.get('animname'), float(e.get('x')), float(e.get('y')), float(e.get('z')), float(e.get('rotation')), cs.get('name'), float(ev.get('time', 0))))
    return out


def spots(trs):
    """one person per spot, the first in file order. Cutscenes reuse spots: each barn find's Discover cutscene stands Dak at its barn again, a metre
    or two off, and the wristband unlock and intro share a stand. A cutscene also moves one person on with a later trigger, as the diner's driver walks
    in and then greets. So a trigger is dropped where one from another cutscene, or from another moment of the same one, stands within CLASH metres, or
    within SAME_MODEL of the same model. People started together keep their places however close, as they stand in groups"""
    kept = []
    for t in trs:
        def clash(k):
            d = np.hypot(t[1] - k[1], t[3] - k[3])
            if abs(t[2] - k[2]) > 3:
                return False
            if (t[5], t[6]) == (k[5], k[6]):
                return t[0] == k[0] and d < SAME_SPOT
            return d < (SAME_MODEL if t[0] == k[0] else CLASH)
        if not any(clash(k) for k in kept):
            kept.append(t)
    return kept


def pose(model, at=0.0):
    """a model's meshes posed at time `at`, in BeamNG axes around the model's origin: [(mesh, positions, triangles wound for BeamNG)], and the texture names.
    Meshes belong to models in order, as many as each Granny model's MeshBindings. Each skinned vertex is the weighted sum of its four bones' moves
    from the bind pose; a rigid mesh moves with its bone"""
    b = animcopy.standalone(model); ms = animcopy.meshes(b, True); t = granny2.File(b).tree()
    cnt = [len(md.get('MeshBindings') or []) for md in t['Models']]
    if sum(cnt) != len(ms):
        raise ValueError(f'{model}: {len(ms)} meshes, models bind {sum(cnt)}')
    W, B, _ = animcopy.poses(b, np.array([at]))
    out = []
    for o, m in zip(np.repeat(np.arange(len(cnt)), cnt), ms):
        moves = np.array([w[0] @ np.linalg.inv(bb) for w, bb in zip(W[o], B[o])])          # bone -> 4x4, bind pose to this frame
        p = np.c_[m['pos'], np.ones(len(m['pos']))]
        if m['skin']:
            M = np.einsum('vk,vkij->vij', m['w4'], moves[m['b4']]); q = np.einsum('vij,vj->vi', M, p)
        else:
            q = p @ moves[m['bone']].T
        out.append((m, (q @ S.T)[:, :3], m['idx'][:, ::-1]))
    return out, animcopy.texture_names(b)


def texture(name, tdir, saved):
    """art/textures/people_<name>.color.dds, and an opacity map when it's cut out -> (colour path, opacity path or None). Names are matched without case,
    as the models ask for 'male05' where the zip holds Male05"""
    key = name.lower()
    if key not in saved:
        real = TEXNAMES.get(key)
        if real is None:
            saved[key] = None
            return None
        a = animcopy.xds(real); base = 'people_' + re.sub(r'[^A-Za-z0-9_]', '_', real); cut = (a[..., 3] < 128).mean() > 1 / CUTOUT
        dds.save(tdir / f'{base}.color.dds', a)
        if cut:
            dds.save(tdir / f'{base}_o.data.dds', a[..., 3])
        saved[key] = (f'/levels/{LVL}/art/textures/{base}.color.dds', f'/levels/{LVL}/art/textures/{base}_o.data.dds' if cut else None)
    return saved[key]


TEXNAMES = {}


def main():
    from kn5port import beamng
    TEXNAMES.update({n[9:-4].lower(): n[9:-4] for n in gamezip.names('animatedobjects.zip') if n.startswith('Textures/') and n.endswith('.xds')})
    level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
    mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
    if not items.exists():
        sys.exit(f'{items} is missing: run build3.py first')
    out = level / 'art' / 'shapes' / 'fh1_people'; out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*'):
        old.unlink()
    tdir = level / 'art' / 'textures'; rel = f'/levels/{LVL}/art/shapes/fh1_people'
    for old in tdir.glob('people_*.dds'):
        old.unlink()
    trs = triggers(); here = [t for t in spots(trs) if abs(t[1] - CX) < HALF and abs(t[3] + CZ) < HALF]
    print(len(trs), 'person triggers,', len(spots(trs)), 'spots,', len(here), 'in this level')
    mats = {}; saved = {}; shapes = {}; rows = []; tris = 0
    for model in sorted({t[0] for t in here}):
        try:
            meshes, tl = pose(model)
        except Exception as e:
            print(model, 'left out:', type(e).__name__, e)
            continue
        groups = []
        for m, q, idx in meshes:
            n_ = granny.smooth_normals(q, idx)
            for slots, first, count in m['segs']:
                tx = texture(tl[slots[0]], tdir, saved) if 0 <= slots[0] < len(tl) else None
                mat = 'fh1_people_' + (Path(tx[0]).name.split('.')[0][7:] if tx else 'plain')
                if mat not in mats:
                    st = {'baseColorMap': tx[0], 'roughnessFactor': .8, 'metallicFactor': 0} if tx else {'baseColorFactor': [.5, .5, .5, 1], 'roughnessFactor': .8, 'metallicFactor': 0}
                    if tx and tx[1]:
                        st['opacityMap'] = tx[1]
                    mats[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'Stages': [st, {}, {}, {}],
                                 **({'alphaTest': True, 'alphaRef': 100, 'doubleSided': True} if tx and tx[1] else {})}
                t_ = idx[first:first + count]; used, inv = np.unique(t_, return_inverse=True)
                groups.append(((mat, (0,)), q[used], n_[used], m['uv'][used], inv.ravel())); tris += len(t_)
        P = np.concatenate([g[1] for g in groups]); R_ = float(np.linalg.norm(P - (P.min(0) + P.max(0)) / 2, axis=1).max())
        write_lod(out / f'{model}.dae', groups, np.zeros(3), (max(int(R_ / (FAR + R_) * PX), 1),), None)
        shapes[model] = (P.min(0), P.max(0))
    for i, (model, x, y, z, turn, cs, _) in enumerate(here):
        if model not in shapes:
            continue
        c, s_ = np.cos(np.radians(turn)), np.sin(np.radians(turn)); R = np.array([[c, 0, -s_], [0, 1, 0], [s_, 0, c]])
        Rm = S[:3, :3] @ R.T @ S[:3, :3]; pos = np.array([x - CX, z + CZ, y + LIFT])
        rows.append({'class': 'TSStatic', 'name': f'fh1_person_{i}', '__parent': 'fh1_people', 'shapeName': f'{rel}/{model}.dae', 'position': [round(float(v), 3) for v in pos],
                     'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True,
                     'internalName': cs})
    (out / 'main.materials.json').write_text(json.dumps(mats, indent=1))
    (mg / 'fh1_people').mkdir(exist_ok=True)
    (mg / 'fh1_people' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows))
    mission.add_groups(items, 'fh1_people')
    print(len(shapes), 'models,', len(rows), 'placed,', tris, 'triangles in the shapes,', len(mats), 'materials, e.g.',
          [(r['shapeName'].split('/')[-1][:-4], [round(v) for v in r['position']]) for r in rows[:5]])
    if (level / 'art' / 'textures_ungraded').exists():       # grade.py already ran: grade the new textures too. It grades from ungraded copies, so rerunning is safe
        subprocess.run([sys.executable, 'grade.py', LVL], check=True)
    print('done ->', level)


if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath('../..'))
    main()
