"""FH1 roads for BeamNG's minimap, big map and AI: colorado.nav becomes BeamNGWaypoints plus map.json, and the map art becomes a minimap tile.
Run after build3.py, which wipes the level: python navmap.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, os, sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import nav

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0)}[LVL]
RADIUS = 4.0; GAP = 250.0; TILE = 2048
# BeamNG treats 0.9 and up as a main road on the minimap, and traffic avoids low values. Speeds are m/s: about 70, 55, 40 and 25 mph
DRIVABILITY = {'freeway': 1, 'a': 1, 'b': .9, 'dirt': .45, 'shortcut': .2, 'none': .2}
SPEED = {'freeway': 31, 'a': 25, 'b': 18, 'dirt': 11}
Image.MAX_IMAGE_PIXELS = None


def segment(road):
    """FH1's tags as map.json fields. Node order is driving order on one-way roads: opposing lanes were always on the driver's left."""
    t = road['tags']; kind = t.get('road_type')
    out = {'drivability': DRIVABILITY.get(kind, .9), 'oneWay': 'oneway' in t}
    if kind in SPEED:
        out['speedLimit'] = SPEED[kind]
    if kind in ('none', 'shortcut') or 'ai_disabled' in t:
        out['hiddenInNavi'] = True
    if 'traffic_disabled' in t or 'ai_disabled' in t:
        out['gatedRoad'] = True
    return out


def runs_of(p, inside, roads):
    """A road splits where it leaves the level or jumps further than GAP. Indices skip numbers along a normal road, so only their order counts."""
    out = []
    for rid, road in roads.items():
        run = []; nodes = road['nodes']
        for a, b in zip(nodes, nodes[1:]):
            good = inside[a] and inside[b] and a != b and np.linalg.norm(p[a] - p[b]) < GAP
            if good:
                run = run or [a]; run.append(b)
            elif run:
                out.append((rid, run)); run = []
        if run:
            out.append((rid, run))
    return out


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
p, roads = nav.read(); roads = {i: r for i, r in roads.items() if nav.is_road(r)}; p = p - [CX, -CZ, 0]
inside = np.abs(p[:, :2]).max(1) < HALF - 16
runs = runs_of(p, inside, roads); used = sorted({i for _, r in runs for i in r})
(mg / 'fh1_nav').mkdir(exist_ok=True)
(mg / 'fh1_nav' / 'items.level.json').write_text(''.join(json.dumps(
    {'name': f'nav{i}', 'class': 'BeamNGWaypoint', '__parent': 'fh1_nav', 'position': [round(float(v), 3) for v in p[i]], 'scale': [RADIUS] * 3}) + '\n' for i in used))
(level / 'map.json').write_text(json.dumps({'segments': {f'fh1_{k}': {'nodes': [f'nav{i}' for i in r], **segment(roads[rid])} for k, (rid, r) in enumerate(runs)}}))
rows = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
if not any(o.get('name') == 'fh1_nav' for o in rows):
    rows.append({'name': 'fh1_nav', 'class': 'SimGroup', '__parent': 'MissionGroup'})
    items.write_text(''.join(json.dumps(o) + '\n' for o in rows))

# The HUD minimap only draws the graph above. This tile is for UI that still reads info.json, and matches the crop in build3.py
r = json.load(open('../reg.json')); src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB')
X0, X1, Z0, Z1 = CX - HALF, CX + HALF, CZ - HALF, CZ + HALF
box = (X0 / r['sx'] + r['ox'], Z0 / r['sz'] + r['oz'], X1 / r['sx'] + r['ox'], Z1 / r['sz'] + r['oz'])
(level / 'minimap').mkdir(exist_ok=True)
src.transform((TILE, TILE), Image.EXTENT, box, Image.BICUBIC).save(level / 'minimap' / 'fh1_map.png')
info = json.loads((level / 'info.json').read_text())
info['minimap'] = [{'file': 'minimap/fh1_map.png', 'size': [2 * HALF, 2 * HALF], 'offset': [-HALF, HALF]}]
(level / 'info.json').write_text(json.dumps(info, indent=2))
print('done', len(used), 'nodes', len(runs), 'road runs', sum(len(r) - 1 for _, r in runs), 'links ->', level)
