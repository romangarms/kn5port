"""The level's face in BeamNG: freeroam menu text and previews, named spawns, location zones, and the data fh1Extras.lua reads.
Run after build3.py, which wipes the level: python levelinfo.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, math, os, re, shutil, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import mission, nav, soundscape

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0), 'fh1_clearsprings': (1700.0, 2000.0, 2048.0)}[LVL]
Image.MAX_IMAGE_PIXELS = None
PREVIEW = (800, 600); PREVIEW_METRES = 2400          # stock menu images are 800x600
# The map art's picture area, inside its black bars
ART_BOX = (377, 0, 4742, 3072)
LANE_OFFSET = 1.8

# name, spawn object, target as (x, nav z): a hub or zone from FH1's data. Spawns snap to the nearest public road.
# Beaumont is the one guess: FH1 calls that zone Brekenridge and no file ties the two names together
SPAWNS = [('Carson', 'spawn_carson', -3954, 879), ('Horizon Festival', 'spawn_festival', -919, -247), ('Gladstone', 'spawn_gladstone', -421, -1388),
          ('Clear Springs', 'spawn_clear_springs', 1028, -3102), ('Kettle Hills', 'spawn_kettle_hills', 2916, -3876), ('The Rockies', 'spawn_rockies', 4616, 1717),
          ('Finley Dam', 'spawn_finley_dam', 6617, 263), ('Beaumont', 'spawn_beaumont', 3491, 3150), ('Montano Plains', 'spawn_montano', -3524, -1868),
          ('Steelworks', 'spawn_steelworks', -1517, 2779), ('Windmill test', 'spawn_windmill_test', -1457, -1612)]
# area_type to FH1's own name, from the matching lists in Environments.str and Map.str. FH1's `lakes` regions are mostly forest, so they aren't named
ZONES = {'festival': 'Horizon Festival', 'maintown': 'Carson', 'redrock': 'Red Rock', 'plains': 'Montano Plains', 'foothills': 'Kettle Hills',
         'mountains': 'The Rockies', 'canyons': 'Canyons', 'reservoir': 'Clear Springs', 'lake': 'Lake'}
AMBIENCE = {'Horizon Festival': 'festival', 'Carson': 'main_town', 'The Rockies': 'mountains', 'Canyons': 'mountains', 'Red Rock': 'red_rock'}
# Placed loops: file, x and nav z from ColoradoRibbon_00.xml and the festival's soundscape tiles, then full-volume radius, silent radius and level.
# Radii and levels are chosen by hand, because the distances in FH1's .fev files aren't decoded
SOUNDS = [('festival_crowd', -950, -208, 120, 650, .8), ('festival_music', -950, -208, 100, 500, .5), ('tanoy', -950, -220, 40, 300, .35),
          ('stage_music_a', -1469, -345, 30, 420, 1), ('stage_crowd', -1434, -383, 20, 260, .7),
          ('stage_music_b', -1398, -22, 30, 420, 1), ('stage_crowd', -1450, -40, 20, 260, .7)]
# Stock tunnel reverb, placed over FH1's tunnel-like reverb zones. A snapshot event plays from any level, as stock levels share the same banks
REVERB_EVENT = 'event:>Snapshot Events>Snapshot_ATT_Tunnel_Closed_Position'; REVERB_GRID = 40.0; REVERB_RADIUS = 34; REVERB_ROAD_METRES = 30
NAMED_ROAD = re.compile(r'(interstate \d+|county route \d+|route \d+|horizon outer loop)')


def art(box):
    """Map art for a world rectangle (x0, z0, x1, z1) in mesh axes."""
    r = json.load(open('../reg.json')); src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB')
    x0, z0, x1, z1 = box
    return src.transform(PREVIEW, Image.EXTENT, (x0 / r['sx'] + r['ox'], z0 / r['sz'] + r['oz'], x1 / r['sx'] + r['ox'], z1 / r['sz'] + r['oz']), Image.BICUBIC)


def spawn_preview(x, z, path):
    w = PREVIEW_METRES; h = w * PREVIEW[1] / PREVIEW[0]
    im = art((x - w / 2, z - h / 2, x + w / 2, z + h / 2)); d = ImageDraw.Draw(im); cx, cy = PREVIEW[0] / 2, PREVIEW[1] / 2
    d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), outline=(255, 90, 30), width=4)
    im.save(path, quality=90)


def level_preview(path):
    src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB').crop(ART_BOX)
    w, h = src.size; want = int(h * PREVIEW[0] / PREVIEW[1]); left = (w - want) // 2
    src.crop((left, 0, left + want, h)).resize(PREVIEW, Image.LANCZOS).save(path, quality=90)


def public_nodes(p, roads):
    """Nodes on real roads that traffic may use, with their driving direction and whether the road is one-way."""
    out = {}
    for r in roads.values():
        t = r['tags']; nodes = r['nodes']
        if not nav.is_road(r) or t.get('road_type') not in ('freeway', 'a', 'b') or 'traffic_disabled' in t or 'ai_disabled' in t:
            continue
        for a, b in zip(nodes, nodes[1:]):
            d = p[b, :2] - p[a, :2]; l = np.hypot(*d)
            if 2 < l < 80:
                out[a] = (d / l, 'oneway' in t)
    return out


def spawn(name, obj, x, y, p, pub):
    ids = np.array(list(pub)); i = ids[((p[ids, 0] - x) ** 2 + (p[ids, 1] - y) ** 2).argmin()]
    (dx, dy), oneway = pub[int(i)]
    pos = p[i] - [CX, -CZ, 0]
    if not oneway:                                         # two-way nodes are on the centre line; drive on the right
        pos = pos + [dy * LANE_OFFSET, -dx * LANE_OFFSET, 0]
    yaw = math.atan2(dx, -dy); c, s = math.cos(yaw), math.sin(yaw)
    return ({'class': 'SpawnSphere', 'name': obj, '__parent': 'MissionGroup', 'dataBlock': 'SpawnSphereMarker', 'position': [float(pos[0]), float(pos[1]), float(pos[2] + .6)],
             'rotationMatrix': [c, -s, 0, s, c, 0, 0, 0, 1], 'radius': 1},
            {'objectname': obj, 'translationId': name, 'preview': f'{obj}_preview.jpg'}, (float(p[i, 0]), float(-p[i, 1])))


def zones(p, roads):
    """FH1's map regions as location zones. BeamNG's zones need closed outlines, which the region rows already are."""
    out = []; seen = {}
    for r in roads.values():
        name = ZONES.get(r['tags'].get('area_type'))
        if name:
            seen[name] = seen.get(name, 0) + 1
            v = p[r['nodes']] - [CX, -CZ, 0]
            out.append({'oldId': len(out) + 1, 'name': name + ' ' * (seen[name] - 1), 'color': [1, 1, 1], 'vertices': [[float(a), float(b), 0.0] for a, b, _ in v],   # spaces keep names unique
                        'top': {'active': False, 'normal': [0, 0, 1], 'pos': [0, 0, 10]}, 'bot': {'active': False, 'normal': [0, 0, -1], 'pos': [0, 0, -10]},
                        'customFields': {'values': {}, 'names': {}, 'tags': {}, 'types': {}}})
    return out


def road_names(roads):
    out = {}
    for r in roads.values():
        m = NAMED_ROAD.match(r['tags'].get('display_name', ''))
        if m and nav.is_road(r):
            for i in r['nodes']:
                out[f'nav{i}'] = m.group(1).title()
    return out


def placed(objects):
    """Placed sounds in the form fh1Extras.lua reads, in BeamNG axes and limited to this level."""
    def points(events):
        p = np.array(sorted(q for e in events for q in objects.get(e, ()))).reshape(-1, 2) - [CX, -CZ]
        return np.round(p[np.abs(p).max(1) < HALF], 1).tolist()
    loops = [{'file': f'art/sound/fh1/{c[0][0]}.ogg', 'near': r[3], 'far': r[4], 'volume': r[5], 'points': points(r[0])}
             for (g, r), c in zip(soundscape.LOOPS.items(), soundscape.clips(soundscape.LOOPS).values())]
    shots = [{'files': [{'file': f'art/sound/fh1/{stem}.ogg', 'seconds': round(s[1] / s[4], 2)} for stem, s, _ in c], 'near': r[3], 'far': r[4], 'volume': r[5], 'wait': [r[6], r[7]], 'points': points(r[0])}
             for (g, r), c in zip(soundscape.ONESHOTS.items(), soundscape.clips(soundscape.ONESHOTS).values())]
    return [l for l in loops if l['points']], [s for s in shots if s['points']]


def reverb(zones, p, roads):
    """Stock snapshot emitters along FH1's tunnel-like reverb zones, at the height of the road through them. Zones with no road nearby have nothing to drive through."""
    nodes = sorted({i for r in roads.values() if nav.is_road(r) for i in r['nodes']})
    tree = cKDTree(p[nodes, :2]); cells = {}
    for templates, tri in zones:
        if soundscape.REVERB_TEMPLATES & set(templates):
            d, near = tree.query(tri, distance_upper_bound=REVERB_ROAD_METRES)
            for (x, y), dist, i in zip(tri, d, near):
                if np.isfinite(dist):
                    cells.setdefault((round(x / REVERB_GRID), round(y / REVERB_GRID)), (x, y, p[nodes[i], 2]))
    return [{'name': f'fh1_reverb_{i}', 'class': 'SFXEmitter', '__parent': 'fh1_reverb', 'position': [round(float(x - CX), 2), round(float(y + CZ), 2), round(float(z + 2), 2)],
             'fadeInTime': 0, 'fadeOutTime': 0, 'isLooping': False, 'maxDistance': REVERB_RADIUS, 'scale': [REVERB_RADIUS] * 3, 'track': REVERB_EVENT, 'useTrackDescriptionOnly': True}
            for i, (x, y, z) in enumerate(cells[k] for k in sorted(cells)) if abs(x - CX) < HALF and abs(y + CZ) < HALF]


