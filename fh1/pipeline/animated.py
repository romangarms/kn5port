"""FH1's animated objects: windmills, wind turbines, tractors, combines, the crop duster, and the festival rides, hot air balloons and inflatables. The fan is cut out of the built shape and given an "ambient" animation, which BeamNG plays by itself,
as West Coast USA's pump jacks do. Vehicles follow the route in their ANIM_*.pgeo the same way. Moving parts have no collision.
Run after build3.py, which wipes the level: python animated.py [fh1_colorado]. Safe to run twice. Needs no rebuild."""
import itertools, json, os, pickle, re, struct, subprocess, sys
from pathlib import Path
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation
from daewrite import FARCUT, write_lod
import animcopy, dds, frame, gamepath, gamezip, granny, granny2, mission, rmb, soundscape, tex
from animsets import placed, records
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]]); S = np.diag([1., 1., -1.])
# name: the source mesh, whose built shape is m<its number>; a track without it skips the object. group: the anim_proc set that places it. hub and axis: the Fan bone from its ANIM_*.pgeo, checked against the mesh, in BeamNG axes.
# radius and depth: parts wholly inside this disc are the fan. origin: the point of the shape that a set's position means, where the ANIM model is centred differently.
# turns: seconds per turn, chosen by eye as FH1's curves aren't decoded. Each speed needs its own shape, as the animation lives in the file.
# tower_lod and fan_lod: detail sizes, from build3.py's detail_sizes for the object's radius
OBJECTS = {'metal': dict(name='PLA_FARM_WindmillMetal_LOD00_', group='fx_windmillsmetal', hub=(-.507, -1.174, 9.931), radius=1.75, depth=.6, turns=(2.4, 2.9, 3.5), tower_lod=(25, 9, 3), fan_lod=(20, 6, 1)),
           'wood': dict(name='PLA_FARM_WindmillWood_LOD00_', group='fx_windmillswood', hub=(-.02, -1.55, 9.94), radius=1.75, depth=.5, turns=(2.4, 2.9, 3.5), tower_lod=(25, 9, 3), fan_lod=(20, 6, 1)),
           'turbine': dict(name='GLOB_STFN_WindTurbine_02_LOD00_', group='fx_maintown', hub=(7.42, -4.6, 82.17), radius=56, depth=1.6, origin=(7.5, -1.35, 0), turns=(5, 6, 7.5), tower_lod=(211, 88, 45), fan_lod=(190, 70, 1))}
AXIS = (0, 1, 0)
# mesh: the source meshes for LOD0 and LOD1, by name, as each track numbers them differently. model: the file holding the route. group: the anim_proc set that places it. lod: detail sizes
# front: which way along y the mesh faces, given for vehicles whose route is a quantised curve. key: seconds between keys.
# parts: meshes that spin about the vehicle's x axis through hub, in BeamNG axes, once per `seconds`. The combine's reel speed is chosen by eye
VEHICLES = {'tractor': dict(mesh=('Tractor_LOD00', 'Tractor_LOD01'), model='ANIM_Tractor_001.pgeo', group='clrd_festival_tractor', lod=(40, 14, 1)),
            'combine': dict(mesh=('L_Front_Wheel_LOD00', 'L_Front_Wheel_LOD01'), model='ANIM_CombineHarvester_001.pgeo', group='clrd_festival_combineharvester', lod=(40, 14, 1), front=-1, key=.25,
                            parts={'reel': dict(mesh='Reel_LOD00', hub=(0, -5.607, .992), seconds=1.5)})}
_NO = {}
for c_ in pickle.load(open('../cat2.pkl', 'rb')):
    _NO.setdefault(c_['name'], int(c_['f'].split('.')[1]))
OBJECTS = {k: {**{a: v for a, v in w.items() if a != 'name'}, 'shape': f"m{_NO[w['name']]}"} for k, w in OBJECTS.items() if w['name'] in _NO}
VEHICLES = {k: dict(w, mesh=tuple(_NO[n] for n in w['mesh']), parts={p: dict(q, mesh=_NO[q['mesh']]) for p, q in w.get('parts', {}).items()})
            for k, w in VEHICLES.items() if all(n in _NO for n in w['mesh']) and all(q['mesh'] in _NO for q in w.get('parts', {}).values())}
KEY_SECONDS = 1.0; STOPPED = .05; AXLES = 1.4; TRACK = 1.0
# Planes come from Granny meshes. texture: the track pool texture its UVs land on (formats.md, animatedobjects.zip). The route is level in FH1 once its tilted frame is removed
# The crop duster flies with the rides now: its decoded route is level once its skeleton's placement is applied. This path is kept for another plane
FLYERS = {}
FLY_KEY_SECONDS = .5; CLEARANCE = 25.0; GROUND_RADIUS = 80.0; MAX_BANK = 55.0; GRAVITY = 9.81
# Rides, balloons, inflatables, sprinklers and flying birds from the track's own copies (animcopy.py). Every bone that carries geometry becomes a part,
# a shape keyed to the bone's motion from its bind pose; only the most detailed meshes are used. Parts that never move, or smaller than TINY metres
# across (the blimp's tether boxes) unless their kind is in SMALL_PARTS, go together in one still shape. Each kind's parts go in one shape, written once, with
# keys for each moving part's nodes, and every placement stands a TSStatic of it, turned as the placement is. Each part's keys are as far apart as an error at its corners of KEY_ERROR of its size allows, at least KEY_ERROR_MIN metres, against
# the motion sampled every KEY_MIN seconds. The quantised curves wobble a little between their own keys, so a large part can't be held closer
# STILL: metres and degrees a part may move and still count as still
RIDES = {'balloon': 'ANIM_FRGND_HotAirBalloonCircle', 'ferris': 'ANIM_FRGND_Ferris', 'zipper': 'ANIM_FRGND_Zipper', 'booster': 'ANIM_ITLY_Ride_Booster',
         'fan': 'ANIM_ITLY_Ride_Fan', 'nofear': 'ANIM_ITLY_Ride_NoFear', 'roundup': 'ANIM_ITLY_Ride_Roundup', 'blimp': 'ANIM_FRGND_InflatableShip',
         'sprinkler': 'ANIM_WaterSprinkler', 'sprinklersmall': 'ANIM_WaterSprinklerSmall', 'birds': 'ANIM_Birds_Flying_Field_', 'cropduster': 'ANIM_CropDuster_Zone_05_',
         'stagelights': 'ANIM_MainStage_Lights', 'substagelights': 'ANIM_SubStage_Lights', 'autoshowlights': 'ANIM_Autoshow_Lights', 'lasers': 'ANIM_MainStage_Lasers',
         'activation': 'ANIM_Autoshow_Activation_Zone'}
# The festival's light rigs: moving lamps on the main and sub stages, the autoshow's lamps, the main stage's lasers and the autoshow activation zones'
# lamps. Their draw nodes and mesh records are in the people models' form (animcopy.meshes flags), and the main stage's lamps have 16-byte vertices.
# Their beams, lens discs and glows are sprites: a colour texture with transparent texels draws added by its alpha, a glow on black added as is.
# They shine at night only, as FH1's stage effects run 18:40 to 7:00: black colour and an emissive map, which the rig's nightEmissive lights as the
# night overlays in nightlights.py are. SPRITE_NITS by eye from those overlays' 40, not yet seen in-game
SPRITE_NITS = 150
RIGS = {'stagelights', 'substagelights', 'autoshowlights', 'lasers', 'activation'}
# Meshes left out: the crop duster carries a 3473-triangle P51 Mustang propeller on its own propeller's bone, over the biplane's 84-triangle blur disc
SKIP_MESHES = {'P51_Mustang_Propeller'}


def at(x, y, z, yaw=0.0):
    """a placement record as anim_proc sets hold them: rotation rows at 0, 4 and 8, position at 12, in the .pgeo z sign (BeamNG y = z + CZ).
    yaw: degrees about up"""
    c, s_ = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
    return np.array([c, 0, -s_, 0, 0, 1, 0, 0, s_, 0, c, 0, x, y, z, 1], 'f8')


