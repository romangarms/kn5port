"""FH1's races as BeamNG time trials: one mission per event, with FH1's name, route and laps, started where FH1's event marker stands
beside its starter trailer (build3.py keeps the trailers). Run after build3.py: python races.py [fh1_colorado|fh1_redrock]. Needs no rebuild.

Each mission gets FH1's props for its route as mainPrefab.prefab.json, which BeamNG loads only while the race runs.

Street, Nemesis and plane races have checkpoints with widths. Festival races only have GPS waypoints and a finish line, because FH1 lined
the course with barriers, so their waypoints become checkpoints. FH1 has no medal times, as its races were against AI, so they come from
FH1's own length for each route and an average speed for its surface."""
import json, math, re, shutil, sys
from pathlib import Path
import numpy as np
from PIL import ImageDraw
sys.path.insert(0, '../..')
from kn5port import beamng
import events, frame, mapart

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
WAYPOINT_RADIUS = 12.0          # metres; a waypoint is only a point on the road, so its checkpoint spans the road
MIN_RADIUS, MAX_RADIUS = 6.0, 25.0
AHEAD = 10.0                    # waypoints less than this far in front of the pole are behind the grid
FINISH_SNAP = 40.0              # a waypoint this close to the finish line is replaced by it
CIRCUIT = 100.0                 # a finish this close to the pole closes the loop
DIRT = re.compile(r'Dirt|Trail|Cross', re.I)
GOLD_SPEED = (28.0, 20.0)       # m/s average on asphalt and on dirt
SILVER, BRONZE = 1.15, 1.35     # times relative to gold


def b(p):
    """FH1 game axes to BeamNG"""
    return np.array([p[0] - CX, p[2] + CZ, p[1] + LIFT])


def quat_z(d):
    """[x, y, z, w] turning BeamNG's forward (+y, as race start positions use it) to face horizontal direction d"""
    t = math.atan2(-d[0], d[1])
    return [0.0, 0.0, math.sin(t / 2), math.cos(t / 2)]


def course(r):
    """(pole, [(BeamNG position, radius)], closed) for one route, or None if it isn't a race"""
    grid = r.get('start_location'); gates = []
    if not grid:
        return None
    pole, facing = grid[0][0], grid[0][1]
    trigger, left, right = r.get('end_race_cannon_trigger'), r.get('end_race_cannon_left'), r.get('end_race_cannon_right')
    finish = None
    if trigger and left and right:        # the trigger's own width is a flat 50 m; the cannons stand either side of the road
        finish = (trigger[0][0], float(np.mean([np.linalg.norm(a[0] - c[0]) for a, c in zip(left, right)])) / 2 + 1)
    if r.get('route_checkpoint'):
        gates = [(p, np.clip(w / 2, MIN_RADIUS, MAX_RADIUS) if w else WAYPOINT_RADIUS) for p, f, w in r['route_checkpoint']]
    else:
        gates = [(p, WAYPOINT_RADIUS) for p, f, w in r.get('route_waypoint', [])]
        while gates and np.dot(gates[0][0] - pole, facing) < AHEAD:
            gates.pop(0)
    # waypoints run on past the finish, and circuits' grids straddle it, so cut at the last one near it. Without finish cannons, the
    # finish is the waypoint nearest where cars park after the race
    end = finish[0] if finish else r['post_race_location'][0][0] if r.get('post_race_location') and not r.get('route_checkpoint') else None
    if end is not None and gates:
        d = np.array([np.linalg.norm(p - end) for p, _ in gates]); k = int(np.nonzero(d <= d.min() + FINISH_SNAP)[0].max())
        beyond = finish and (np.dot(gates[k][0] - end, trigger[0][1]) > 0 or d[k] < FINISH_SNAP)
        gates = gates[:k + (not beyond)]
    if finish:
        gates.append(finish)
    if not gates:
        return None
    closed = len(gates) > 2 and np.linalg.norm((gates[-1][0] - pole)[[0, 2]]) < CIRCUIT
    return pole, facing, [(b(p), float(rad)) for p, rad in gates], closed