mod = Path(beamng.find_mods()) / 'unpacked' / LVL; level = mod / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
p, roads = nav.read(); pub = public_nodes(p, roads)
spawns = []      # rows for the top-level items file, which other scripts change at the same time, so it's edited in one go below
points = [{'objectname': 'spawn_redrock', 'translationId': 'Red Rock', 'preview': 'spawn_redrock_preview.jpg'}]
red = next(o for o in mission.rows(items) if o.get('name') == 'spawn_redrock')['position']
spawn_preview(red[0] + CX, CZ - red[1], level / 'spawn_redrock_preview.jpg')
if LVL == 'fh1_colorado':
    for name, obj, x, y in SPAWNS:
        row, point, where = spawn(name, obj, x, y, p, pub)
        spawns.append(row); points.append(point); spawn_preview(*where, level / point['preview'])
objects, reverb_zones = soundscape.read()
emitters = reverb(reverb_zones, p, roads)
(items.parent / 'fh1_reverb').mkdir(exist_ok=True)
(items.parent / 'fh1_reverb' / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in emitters))
with mission.edit(items) as rows:
    rows[:] = [o for o in rows if not (o.get('class') == 'SpawnSphere' and o['name'] != 'spawn_redrock')] + spawns
    if not any(o.get('name') == 'fh1_reverb' for o in rows):
        rows.append({'name': 'fh1_reverb', 'class': 'SimGroup', '__parent': 'MissionGroup'})