# ANIM models no anim_proc set places and the track has no copy of, read from animatedobjects.zip (animcopy.standalone), with where they go:
# planebanner: FH1 flies ANIM_PlaneBanner_002 once, from the free-roam cutscene that FirstTimeCareerAnimations.xml triggers near (-785, -1753)
# (gamemodes.zip, Colorado/Cutscenes/cutscenes_default_freeroam.xml: x -702.284, y 44.836, z -1351.944, rotation 0.455 degrees, loop 1).
# Its 100 s route closes, so it flies here for good.
# cablecar1 and cablecar2: Beaumont's two gondola lifts, which FH1 draws standing still (stations, pylons, cables and parked cabins, Gondola_LOD00_*).
# No file places the animation, but the parked cabins are copies of its cabins in its own frame. Zone records hold z in the .pgeo sign, as
# anim_proc records do: line 1's parked cabins (meshes 1954-1957) sit at 3167.8, 144.7, 3466.8 turned by diag(1, 1, -1), which puts the moving
# cabins on them to within 0.9 m with no further turn. Line 2's parked cabins (1966-1969) have a pivot the animation doesn't share, so its
# origin is fitted with yaw held at 0: its parked cabins land 1.2 to 2.8 m from its paths and its six pylons 1 to 2 m to one side, between the
# up and down cables, as line 1's are. A straight line can slide along itself, so the origin is held by its bottom station, where the cabins
# turn: 11 m from the station, as line 1's is 15 m. Cabins ride 4 to 24 m over the built ground; line 1's top end turns about 20 m inside the
# hill, where FH1 has no top station. FH1's parked cabins are left out of the build (build3.py ANIMATED_INSTEAD)
STANDALONE = {'planebanner': 'ANIM_PlaneBanner_002', 'cablecar1': 'ANIM_Cablecars_001', 'cablecar2': 'ANIM_Cablecars_002'}
STANDALONE_AT = {'planebanner': [at(-702.284, 44.836, -1351.944, .455)], 'cablecar1': [at(3167.8, 144.7, 3466.8)], 'cablecar2': [at(2744.19, 145.25, 3739.57)]}
# Kinds that travel: their placement is lifted if the largest part's route passes under the ground samples, to FLIGHT_CLEARANCE metres above them
FLIGHTS = {'cropduster', 'planebanner'}; FLIGHT_CLEARANCE = 5.0
SMALL_PARTS = {'sprinkler', 'sprinklersmall', 'birds'}
# Left out: ANIM_FRGND_InflatableSprayGun. FH1's only three (sets clrd_festival_plns_animated_objects_004_1 to _3) stand under the ground 5 km
# out, 3.7 km west of the plains festival site, hidden in FH1 too. Stood on the ground they are an untextured balloon on a pole, not a ride
KEY_ERROR = .01; KEY_ERROR_MIN = .05; KEY_MIN = 1 / 30; STILL = (.05, .5); TINY = 1.0
# Movers: kinds whose travel fh1Extras.lua plays instead of the shape, so their sprays and sounds can follow them. A shape's own animation runs on a
# clock Lua can't read. The vehicles (VEHICLES) all move this way, and of RIDES and STANDALONE these. A mover's shape keeps only its parts' motion
# relative to its largest moving part (the body), such as the propeller, whose phase doesn't show. MOVER_KEY: seconds between Lua's keys
MOVERS = {'cropduster', 'planebanner'}; MOVER_KEY = .1
# Vehicle effect bones (Granny bone name ends) -> the end of the effect the model's tags name for them: the tractor's two rear sprays, the combine's
# rear sprays, mist and thick dust. Their sprays run while the vehicle moves
VEHICLE_FX = {'Spray_Right': 'Spray_Rear', 'Spray_Left': 'Spray_Rear', 'Back_Mist': 'Spray_Mist', 'Back_Thick': 'Spray_Thick'}
# How far away a mover's sprays wake, and its FH1 sound event: FH1 tags ANIM_PlaneBanner_001 with BiPlane_Distant, and the duster is the same biplane
MOVER_FX_DIST = 600; MOVER_SOUNDS = {'cropduster': 'BiPlane_Distant', 'planebanner': 'BiPlane_Distant'}
# Ride sounds come from their models' tags (soundscape.TRIGGERS). Loops play while near, as other placed loops do. Of the one-shots, these play now and
# then, with the shortest and longest wait between in seconds. The rest (the Fan's scoosh up and down, NoFear's whine) are timed to the ride's motion,
# whose clock Lua can't see, so they are left out
RIDE_SHOTS = {'TRG_Screams_Oneshot': (6, 16), 'VFX_HotAirBalloon': (5, 15)}
EULER = ('ZYX', 'XYZ', 'YXZ', 'YZX', 'ZXY', 'XZY')
PLAIN = (.55, .55, .55, 1)            # draws whose colour slot is empty
GLOW_NITS = 3000                      # ride bulbs at night, as build3.py's lamp glass


def channel(node, target, param, times, values):
    """One animation curve, in the form Collada loaders expect."""
    a = f"{node}_{re.sub('[^A-Za-z]', '', target)}"; n = len(times)
    t = ' '.join(f'{v:.4f}' for v in times); o = ' '.join(f'{v:.4f}' for v in values)
    return (f'<animation id="{a}">'
            f'<source id="{a}-in"><float_array id="{a}-in-a" count="{n}">{t}</float_array><technique_common><accessor source="#{a}-in-a" count="{n}" stride="1"><param name="TIME" type="float"/></accessor></technique_common></source>'
            f'<source id="{a}-out"><float_array id="{a}-out-a" count="{n}">{o}</float_array><technique_common><accessor source="#{a}-out-a" count="{n}" stride="1"><param name="{param}" type="float"/></accessor></technique_common></source>'
            f'<source id="{a}-int"><Name_array id="{a}-int-a" count="{n}">{" ".join(["LINEAR"] * n)}</Name_array><technique_common><accessor source="#{a}-int-a" count="{n}" stride="1"><param name="INTERPOLATION" type="name"/></accessor></technique_common></source>'
            f'<sampler id="{a}-s"><input semantic="INPUT" source="#{a}-in"/><input semantic="OUTPUT" source="#{a}-out"/><input semantic="INTERPOLATION" source="#{a}-int"/></sampler>'
            f'<channel source="#{a}-s" target="{node}/{target}"/></animation>\n')


def transforms(order='ZYX'):
    """a node's location, then rotations about its axes in this order, outermost first, then scale"""
    return ('<translate sid="location">0 0 0</translate>' + ''.join(f'<rotate sid="rotation{c}">{" ".join("1" if c == d else "0" for d in "XYZ")} 0</rotate>' for c in order)
            + '<scale sid="scale">1 1 1</scale>')


def animate(path, curves, extra='', order='ZYX'):
    """Give each LOD node the separate transforms that animation channels address, as in the stock pump jacks. curves: (target, param, times, values),
    or {group: (curves, order)} for each write_lod group that moves on its own; other groups' nodes stay put."""
    text = path.read_text(); anims = []
    for node in re.findall(r'<node id="(o\d+_a\d+)"', text):
        cs, o = (curves, order) if not isinstance(curves, dict) else curves.get(int(node[1:node.index('_')]), (None, None))
        if cs is None:
            continue
        m = re.search(rf'(<node id="{node}"[^>]*>\s*)<matrix sid="transform">[^<]*</matrix>', text)
        text = text[:m.start()] + m.group(1) + transforms(o) + text[m.end():]
        anims += [channel(node, *c) for c in cs]
    if not anims:
        raise ValueError(f'no LOD nodes in {path}')
    i = text.index('<library_visual_scenes')
    text = text[:i] + '<library_animations>\n' + ''.join(anims) + '</library_animations>\n' + text[i:]
    if extra:
        text = text.replace('</library_geometries>', extra[0] + '</library_geometries>', 1)
        text = re.sub(r'(<visual_scene [^>]*>)', lambda m: m.group(1) + extra[1], text, 1)
    path.write_text(text)


def spin(seconds, keys=8):
    times = [seconds * i / keys for i in range(keys + 1)]
    return [('rotationY.ANGLE', 'ANGLE', times, [360 * i / keys for i in range(keys + 1)])]