def race_file(name, pole, facing, gates, closed, laps):
    """the race.race.json BeamNG's time trials read. For a circuit the last gate is the start and finish line, for a sprint a first node sits on the pole"""
    nodes = gates[-1:] + gates[:-1] if closed else [(b(pole), WAYPOINT_RADIUS)] + gates
    ids = iter(range(1, 10 ** 6)); pathnodes, starts = [], []
    start = {'oldId': next(ids), 'name': 'Start Position fwd', 'pos': b(pole).tolist(), 'rot': quat_z([facing[0], facing[2]])}
    starts.append(start)
    for i, (p, rad) in enumerate(nodes):
        prev, nxt = nodes[i - 1][0] if (i or closed) else p, nodes[(i + 1) % len(nodes)][0] if (i + 1 < len(nodes) or closed) else p
        d = nxt - prev
        if np.linalg.norm(d[:2]) < 1:
            d = p - nodes[i - 1][0]
        d = d / np.linalg.norm(d)
        rec = {'oldId': next(ids), 'name': f'Pathnode {i + 1} Recovery Forward', 'pos': (p + [0, 0, .5]).tolist(), 'rot': quat_z(d[:2])}
        starts.append(rec)
        pathnodes.append({'customFields': {'values': {}, 'names': {}, 'tags': {}, 'types': {}}, 'mode': 'manual', 'name': f'Pathnode {i + 1}', 'navRadiusScale': 1,
                          'normal': d.tolist(), 'oldId': next(ids), 'pos': p.tolist(), 'radius': rad, 'recovery': rec['oldId'], 'reverseRecovery': -1,
                          'sidePadding': [1, 3], 'visible': True})
    n = len(pathnodes); last = n if closed else n - 1
    segments = [{'capsules': {}, 'from': pathnodes[i]['oldId'], 'to': pathnodes[(i + 1) % n]['oldId'], 'mode': 'waypoint', 'name': f'Segment {i + 1}', 'oldId': next(ids)} for i in range(last)]
    return {'authors': 'Playground Games (FH1), converted', 'classification': {'allowRollingStart': False, 'branching': False, 'closed': bool(closed), 'reversible': False},
            'date': 0, 'defaultLaps': laps, 'defaultStartPosition': start['oldId'], 'description': name, 'difficulty': 24,
            'endNode': -1 if closed else pathnodes[-1]['oldId'], 'forwardPrefabs': {}, 'hideMission': False, 'name': name, 'pacenotes': {}, 'pathnodes': pathnodes,
            'prefabs': {}, 'reversePrefabs': {}, 'reverseStartPosition': -1, 'rollingReverseStartPosition': -1, 'rollingStartPosition': -1, 'segments': segments,
            'startNode': pathnodes[0]['oldId'], 'startPositions': starts}


def medals(e, length):
    gold = length / GOLD_SPEED[bool(DIRT.search(e['track']))]
    return {k: round(gold * f) for k, f in (('goldTime', 1), ('silverTime', SILVER), ('bronzeTime', BRONZE))}


def info(e, folder, trigger, facing, laps, closed, times):
    t = {'goldTime': times['goldTime'], 'silverTime': times['silverTime'], 'bronzeTime': times['bronzeTime']}
    for k in ('goldTime', 'silverTime', 'bronzeTime'):
        t[k + '2'] = t[k + '3'] = t[k]
    lap = {f'{m}{n}Laps': -1 for m in ('bronze', 'silver', 'gold') for n in (1, 2, 3)}
    return {'additionalAttributes': {'difficulty': 'medium'}, 'author': 'Playground Games (FH1), converted',
            'careerSetup': {'defaultStarKeys': ['bronzeTime', 'silverTime', 'goldTime'], 'showInFreeroam': True, 'skill': '(none)', 'starOutroTexts': {}, 'starRewards': {},
                            'starsActive': {'bronzeTime': True, 'silverTime': True, 'goldTime': True}},
            'customAdditionalAttributes': {}, 'date': 0,
            'description': f"{e['kind']} on {e['track']}" + (f', {laps} laps' if closed and laps > 1 else '') + '. From Forza Horizon; FH1 had no medal times, so these are estimates.',
            'devMission': False, 'grouping': {'id': '', 'label': ''},
            'layers': [{'dir': f'/{folder}/', 'fixed': True, 'isMissionFolderDir': True, 'isMissionTypeDir': False},
                       {'dir': '/gameplay/missionTypes/timeTrial/', 'fixed': True, 'isMissionFolderDir': False, 'isMissionTypeDir': True}],
            'missionType': 'timeTrial',
            'missionTypeData': dict(lap, **t, allowFlip=True, allowRecover=True, allowRollingStart=False, canChangeLapAmount=bool(closed), closed=bool(closed),
                                    defaultLaps=laps, flipLimit=-1, flipPenalty=5, recoverLimit=-1, recoverPenalty=5, justFinishPenalty=30, mapPreviewMode='navgraph',
                                    reversible=False, startScreenText=f"{e['name']}: {e['track']}"),
            'name': e['name'], 'retryBehaviour': 'infiniteRetries',
            'setupModules': {'environment': {'enabled': False}, 'traffic': {'enabled': False},
                             'vehicles': {'enabled': True, 'includePlayerVehicle': True, 'playerVehicleDiffs': {}, 'prioritizePlayerVehicle': True, 'vehicles': {}}},
            'startCondition': {'type': 'automatic'},
            'startTrigger': {'level': LVL, 'pos': trigger.tolist(), 'radius': 5, 'rot': quat_z([facing[0], facing[2]]), 'type': 'coordinates'},
            'visibleCondition': {'type': 'always'}}


