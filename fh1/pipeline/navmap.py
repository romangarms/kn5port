"""FH1 roads for BeamNG's minimap, big map and AI: colorado.nav becomes BeamNGWaypoints plus map.json, and the map art becomes a minimap tile.
Run after build3.py, which wipes the level: python navmap.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, os, sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0)}[LVL]
NAV = os.environ.get('FH1_DUMP', 'D:/Roms/Xbox 360/Xenia/FH1XeniaFix') + '/media/tracks/colorado/colorado.nav'
RADIUS = 4.0; GAP = 250.0; TILE = 2048
Image.MAX_IMAGE_PIXELS = None


def roads():
    """Nodes in BeamNG axes, then FH1's roads as ordered node lists. Junction nodes sit on several roads.
    Records are 32 bytes from 44: position, 3 unused here, link count, first link slot. The second table holds one (road, index) pair per link slot.
    Nav z is the opposite sign to mesh z, so it already points the way BeamNG y does."""
    b = open(NAV, 'rb').read(); n = int.from_bytes(b[4:8], 'big'); slots = int.from_bytes(b[16:20], 'big')
    r = np.frombuffer(b, '>u4', count=n * 8, offset=44).reshape(n, 8).astype('i8')
    p = np.frombuffer(b, '>f4', count=n * 8, offset=44).reshape(n, 8)[:, :3].astype('f8')
    tables = 44 + n * 32 + int.from_bytes(b[8:12], 'big') * 16 - 4      # the first table ends a word short of its 16-byte records
    owner = np.full(slots, -1)
    for i in range(n):
        owner[r[i, 4]:r[i, 4] + r[i, 3]] = i
    pairs = np.frombuffer(b, '>u4', count=slots * 2, offset=tables + slots * 4).reshape(slots, 2).astype('i8')
    out = {}
    for s in np.lexsort((pairs[:, 1], pairs[:, 0])):
        out.setdefault(int(pairs[s, 0]), []).append((int(pairs[s, 1]), int(owner[s])))
    return np.stack([p[:, 0], p[:, 2], p[:, 1]], 1), out


def runs_of(p, inside, roads):
    """A road splits where it leaves the level or jumps further than GAP. Indices skip numbers along a normal road, so only their order counts."""
    out = []
    for road in roads.values():
        run = []
        for (_, a), (_, b) in zip(road, road[1:]):
            good = inside[a] and inside[b] and a != b and np.linalg.norm(p[a] - p[b]) < GAP
            if good:
                run = run or [a]; run.append(b)
            elif run:
                out.append(run); run = []
        if run:
            out.append(run)
    return out


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
p, roads_ = roads(); p = p - [CX, -CZ, 0]
inside = np.abs(p[:, :2]).max(1) < HALF - 16
runs = runs_of(p, inside, roads_); used = sorted({i for r in runs for i in r})
(mg / 'fh1_nav').mkdir(exist_ok=True)
(mg / 'fh1_nav' / 'items.level.json').write_text(''.join(json.dumps(
    {'name': f'nav{i}', 'class': 'BeamNGWaypoint', '__parent': 'fh1_nav', 'position': [round(float(v), 3) for v in p[i]], 'scale': [RADIUS] * 3}) + '\n' for i in used))
(level / 'map.json').write_text(json.dumps({'segments': {f'fh1_{k}': {'nodes': [f'nav{i}' for i in r], 'drivability': 1} for k, r in enumerate(runs)}}))
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
print('done', len(used), 'nodes', len(runs), 'road runs', sum(len(r) - 1 for r in runs), 'links ->', level)