level_preview(level / f'{LVL}_preview.jpg')

info = json.loads((level / 'info.json').read_text())
info.update({'authors': 'Playground Games, Turn 10 (original game). Converted locally for personal use',
             'country': 'levels.common.country.usa', 'region': 'northAmerica', 'biome': 'Mountains, plains and desert',
             'previews': [f'{LVL}_preview.jpg'], 'spawnPoints': points, 'defaultSpawnPointName': 'spawn_redrock'})
if LVL == 'fh1_colorado':           # a chunk keeps build3.py's title, or the level list shows two "Forza Horizon Colorado"
    info.update({'title': 'Forza Horizon Colorado',
                 'description': "Forza Horizon's Colorado, converted from your own copy: Carson, Red Rock, the Horizon Festival, Finley Dam and the Rockies, "
                                'with FH1\'s roads, lighting and regional ambience. Personal use only, not for redistribution.',
                 'features': 'Whole Horizon map, towns, interstate, mountain passes, dirt roads', 'suitablefor': 'Free roam, road trips, rally, touring',
                 'roads': 'Interstate 70 and 76, county routes, dirt tracks'})
(level / 'info.json').write_text(json.dumps(info, indent=2))

z = zones(p, roads)
(level / 'city.sites.json').write_text(json.dumps({'description': 'Regions from colorado.nav', 'dir': f'/levels/{LVL}/', 'filename': 'city.sites.json', 'locations': {},
                                                   'name': 'city', 'parkingSpots': [], 'zones': z}, indent=1))
names = road_names(roads); loops_, shots_ = placed(objects)
(level / 'fh1_extras.json').write_text(json.dumps({'roads': names, 'ambience': {k['name']: f'art/sound/fh1/{AMBIENCE[k["name"].strip()]}.ogg' for k in z if k['name'].strip() in AMBIENCE},
                                                   'defaultAmbience': 'art/sound/fh1/plain.ogg',
                                                   'sounds': [{'file': f'art/sound/fh1/{f}.ogg', 'x': x - CX, 'y': y + CZ, 'near': near, 'far': far, 'volume': v} for f, x, y, near, far, v in SOUNDS],
                                                   'placedLoops': loops_, 'oneshots': shots_}))
(mod / 'lua' / 'ge' / 'extensions').mkdir(parents=True, exist_ok=True); (mod / 'scripts' / LVL).mkdir(parents=True, exist_ok=True)
shutil.copy('fh1Extras.lua', mod / 'lua' / 'ge' / 'extensions' / 'fh1Extras.lua')
(mod / 'scripts' / LVL / 'modScript.lua').write_text('load("fh1Extras")\nsetExtensionUnloadMode("fh1Extras", "manual")\n')
print('done', len(points), 'spawns', len(z), 'zones', len(names), 'named nodes', len(emitters), 'reverb emitters', sum(len(l['points']) for l in loops_ + shots_), 'placed sounds ->', level)