def bounds(lo, hi):
    """BeamNG culls a shape by its resting bounds, so a vehicle would vanish partway along its route. A node named bounds sets them."""
    x = [(lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]), (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]), (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
    tri = '0 2 1 0 3 2 4 5 6 4 6 7 0 1 5 0 5 4 1 2 6 1 6 5 2 3 7 2 7 6 3 0 4 3 4 7'
    p = ' '.join(f'{v:.3f}' for q in x for v in q)
    return (f'<geometry id="bounds_g" name="bounds_g"><mesh><source id="bounds_p"><float_array id="bounds_a" count="24">{p}</float_array><technique_common><accessor source="#bounds_a" count="8" stride="3">'
            '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>'
            f'<vertices id="bounds_v"><input semantic="POSITION" source="#bounds_p"/></vertices><triangles count="12"><input semantic="VERTEX" source="#bounds_v" offset="0"/><p>{tri}</p></triangles></mesh></geometry>\n',
            '<node id="bounds" name="bounds" type="NODE"><instance_geometry url="#bounds_g"/></node>')


def route(name):
    """Times and positions of an ANIM model's root track, the longest move among its raw float curves. These are a run of rising knots followed by three floats per knot."""
    b = gamezip.read('animatedobjects.zip', name); F = np.frombuffer(b[:len(b) // 4 * 4], '>f4'); best = None; i = 0x100 // 4
    while i < len(F) - 8:
        j = i
        while j + 1 < len(F) and 0 <= F[j] < F[j + 1] < 2000 and F[j + 1] - F[j] < 60:
            j += 1
        n = j - i + 1
        if n >= 7 and j + 1 + 3 * n <= len(F):
            c = F[j + 1:j + 1 + 3 * n].astype('f8').reshape(-1, 3)
            if np.isfinite(c).all() and np.abs(c).max() < 5000 and (best is None or np.ptp(c, axis=0).max() > np.ptp(best[1], axis=0).max()):
                best = (F[i:j + 1].astype('f8'), c)
            i = j + 1
        else:
            i += 1
    return best


def level_route(c):
    """Route in BeamNG axes with the tilt of its best-fit plane removed. FH1's frame for it is tilted, but the flight is flat in that plane. Compass direction is kept as far as a plain tilt keeps it."""
    n = np.linalg.svd(c - c.mean(0), full_matrices=False)[2][2]; n = -n if n[1] < 0 else n
    v = np.cross(n, (0, 1, 0)); k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    w = c @ (np.eye(3) + k + k @ k / (1 + n[1])).T
    return np.c_[w[:, 0], w[:, 2], w[:, 1]]


def quantised_route(b):
    """Times and positions of a D3K16uC16u curve, in Granny axes. Its header is format 10, degree, the top half of 1 / knot scale as a float, 3 scales, 3 offsets, then the count of
    u16 values. The values sit elsewhere, as knots rising from 0 and then 3 controls per knot, so the run that reads as the smoothest path is taken."""
    U = np.frombuffer(b[:len(b) // 2 * 2], '>u2').astype('i8'); rising = np.r_[np.diff(U) > 0, False]
    for i in range(len(b) - 32):
        if b[i] != 10 or b[i + 1] > 3:
            continue
        f = np.array(struct.unpack('>6f', b[i + 4:i + 28])); cnt = struct.unpack('>I', b[i + 28:i + 32])[0]
        if not np.isfinite(f).all() or np.abs(f).max() > 1e4 or not 0 < cnt < 100000 or cnt % 4 or not np.abs(f[:3]).max() > 1e-6:
            continue
        per = struct.unpack('>f', b[i + 2:i + 4] + b'\0\0')[0]; n = cnt // 4; best = None
        for s in np.flatnonzero(U == 0):
            if s + cnt <= len(U) and rising[s:s + n - 1].all():
                c = U[s + n:s + cnt].reshape(-1, 3) * f[:3] + f[3:]; step = np.median(np.abs(np.diff(c, axis=0)).max(1))
                if best is None or step < best[0]:
                    best = (step, U[s:s + n] / per, c)
        if best:
            return best[1:]
    raise ValueError('no D3K16uC16u curve')


def fit_box(u, body, box):
    """Offset that centres a route, with the body's footprint turning along it, in the bounding box of the model's header (min x, y, z, pad, max x, y, z)."""
    d = np.gradient(u, axis=0); a = np.arctan2(d[:, 0], d[:, 1]); c, s = np.cos(a)[:, None], np.sin(a)[:, None]
    fx, fy = np.ptp(body[:, 0]) / 2, np.ptp(body[:, 1]) / 2
    pts = np.concatenate([u + np.c_[c * x + s * y, -s * x + c * y] for x in (-fx, fx) for y in (-fy, fy)])
    lo, hi = np.array([box[0], box[2]]), np.array([box[4], box[6]])
    print('  route box', np.round(np.ptp(pts, 0), 1), 'header box', np.round(hi - lo, 1))
    return (lo + hi) / 2 - (pts.min(0) + pts.max(0)) / 2


def turn(yaw, roll, pitch):
    """The rotation of a node with rotationZ, rotationY and rotationX in that order, in degrees."""
    z, y, x = np.radians([yaw, roll, pitch])
    return (np.array([[np.cos(z), -np.sin(z), 0], [np.sin(z), np.cos(z), 0], [0, 0, 1]]) @ np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
            @ np.array([[1, 0, 0], [0, np.cos(x), -np.sin(x)], [0, np.sin(x), np.cos(x)]]))


def flight(t, q):
    """Keys through a route, with the plane's heading, pitch and bank taken from its motion, as FH1's rotation curves aren't decoded."""
    times = np.arange(0, t[-1], FLY_KEY_SECONDS); p = np.c_[[np.interp(times, t, q[:, i]) for i in range(3)]].T
    times = np.r_[times, t[-1]]; p = np.r_[p, p[:1]]
    d = np.gradient(p, times, axis=0); flat = np.hypot(d[:, 0], d[:, 1])
    yaw = np.unwrap(np.arctan2(-d[:, 0], d[:, 1]))
    rate = np.convolve(np.gradient(yaw, times), np.ones(7) / 7, 'same')
    bank = np.clip(np.degrees(np.arctan(flat * rate / GRAVITY)), -MAX_BANK, MAX_BANK)
    return times, p, np.degrees(yaw), np.degrees(np.arctan2(d[:, 2], flat)), -bank


def ground_triangles(lo, hi):
    """Ground triangles in a box, from the collision shapes of the map cells, in world space."""
    out = []
    for o in world:
        name = Path(o['shapeName']).name; q = np.array(o['position'])
        if not name.endswith(('_col.dae', '_colf.dae')) or (q[:2] < lo - 600).any() or (q[:2] > hi + 600).any():
            continue
        R = np.array(o.get('rotationMatrix', np.eye(3).ravel())).reshape(3, 3)
        for geo in (level / 'art' / 'shapes' / 'fh1' / name).read_text().split('<geometry ')[1:]:
            v = re.search(r'-p-a"[^>]*>([^<]*)<', geo)
            V = np.array(v.group(1).split(), 'f8').reshape(-1, 3) @ R + q
            for inputs, p in re.findall(r'<triangles[^>]*>(.*?)<p>([^<]*)</p>', geo, re.S):
                step = max(map(int, re.findall(r'offset="(\d+)"', inputs))) + 1
                out.append(V[np.array(p.split(), 'i8')[::step].reshape(-1, 3)])
    T = np.concatenate(out); box = (T[:, :, :2].max(1) >= lo).all(1) & (T[:, :, :2].min(1) <= hi).all(1)
    return T[box]


def height(T, xy):
    """Height of the highest triangle over each point."""
    lo, hi = T[:, :, :2].min(1), T[:, :, :2].max(1); out = np.zeros(len(xy))
    for k, q in enumerate(xy):
        A, B_, C = T[(lo <= q).all(1) & (hi >= q).all(1)].transpose(1, 0, 2)
        e, f = B_ - A, C - A; g = q - A[:, :2]; d = e[:, 0] * f[:, 1] - e[:, 1] * f[:, 0]
        u = (g[:, 0] * f[:, 1] - g[:, 1] * f[:, 0]) / d; v = (e[:, 0] * g[:, 1] - e[:, 1] * g[:, 0]) / d
        inside = (u >= -1e-3) & (v >= -1e-3) & (u + v <= 1.001)
        out[k] = (A[:, 2] + u * e[:, 2] + v * f[:, 2])[inside].max()
    return out


def on_ground(T, xy, yaw):
    """Height, pitch and roll of a vehicle on one plane fitted to the ground under its route, so FH1's flat route keeps its shape and only tilts.
    Points far off the plane, such as objects on the ground, are dropped before the second fit."""
    A = np.c_[xy, np.ones(len(xy))]; h = height(T, xy); keep = np.ones(len(xy), bool)
    for _ in range(2):
        coef = np.linalg.lstsq(A[keep], h[keep], rcond=None)[0]; keep = np.abs(h - A @ coef) < 1
    plane = lambda q: np.c_[q, np.ones(len(q))] @ coef
    a = np.radians(yaw)[:, None]; ahead = np.c_[-np.sin(a), np.cos(a)]; right = np.c_[np.cos(a), np.sin(a)]
    f, b_ = plane(xy + AXLES * ahead), plane(xy - AXLES * ahead); r, l = plane(xy + TRACK * right), plane(xy - TRACK * right)
    return (f + b_ + r + l) / 4, np.degrees(np.arctan2(f - b_, 2 * AXLES)), np.degrees(np.arctan2(l - r, 2 * TRACK))


def ground_samples():
    """Points on the ground. The level has no TerrainBlock, as FH1's meshes are the ground, so this uses what stands on them: forest items and road waypoints."""
    pts = [o['pos'] for f in (level / 'forest').glob('*.json') if not f.name.startswith('fh1_m') for o in map(json.loads, filter(str.strip, f.read_text().splitlines()))]
    pts += [o['position'] for o in map(json.loads, filter(str.strip, (mg / 'fh1_nav' / 'items.level.json').read_text().splitlines())) if 'position' in o]
    return np.array(pts, 'f8')


def mesh_groups(nos):
    """Geometry straight from the source meshes, for objects the build merged into map cells. Naming follows Textures and box_uv in build3.py."""
    MT = pickle.load(open('../meshtab.pkl', 'rb')); IDX = pickle.load(open('../texshader.pkl', 'rb'))[0]
    mats = json.loads((level / 'art' / 'shapes' / f'{LVL}.materials.json').read_text()); out = []
    for lv, no in enumerate(nos):
        m = rmb.parse(open(f'../x/{gamepath.MESH}.{no:05d}.rmb.bin', 'rb').read())['meshes'][0]
        p, nr, _ = rmb.verts(m); a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd'])
        r0 = np.frombuffer(a[:, 16:20].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535
        pb = np.c_[p[:, 0], -p[:, 2], p[:, 1]]; nb = np.c_[nr[:, 0], -nr[:, 2], nr[:, 1]]
        for s_ in m['secs']:
            T = rmb.tris(s_['ib'], m['nv'], s_['ints'][1])
            names = [f'fh1_d{IDX[i][0]:X}' for i in MT[no][0] if i in IDX]
            # the plain name, else one with build3.py's suffixes, such as _o<id> for an AO map
            mat = next((n for n in names if n in mats), None) or next((k for n in names for k in mats if k.startswith(n + '_')), None)
            if mat is None:
                sys.exit(f'no built material for mesh {no}: {names}')
            uv = np.array(s_['fl'][4:6]) + r0 * np.array(s_['fl'][6:8])
            # UV1, which AO maps read: the same box as UV0, as uv1_box in build3.py
            uv1 = np.array(s_['fl'][4:6]) + np.frombuffer(a[:, 20:24].tobytes(), '>u2').reshape(-1, 2).astype('f8') / 65535 * np.array(s_['fl'][6:8]) if m['sd'] >= 24 else uv
            out.append(((mat, (lv,)), pb, nb, uv, T.ravel(), uv1))
    return out


def arrays(text, gid):
    def get(name, n):
        m = re.search(rf'<float_array id="{gid}-{name}-a"[^>]*>([^<]*)<', text)
        return np.array(m.group(1).split(), 'f8').reshape(-1, n)
    tri = re.search(rf'<geometry id="{gid}".*?<p>([^<]*)</p>', text, re.S).group(1)
    t = get('t', 2); u = get('u', 2)
    return get('p', 3), get('n', 3), np.c_[t[:, 0], 1 - t[:, 1]], np.array(tri.split(), 'i8').reshape(-1, 3), np.c_[u[:, 0], 1 - u[:, 1]]


def split(p, tri, hub, axis, radius, depth):
    """Triangles of the parts that lie wholly inside the fan's disc. Vertices at the same place are joined, because UV seams duplicate them."""
    _, weld = np.unique(np.round(p, 3), axis=0, return_inverse=True); weld = weld.ravel()
    t = weld[tri]; n = weld.max() + 1
    g = coo_matrix((np.ones(tri.size), (t.ravel(), np.roll(t, 1, axis=1).ravel())), shape=(n, n))
    _, part = connected_components(g, directed=False)
    d = p - hub; along = d @ axis; off = np.linalg.norm(d - np.outer(along, axis), axis=1)
    inside = np.ones(part.max() + 1, bool)
    np.logical_and.at(inside, part[weld], (off < radius) & (np.abs(along) < depth))
    return inside[part[weld[tri[:, 0]]]]


def piece(a, keep):
    """Geometry for the kept triangles only, with unused vertices dropped."""
    p, n, uv, tri, u2 = a
    t = tri[keep]; used, inv = np.unique(t, return_inverse=True)
    return p[used], n[used], uv[used], inv.ravel(), u2[used]


def yaw_matrix(r):
    """As the 96-byte placed sets in build3.py."""
    Rg = np.c_[r[0:3], r[4:7], r[8:11]]; Rg = Rg / np.maximum(np.linalg.norm(Rg, axis=0, keepdims=True), 1e-6)
    gr = S @ Rg if np.linalg.det(Rg) < 0 else S @ Rg @ S
    Rm = B @ gr @ B.T
    if np.linalg.det(Rm) < 0:
        Rm[:, 0] *= -1
    return Rm


def euler(R):
    """Angles for the rotate elements of each rotation matrix, in the order whose middle angle stays furthest from 90 degrees, so no key flips through
    gimbal lock. The outer two are unwrapped, so interpolating between keys turns the short way"""
    best = None
    for order in EULER:
        a = Rotation.from_matrix(R).as_euler(order, degrees=True)
        if best is None or np.abs(a[:, 1]).max() < best[0]:
            best = (np.abs(a[:, 1]).max(), order, a)
    _, order, a = best
    a[:, 0] = np.degrees(np.unwrap(np.radians(a[:, 0]))); a[:, 2] = np.degrees(np.unwrap(np.radians(a[:, 2])))
    return order, a


def keys(T, corners):
    """Which of the transforms T (n, 4, 4) to key, every so many, and the Euler order and angles at them: the widest spacing at which interpolating
    the keys moves none of the corners further from where T puts them than KEY_ERROR of the box's size. The first two are always keys: Granny's
    curves open with a second knot a few milliseconds in, a step at the loop's seam"""
    n = len(T); limit = max(KEY_ERROR * np.ptp(corners, 0).max(), KEY_ERROR_MIN); true = np.einsum('nij,kj->nki', T[:, :3, :3], corners) + T[:, None, :3, 3]; best = None
    for every in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128):
        ix = np.unique(np.r_[0, 1, np.arange(0, n - 1, every), n - 1])
        if len(ix) < 4 and best:
            break
        order, a = euler(T[ix, :3, :3]); f = np.arange(n)
        R = Rotation.from_euler(order, np.c_[[np.interp(f, ix, a[:, j]) for j in range(3)]].T, degrees=True).as_matrix()
        t = np.c_[[np.interp(f, ix, T[ix, j, 3]) for j in range(3)]].T
        if np.linalg.norm(np.einsum('nij,kj->nki', R, corners) + t[:, None] - true, axis=2).max() > limit and best:
            break
        best = (ix, order, a)
    if len(best[0]) > 64:
        # One sharp turn holds the whole loop to its spacing: the cable car cabins turn inside their stations (0.1 m off at every 2nd sample) and
        # kept all 11,503 samples of their 383 s. Keys placed where they're needed instead, splitting each span at its worst sample, keep the same limit
        order, a = euler(T[:, :3, :3]); keep = {0, 1, n - 1}; stack = [(1, n - 1)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            f = np.arange(i + 1, j); w = ((f - i) / (j - i))[:, None]
            R = Rotation.from_euler(order, a[i] + w * (a[j] - a[i]), degrees=True).as_matrix(); t = T[i, :3, 3] + w * (T[j, :3, 3] - T[i, :3, 3])
            e = np.linalg.norm(np.einsum('nij,kj->nki', R, corners) + t[:, None] - true[i + 1:j], axis=2).max(1)
            if e.max() > limit:
                k = int(i + 1 + np.argmax(e)); keep.add(k); stack += [(i, k), (k, j)]
        ix = np.array(sorted(keep))
        if len(ix) < len(best[0]):
            best = (ix, order, a[ix])
    return best


def ride_sizes(r):
    """detail sizes for a part of radius r"""
    s0 = max(3, int(2 * r)); s1 = max(2, min(int(.7 * r), s0 - 1))
    return s0, s1, 1


def ride_texture(t, saved):
    """art/textures/anim<id>.color.dds, saved the first time, so grade.py grades it with the rest. t: a texture-list id, or the name of a
    standalone model's texture in animatedobjects.zip"""
    tname = f'anim{t:X}' if isinstance(t, int) else 'anim_' + re.sub(r'[^A-Za-z0-9_]', '_', t)
    if t not in saved:
        if isinstance(t, int):
            a_, t_ = tex.load(t); dds.save(level / 'art' / 'textures' / f'{tname}.color.dds', a_, tex.blocks(t) if t_['fmt'] == 0x52 else None)
        else:
            dds.save(level / 'art' / 'textures' / f'{tname}.color.dds', animcopy.xds(t))
        saved.add(t)
    return f'/levels/{LVL}/art/textures/{tname}.color.dds'


def glow_mask(t):
    """whether a draw's glow slot (the third) holds a bulb or sign mask: mostly black. Two rides put a specular map there (Roundup's grey
    'Zero Gravity' panel, median 21), and the balloons their lit envelope (median 36); the eight bulb masks have medians of 0 to 8"""
    r = tex.load(t)
    return r is not None and float(np.median(r[0][..., :3].max(2))) < 16


def sprite(t):
    """how a light rig's colour texture draws: 'AddAlpha' for a beam or lens with transparent texels, 'Add' for a glow on black, else None"""
    r = tex.load(t)
    return None if r is None else 'AddAlpha' if r[0][..., 3].min() < 128 else 'Add' if r[0][..., :3].mean() < 12 else None


def ride_material(tl, slot, saved, glow=-1, rig=False):
    """the material for a draw's colour slot. glow: its glow slot, lit at night as build3.py's lamp glass is (GLOW): an emissive map under
    instanceEmissive, which the ride TSStatics' nightEmissive turns on from 18:00 to 6:00. rig: a light rig's draw, whose sprites draw added (sprite)"""
    if slot < 0:
        mats_['fh1_anim_plain'] = {'name': 'fh1_anim_plain', 'mapTo': 'fh1_anim_plain', 'class': 'Material', 'version': 1.5, 'doubleSided': True,
                                   'Stages': [{'baseColorFactor': list(PLAIN), 'roughnessFactor': .7, 'metallicFactor': 0}]}
        return 'fh1_anim_plain'
    look = lambda k: tl[k] if isinstance(tl[k], str) else IDX[tl[k]][0]       # standalone models list texture names
    t = look(slot); g = look(glow) if glow >= 0 else None; g = g if isinstance(g, int) and glow_mask(g) else None
    add = sprite(t) if rig and isinstance(t, int) else None
    if add:
        mat = 'fh1_' + Path(ride_texture(t, saved)).name.split('.')[0] + '_add'
        mats_[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'translucent': True, 'translucentBlendOp': add, 'translucentZWrite': False,
                      'doubleSided': True, 'castShadows': False,
                      'Stages': [{'baseColorMap': ride_texture(t, saved), 'baseColorFactor': [0, 0, 0, 1], 'roughnessFactor': 1, 'metallicFactor': 0, 'emissive': True,
                                  'instanceEmissive': True, 'emissiveMap': ride_texture(t, saved), 'emissiveFactor': [1, 1, 1], 'emissiveIntensityNits': SPRITE_NITS}, {}, {}, {}]}
        return mat
    mat = 'fh1_' + Path(ride_texture(t, saved)).name.split('.')[0] + (f'_g{g:X}' if g is not None else '')
    st = {'baseColorMap': ride_texture(t, saved), 'roughnessFactor': .7, 'metallicFactor': 0}
    if g is not None:
        st.update({'emissiveMap': ride_texture(g, saved), 'emissiveFactor': [1, 1, 1], 'emissiveIntensityNits': GLOW_NITS, 'instanceEmissive': True})
    mats_[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'doubleSided': True, 'Stages': [st]}
    return mat


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
mg = level / 'main' / 'MissionGroup'; items = mg / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
out = level / 'art' / 'shapes' / 'fh1_anim'; out.mkdir(parents=True, exist_ok=True)
for old in out.glob('*'):
    old.unlink()
rel = f'/levels/{LVL}/art/shapes/fh1_anim'
rows = []; movers = []; ride_sounds = {}; world = mission.rows(mg / 'fh1' / 'items.level.json'); was = {o.get('name'): o.get('shapeName') for o in world}
for kind, w in OBJECTS.items():
    src = level / 'art' / 'shapes' / 'fh1' / f"{w['shape']}.dae"
    if not src.exists():          # a chunk such as Red Rock has none of this kind
        print(kind, 'not in this level')
        continue
    text = src.read_text(); mat = re.search(r'<material id="([^"]+)"', text).group(1)
    hub = np.array(w['hub']); axis = np.array(AXIS, 'f8'); origin = np.array(w.get('origin', (0, 0, 0)), 'f8')
    lods = [arrays(text, 'g0'), arrays(text, 'g1' if '<geometry id="g1"' in text else 'g0')]     # some shapes have one LOD
    masks = [split(a[0], a[3], hub, axis, w['radius'], w['depth']) for a in lods]
    print(kind, 'fan triangles', [int(m.sum()) for m in masks], 'of', [len(a[3]) for a in lods])
    tower = [piece(a, ~m) for a, m in zip(lods, masks)]; fan = [piece(a, m) for a, m in zip(lods, masks)]
    write_lod(out / f'{kind}_tower.dae', [((mat, (0,)),) + tower[0], ((mat, (1, FARCUT)),) + tower[1]], np.zeros(3), w['tower_lod'], 2)
    # The fan shape is centred on the hub, with its axis along Y
    def centred(g):
        p, n, uv, tri, u2 = g
        return p - hub, n, uv, tri, u2
    for v, seconds in enumerate(w['turns']):
        path = out / f'{kind}_fan_{v}.dae'
        write_lod(path, [((mat, (0,)),) + centred(fan[0]), ((mat, (1,)),) + centred(fan[1])], np.zeros(3), w['fan_lod'], None)
        animate(path, spin(seconds))
    places = []
    for r in records(w['group']):
        Rm = yaw_matrix(r); places.append((np.array([r[12] - CX, r[14] + CZ, r[13] + LIFT]) - Rm @ origin, Rm))
    for o in world:
        if o.get('shapeName', '').endswith(f"/{w['shape']}.dae") or o.get('shapeName', '').endswith(f'/{kind}_tower.dae'):
            o['shapeName'] = f'{rel}/{kind}_tower.dae'
            places.append((None, np.array(o['rotationMatrix']).reshape(3, 3).T, np.array(o['position'])))
    rng = np.random.default_rng(1)
    for i, place in enumerate(places):
        Rm = place[1]; pos = place[0] if place[0] is not None else place[2]
        if place[0] is not None:
            rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_tower_{i}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}_tower.dae', 'position': [round(float(v), 3) for v in pos],
                         'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'Collision Mesh', 'decalType': 'Collision Mesh'})
        at = pos + Rm @ hub
        rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_fan_{i}', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}_fan_{int(rng.integers(len(w['turns'])))}.dae', 'position': [round(float(v), 3) for v in at],
                     'rotationMatrix': [round(float(v), 5) for v in Rm.T.ravel()], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
    print(kind, len(places), 'placed', [[round(float(v)) for v in (p[0] if p[0] is not None else p[2])] for p in places][:8])
def lua_quat(R):
    """rotations (n, 3, 3) as BeamNG's LuaQuat x, y, z, w, kept on one side so Lua can blend neighbours. LuaQuat * v turns v by the conjugate of the
    usual quaternion (mathlib.lua), so this is the conjugate of the rotation's own"""
    q = Rotation.from_matrix(R).as_quat() * [-1, -1, -1, 1]
    for k in range(1, len(q)):
        if q[k] @ q[k - 1] < 0:
            q[k] = -q[k]
    return q


def mover_keys(p, R):
    """Lua's keys for a mover: x, y, z and its LuaQuat per key"""
    return np.round(np.c_[p, lua_quat(R)], 4).tolist()


def windows(t, on):
    """[from, to] spans of t where on holds"""
    e = np.flatnonzero(np.diff(np.r_[0, on.astype(int), 0]))
    return [[round(float(t[a]), 2), round(float(t[min(z, len(t) - 1)]), 2)] for a, z in zip(e[::2], e[1::2])]


def vehicle_fx(b, p0):
    """A vehicle's effect bones in its shape's frame: [(effect, offset, direction)]. granny.vertices gives the ANIM mesh with Granny x and +-z as x and y
    and height as z; the sign is the one that puts the wheel bones among the mesh's vertices. The catalog mesh the shape is made of is that mesh, turned
    half round about up or not (the combine is), and moved: both are fitted. Checked against the combine's reel, which lands 0.1 m from its hub"""
    tagged = sorted({x.decode() for x in re.findall(rb'Particles emitterType=(AMB_[A-Za-z0-9_]+)', b)})
    bones = [(bn['Name'], np.linalg.inv(np.array(bn['InverseWorldTransform'], 'f8').reshape(4, 4))) for s in granny2.File(b).tree()['Skeletons'] for bn in s['Bones']]
    a_ = granny.vertices(b)[2]; a_ = a_[np.isfinite(a_).all(1)]; wheels = [W[3, :3] for n, W in bones if 'Wheel' in n]
    L = max((np.array([[1, 0, 0], [0, 0, s_], [0, 1, 0.]]) for s_ in (1, -1)), key=lambda L_: sum(int((np.linalg.norm(a_ - L_ @ w_, axis=1) < 1).sum()) for w_ in wheels))
    fits = []
    for R in (np.eye(3), np.diag([-1., -1, 1])):
        r = a_ @ R.T; t = (p0.min(0) + p0.max(0)) / 2 - (r.min(0) + r.max(0)) / 2
        fits.append((float(np.median(cKDTree(p0).query(r + t)[0])), R, t))
    err, R, t = min(fits, key=lambda f: f[0]); M = R @ L; out = []
    for n, W in bones:
        end = next((k for k in VEHICLE_FX if n.endswith(k)), None)
        eff = next((e for e in tagged if end and e.endswith(VEHICLE_FX[end])), None)
        if eff:
            y = W[1, :3] / np.linalg.norm(W[1, :3])           # rows are the bone's axes; effects eject along y
            out.append((eff, M @ W[3, :3] + t, M @ y))
    print('  effects', [(e, np.round(o, 2).tolist()) for e, o, _ in out], 'mesh fit', round(err, 3), 'm')
    return out


def tag_events(b):
    """the sound events an ANIM model's tags name: [(event, oneshot)]"""
    return sorted({(e.decode(), o == b'True') for e, o in re.findall(rb'eventfile=([A-Za-z0-9_]+) oneshot=(True|False)', b)})


def inside(r):
    """whether a placement record stands in this level's square"""
    return abs(r[12] - CX) < HALF and abs(r[14] + CZ) < HALF


for kind, w in VEHICLES.items():
    if not any(inside(r) for r in records(w['group'])):         # a chunk such as Red Rock has none of these
        print(kind, 'not in this level')
        continue
    groups = mesh_groups(w['mesh']); p0 = groups[0][1]; b = gamezip.read('animatedobjects.zip', w['model'])
    if 'front' in w:
        forward = w['front']; t, c = quantised_route(b)
        times = np.arange(0, t[-1], w['key']); u = np.c_[np.interp(times, t, c[:, 0]), np.interp(times, t, c[:, 2])]
        # The same quarter turn as the tractor's. The offset of the route in the placement's frame is fitted to the bounding box in the model's header
        u = np.c_[-u[:, 1], u[:, 0]]; u += fit_box(u, p0, np.frombuffer(b[0x10:0x2c], '>f4').astype('f8'))
    else:
        t, c = route(w['model'])
        # Granny axes are x, up, z. The big wheels show which end of the mesh is the rear, so which way it drives
        rear = np.sign(p0[p0[:, 1] > 0, 2].size and (p0[(p0[:, 1] > .7) & (np.abs(p0[:, 0]) > .8), 2].max() - p0[(p0[:, 1] < -.7) & (np.abs(p0[:, 0]) > .8), 2].max()))
        forward = -1 if rear > 0 else 1
        # The ANIM model stands away from its origin, and the route starts from there
        a_ = granny.vertices(b)[2][:, :2]
        start = (np.nanmin(a_, 0) + np.nanmax(a_, 0)) / 2 - (p0[:, :2].min(0) + p0[:, :2].max(0)) / 2
        times = np.arange(0, t[-1], KEY_SECONDS); u = np.c_[np.interp(times, t, c[:, 0]), np.interp(times, t, c[:, 2])] + start
        # The route is a quarter turn from the placement's frame: Granny x, z becomes -z, x. This fits the bounding boxes in the sets' headers, and puts the laps on fields
        u = np.c_[-u[:, 1], u[:, 0]]
    times = np.r_[times, t[-1]]; u = np.r_[u, u[:1]]
    parts = []
    for name, part in w.get('parts', {}).items():
        hub = np.array(part['hub']); turns = round(t[-1] / part['seconds'])
        parts.append((name, hub, mesh_groups((part['mesh'],)), 360 * turns * times / t[-1]))
    fx = vehicle_fx(b, p0)
    # One shape of the body for every placement, and one of each spinning part, which spins about its own x in place. fh1Extras.lua moves them along
    # FH1's route, tilted to the ground under each placement, from the keys in fh1_movers.json, and the sprays with them
    write_lod(out / f'{kind}.dae', groups, np.zeros(3), w['lod'], None)
    for name, hub, pg, spin_ in parts:
        write_lod(out / f'{kind}_{name}.dae', [(g[0], g[1] - hub) + tuple(g[2:]) for g in pg], np.zeros(3), w['lod'], None)
        animate(out / f'{kind}_{name}.dae', [('rotationX.ANGLE', 'ANGLE', times, spin_)])
    key = w.get('key', KEY_SECONDS); grid = np.linspace(0, t[-1], int(round(t[-1] / key)) + 1)
    rng = np.random.default_rng(len(movers))
    for i, r in enumerate(records(w['group'])):
        pos = np.array([r[12] - CX, r[14] + CZ, r[13] + LIFT])
        xy = pos[:2] + u[:, :1] * [r[0], r[2]] + u[:, 1:] * [r[8], r[10]]
        d = np.gradient(xy, times, axis=0); moving = np.hypot(*d.T) > STOPPED
        yaw = np.degrees(np.arctan2(-d[:, 0] * forward, d[:, 1] * forward))
        for k in range(len(yaw)):
            if not moving[k]:
                yaw[k] = yaw[k - 1] if k else yaw[moving][0]
        yaw = np.degrees(np.unwrap(np.radians(yaw)))
        T = ground_triangles(xy.min(0) - 8, xy.max(0) + 8); z, pitch, roll = on_ground(T, xy, yaw)
        x, y = xy.T - pos[:2, None]; z = z - pos[2]
        # the route on Lua's even keys. The yaw is unwrapped, so it blends through the short way
        kp = np.c_[[np.interp(grid, times, v) for v in (x, y, z)]].T + pos; R = np.array([turn(*a) for a in zip(*(np.interp(grid, times, v) for v in (yaw, roll, pitch)))])
        go = np.interp(grid, times, moving.astype(float)) > .5
        # A spinning part turns about the vehicle's x, so it takes the vehicle's turn and sits at the hub
        # name, offset, shape: fh1Extras.lua creates these TSStatics, as stock creates the ones it moves (MOVERS)
        objects = [[f'fh1_{kind}_{i}', 0, 0, 0, f'{rel}/{kind}.dae']] + [[f'fh1_{kind}_{name}_{i}'] + [round(float(v), 3) for v in hub] + [f'{rel}/{kind}_{name}.dae'] for name, hub, _, _ in parts]
        movers.append({'name': f'{kind}_{i}', 'period': round(float(t[-1]), 3), 'phase': round(float(rng.uniform(0, t[-1])), 2), 'keys': mover_keys(kp, R), 'objects': objects,
                       'fx': [[e, *np.round(o, 3).tolist(), *np.round(d, 4).tolist(), windows(grid, go)] for e, o, d in fx], 'fxDist': MOVER_FX_DIST})
        print(kind, i, [round(float(v)) for v in pos], 'ground follows', round(float(np.ptp(z)), 1), 'm, max pitch', round(float(np.abs(pitch).max()), 1), 'max roll', round(float(np.abs(roll).max()), 1))
g = ground_samples(); mats_ = {}
for kind, w in FLYERS.items():
    if not inside(records(w['group'])[0]):
        print(kind, 'not in this level')
        continue
    q, tri, uv = granny.mesh(w['model']); t, c = route(w['model'])
    path = out / f'{kind}.dae'; mat = f'fh1_{kind}'
    write_lod(path, [((mat, (0, 1, FARCUT)), q, granny.smooth_normals(q, tri), uv, tri.ravel(), np.zeros((len(q), 2)))], np.zeros(3), w['lod'], None)
    a_, t_ = tex.load(w['texture']); tname = f'anim{w["texture"]:X}'      # in art/textures, so grade.py grades it with the rest
    dds.save(level / 'art' / 'textures' / f'{tname}.color.dds', a_, tex.blocks(w['texture']) if t_['fmt'] == 0x52 else None)
    r = records(w['group'])[0]; pos = np.array([r[12] - CX, r[14] + CZ, r[13] + LIFT])
    times, p, yaw, pitch, bank = flight(t, level_route(c))
    near = cKDTree(g[:, :2]).query_ball_point(pos[:2] + p[:, :2], GROUND_RADIUS)
    lift = max(g[i, 2].max() - pos[2] - p[k, 2] for k, i in enumerate(near) if i) + CLEARANCE; p[:, 2] += lift
    lo = p.min(0) - 8; hi = p.max(0) + 8
    animate(path, [('location.X', 'X', times, p[:, 0]), ('location.Y', 'Y', times, p[:, 1]), ('location.Z', 'Z', times, p[:, 2]), ('rotationZ.ANGLE', 'ANGLE', times, yaw),
                   ('rotationY.ANGLE', 'ANGLE', times, bank), ('rotationX.ANGLE', 'ANGLE', times, pitch)], bounds(lo, hi))
    mats_[mat] = {'name': mat, 'mapTo': mat, 'class': 'Material', 'version': 1.5, 'doubleSided': True,
                  'Stages': [{'baseColorMap': f'/levels/{LVL}/art/textures/{tname}.color.dds', 'roughnessFactor': .55, 'metallicFactor': 0}]}
    rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}_0', '__parent': 'fh1_anim', 'shapeName': f'{rel}/{kind}.dae', 'position': [round(float(v), 3) for v in pos],
                 'rotationMatrix': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True})
    world_p = pos + p
    print(kind, len(q), 'verts', len(tri), 'tris, lift', round(float(lift), 1), 'x', np.round([world_p[:, 0].min(), world_p[:, 0].max()]).tolist(), 'y', np.round([world_p[:, 1].min(), world_p[:, 1].max()]).tolist(),
          'z', np.round([world_p[:, 2].min(), world_p[:, 2].max()]).tolist(), 'max bank', round(float(np.abs(bank).max())), 'max pitch', round(float(np.abs(pitch).max())))
IDX = pickle.load(open('../texshader.pkl', 'rb'))[0]; saved = set()
for kind, model in {**RIDES, **STANDALONE}.items():
    recs = [r for r in (STANDALONE_AT[kind] if kind in STANDALONE else placed(model)) if inside(r)]
    if not recs:
        print(kind, 'not in this level')
        continue
    if kind in STANDALONE:
        b = animcopy.standalone(model); tl = animcopy.texture_names(b)
    else:
        b = animcopy.find(model); tl = animcopy.textures(b)
    ms = animcopy.meshes(b, flags=kind in RIGS); animcopy.bind(b, ms)
    # triangles of each bone, by material, from the most detailed meshes. A skinned triangle goes with its first vertex's bone
    parts = {}; lit_ = False
    for m in ms:
        if animcopy.lod(m, ms) or m['name'] in SKIP_MESHES:
            continue
        tri_bone = m['bones'][m['idx'][:, 0]] if m['skin'] else np.full(m['T'], m['bone'])
        for slots, first, count in m['segs']:
            mat = ride_material(tl, slots[0], saved, slots[2], kind in RIGS); lit_ = lit_ or '_g' in mat
            for k in np.unique(tri_bone[first:first + count]):
                t_ = m['idx'][first:first + count][tri_bone[first:first + count] == k]
                parts.setdefault((m['model'], int(k)), []).append((mat, m, t_))
    dur = animcopy.duration(b); times = np.r_[np.arange(0, dur - KEY_MIN / 2, KEY_MIN), dur]; W, B_, _ = animcopy.poses(b, times)
    # Granny x, up, z -> BeamNG x, y, up, as an unturned placement converts. That swap mirrors, so faces are turned back.
    # Every part goes in one shape, a write_lod group per material, and each moving part's groups get its own keys
    S = np.eye(4)[[0, 2, 1, 3]]; groups = []; curves = {}; swept = []; n_moving = 0; n_keys = []; route = np.zeros((0, 3))
    Ts = {sk: S @ W[sk[0]][sk[1]] @ np.linalg.inv(B_[sk[0]][sk[1]]) @ S for sk in parts}
    moving_ = lambda T: np.abs(T[:, :3, 3] - T[:1, :3, 3]).max() > STILL[0] or np.degrees((Rotation.from_matrix(T[:, :3, :3]) * Rotation.from_matrix(T[0, :3, :3]).inv()).magnitude()).max() > STILL[1]
    size_ = lambda sk: sum(len(np.unique(t_)) for _, _, t_ in parts[sk])
    # A mover's body is its largest moving part. Its shape holds the other parts' motion relative to the body, and Lua carries the body
    root = max((sk for sk in parts if moving_(Ts[sk])), key=size_, default=None) if kind in MOVERS else None
    for (s, k), pieces in sorted(parts.items()):
        T0 = Ts[(s, k)]; T = np.linalg.inv(Ts[root]) @ T0 if root else T0
        own = []
        for mat, m, t_ in pieces:
            used, inv = np.unique(t_, return_inverse=True); q = (S[:3, :3] @ m['pos'][used].T).T; t_ = inv.reshape(-1, 3)[:, ::-1]
            own.append(((mat, (0, 1, FARCUT)), q, granny.smooth_normals(q, t_), m['uv'][used], t_.ravel(), np.zeros((len(q), 2))))
        p = np.concatenate([g[1] for g in own])
        if len(p) > len(route) and moving_(T0):
            route = p.mean(0) @ T0[:, :3, :3].transpose(0, 2, 1) + T0[:, :3, 3]
        moves = moving_(T)
        if not moves or (np.ptp(p, 0).max() < TINY and kind not in SMALL_PARTS):
            groups += [(g[0], (T[0, :3, :3] @ g[1].T).T + T[0, :3, 3], (T[0, :3, :3] @ g[2].T).T) + g[3:] for g in own]
            swept.append((T[0, :3, :3] @ p.T).T + T[0, :3, 3])
            continue
        box = np.array(list(itertools.product(*zip(p.min(0), p.max(0))))); swept.append((np.einsum('nij,kj->nki', T[:, :3, :3], box) + T[:, None, :3, 3]).reshape(-1, 3))
        ix, order, a = keys(T, box); n_keys.append(len(ix)); n_moving += 1
        c = ([(f'location.{c_}', c_, times[ix], T[ix, n, 3]) for n, c_ in enumerate('XYZ')] + [(f'rotation{c_}.ANGLE', 'ANGLE', times[ix], a[:, n]) for n, c_ in enumerate(order)], order)
        curves.update({len(groups) + n: c for n in range(len(own))}); groups += own
    swept = np.concatenate(swept); c = (swept.min(0) + swept.max(0)) / 2
    sizes_ = ride_sizes(np.linalg.norm(np.concatenate([g[1] for g in groups]) - c, axis=1).max())
    # A light rig's beams and glows (the '_add' sprites) go in their own shape, kind_beams, standing in fh1_rig_beams, which fh1Extras.lua hides by day as it
    # hides the night overlays: drawn by day, they showed as pale rays over the main stage although their emissive was off
    beams = [n for n, g_ in enumerate(groups) if kind in RIGS and g_[0][0].endswith('_add')]
    glows = any('_g' in g_[0][0] for n, g_ in enumerate(groups) if n not in beams)       # bulbs and lamp lenses lit by a glow mask
    shapes_ = [('', 'fh1_anim', glows)] + ([('_beams', 'fh1_rig_beams', True)] if beams else [])
    for suffix, keep in (('', [n for n in range(len(groups)) if n not in beams]), ('_beams', beams)):
        if not keep:
            continue
        path = out / f'{kind}{suffix}.dae'
        write_lod(path, [groups[n] for n in keep], np.zeros(3), sizes_, None)
        cv = {j: curves[n] for j, n in enumerate(keep) if n in curves}
        if cv:
            animate(path, cv, bounds(swept.min(0) - 2, swept.max(0) + 2))
    if root:
        # the body on Lua's even keys, and the effect bones on it ('L_AMB_CropDuster_Spray'). Effects eject along their bone's y axis, which in BeamNG axes is the bone matrix's z column
        grid = np.linspace(0, dur, int(round(dur / MOVER_KEY)) + 1); W2, B2, _ = animcopy.poses(b, grid)
        body = S @ W2[root[0]][root[1]] @ np.linalg.inv(B2[root[0]][root[1]]) @ S; fx = []
        for s, sk in enumerate(granny2.File(b).tree()['Skeletons']):
            for k, bn in enumerate(sk['Bones']):
                if 'AMB_' not in bn['Name']:
                    continue
                rb_ = np.linalg.inv(body) @ (S @ W2[s][k] @ S); sc = np.abs(np.linalg.det(W2[s][k][:, :3, :3])) ** (1 / 3)
                # The duster's bones rest at scale 0.18 and swell for about a second three times a lap: the bursts. The plain spray runs throughout
                on = sc > (sc.min() + sc.max()) / 2 if sc.max() > sc.min() * 1.2 and 'Burst' in bn['Name'] else np.ones(len(grid), bool)
                d = np.median(rb_[on, :3, 2], 0)
                fx.append([bn['Name'][bn['Name'].index('AMB_'):], *np.round(np.median(rb_[on, :3, 3], 0), 3).tolist(), *np.round(d / np.linalg.norm(d), 4).tolist(), windows(grid, on)])
        print('  ', kind, 'moved by Lua,', len(grid), 'keys, effects', [(f[0], f[1:4], len(f[7])) for f in fx])
    for i, r in enumerate(recs):
        # The placement's turn in BeamNG axes: its records convert as S R^T, which is this turn after S
        Rm = S[:3, :3] @ np.array([r[0:3], r[4:7], r[8:11]]).T @ S[:3, :3]; pos = np.array([r[12] - CX, r[14] + CZ, r[13] + LIFT])
        if kind in FLIGHTS:
            w_ = pos + route @ Rm.T; near = cKDTree(g[:, :2]).query_ball_point(w_[:, :2], GROUND_RADIUS)
            low = min(w_[k, 2] - g[i_, 2].max() for k, i_ in enumerate(near) if i_)
            pos[2] += max(FLIGHT_CLEARANCE - low, 0)
            print(kind, i, 'lowest', round(float(low), 1), 'm over the ground within', GROUND_RADIUS, 'm, lifted', round(float(max(FLIGHT_CLEARANCE - low, 0)), 1))
        at_, R_ = pos, Rm
        if root:
            kp = pos + body[:, :3, 3] @ Rm.T; kR = Rm @ body[:, :3, :3]; at_, R_ = kp[0], kR[0]
            sound = soundscape.trigger(MOVER_SOUNDS[kind]) if kind in MOVER_SOUNDS else None
            movers.append({'name': f'{kind}_{i}', 'period': round(float(dur), 3), 'phase': round(float(np.random.default_rng(i).uniform(0, dur)), 2), 'keys': mover_keys(kp, kR),
                           'objects': [[f'fh1_{kind}_{i}', 0, 0, 0, f'{rel}/{kind}.dae']], 'fx': fx, 'fxDist': MOVER_FX_DIST,
                           **({'sound': {'file': sound['files'][0]['file'], 'near': sound['near'], 'far': sound['far'], 'volume': sound['volume']}} if sound else {})})
            continue
        for ev in tag_events(b):
            ride_sounds.setdefault(ev, []).append([round(float(pos[0]), 1), round(float(pos[1]), 1)])
        for suffix, parent, lit in shapes_:
            rows.append({'class': 'TSStatic', 'name': f'fh1_{kind}{suffix}_{i}', '__parent': parent, 'shapeName': f'{rel}/{kind}{suffix}.dae', 'position': [round(float(v), 3) for v in at_],
                         'rotationMatrix': [round(float(v), 5) for v in R_.T.ravel()], 'collisionType': 'None', 'decalType': 'None', 'useInstanceRenderData': True,
                         **({'instanceColor': [0, 0, 0, 1], 'nightEmissive': '1'} if lit else {}),
                         # Beams start hidden and fh1Extras.lua shows them at night: shown by default, they stayed as grey rays by day whenever the script didn't run
                         **({'hidden': True} if parent == 'fh1_rig_beams' else {})})
    print(kind, len(recs), 'placed', [[round(float(v)) for v in (r[12] - CX, r[14] + CZ)] for r in recs][:4], len(parts), 'parts,', n_moving, 'moving,',
          sum(len(t_) for ps in parts.values() for _, _, t_ in ps), 'triangles,', sum(n_keys), 'keys over', round(dur, 1), 's')
(out / 'main.materials.json').write_text(json.dumps(mats_))
# Sounds the rides' tags name, as fh1Extras.lua's placed loops and one-shots, and the movers, which fh1Extras.lua carries along their keys.
# particles.py adds the movers' emitter nodes
loops = []; shots = []
for (ev, oneshot), pts in sorted(ride_sounds.items()):
    if ev not in soundscape.TRIGGERS:          # timed to the ride's motion, or not in the bank
        continue
    t_ = soundscape.trigger(ev); pts = [list(p_) for p_ in sorted({tuple(p_) for p_ in pts})]
    if not oneshot:
        loops.append({'file': t_['files'][0]['file'], 'near': t_['near'], 'far': t_['far'], 'volume': t_['volume'], 'points': pts})
    elif ev in RIDE_SHOTS:
        shots.append({'files': t_['files'], 'near': t_['near'], 'far': t_['far'], 'volume': t_['volume'], 'wait': list(RIDE_SHOTS[ev]), 'points': pts})
print('ride sounds:', [(Path(l_['file']).stem, len(l_['points'])) for l_ in loops], 'one-shots', [(Path(s_['files'][0]['file']).stem, len(s_['points'])) for s_ in shots],
      'movers', len(movers), 'with', sum(len(m_['fx']) for m_ in movers), 'effects')
(level / 'fh1_movers.json').write_text(json.dumps({'loops': loops, 'shots': shots, 'movers': movers}))
moved = {o['name']: o['shapeName'] for o in world if o.get('shapeName') != was.get(o.get('name'))}
with mission.edit(mg / 'fh1' / 'items.level.json') as r_:       # water.py edits this file too, while this runs: only the shapes this swapped change
    for o in r_:
        if o.get('name') in moved:
            o['shapeName'] = moved[o['name']]
for grp in ('fh1_anim', 'fh1_rig_beams'):
    (mg / grp).mkdir(exist_ok=True)
    (mg / grp / 'items.level.json').write_text(''.join(json.dumps(o) + '\n' for o in rows if o['__parent'] == grp))
mission.add_groups(items, 'fh1_anim', 'fh1_rig_beams')
(level / 'fh1_animated.json').unlink(missing_ok=True)
if (level / 'art' / 'textures_ungraded').exists():       # grade.py already ran: grade the new textures too. It grades from ungraded copies, so rerunning is safe
    subprocess.run([sys.executable, 'grade.py', LVL], check=True)
print('done', len(rows), 'objects ->', level)