def preview(pole, gates, path):
    """the route over FH1's map art, in mesh axes (x, -z)"""
    pts = np.array([[pole[0], -pole[2]]] + [[p[0] + CX, CZ - p[1]] for p, _ in gates])
    lo, hi = pts.min(0), pts.max(0); c = (lo + hi) / 2; span = max(hi[0] - lo[0], (hi[1] - lo[1]) * 4 / 3, 600) * 1.25
    box = (c[0] - span / 2, c[1] - span * 3 / 8, c[0] + span / 2, c[1] + span * 3 / 8)
    im = mapart.art(box); d = ImageDraw.Draw(im); W, H = mapart.SIZE
    px = [((x - box[0]) / (box[2] - box[0]) * W, (z - box[1]) / (box[3] - box[1]) * H) for x, z in pts]
    d.line(px, fill=(255, 90, 30), width=5, joint='curve')
    (x, y) = px[0]; d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(255, 255, 255), outline=(255, 90, 30), width=4)
    im.save(path / 'preview.jpg', quality=88); im.resize((W // 2, H // 2)).save(path / 'thumbnail.jpg', quality=88)


mod = Path(beamng.find_mods()) / 'unpacked' / LVL
if not (mod / 'levels' / LVL / 'info.json').exists():
    sys.exit(f'{mod} has no level: run build3.py first')
root = mod / 'gameplay' / 'missions' / LVL
shutil.rmtree(root, ignore_errors=True)
nodes = events.nodes(); made = []; skipped = []; props = 0
for e in events.races():
    c = course(events.route(e['route']))
    if c is None:
        skipped.append(e['id']); continue
    pole, facing, gates, closed = c
    if max(np.abs(b(pole)[:2]).max(), max(np.abs(p[:2]).max() for p, _ in gates)) > HALF:
        skipped.append(e['id']); continue
    laps = int(e['laps']) if closed else 1
    marker = nodes.get(e['id'] + '_NODE') or (pole, facing)
    rel = f'gameplay/missions/{LVL}/timeTrial/{len(made) + 1:03d}-{e["id"].lower()}'; folder = mod / rel; folder.mkdir(parents=True)
    race = race_file(e['name'], pole, facing, gates, closed, laps)
    length = e['length'] * laps
    (folder / 'race.race.json').write_text(json.dumps(race, indent=1))
    # FH1's scenery for this route (build3.py, from the zone records' list bytes): the stock time trial spawns mainPrefab when the race
    # starts and deletes it when the race ends
    pf = mod / 'levels' / LVL / 'art' / 'fh1_races' / f"route_{e['route']}.prefab.json"
    if pf.exists():
        shutil.copyfile(pf, folder / 'mainPrefab.prefab.json'); props += pf.read_text().count('TSStatic')
    (folder / 'info.json').write_text(json.dumps(info(e, rel, b(marker[0]), marker[1], laps, closed, medals(e, length)), indent=1))
    preview(pole, gates, folder)
    made.append((e['id'], e['name'], 'circuit' if closed else 'sprint', laps, len(gates), round(length)))
for m in made:
    print(*m)
print('race props', props, 'in mission prefabs')
print('done', len(made), 'time trials,', len(skipped), 'skipped:', ' '.join(skipped), '->', root)
