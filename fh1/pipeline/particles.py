"""FH1's ambient particles for a built level: water, smoke, falling leaves, road dust, campfires, lanterns and bird flocks. effects.zip becomes BeamNG particle datablocks, ParticleEmitters.xml becomes nodes, which fh1Extras.lua wakes near the player.
Run after levelinfo.py: python particles.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, os, re, struct, sys, zlib
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import animcopy, frame, gamezip, granny2, mission, soundscape, tex
from scipy.spatial.transform import Rotation
from animsets import placed as anim_placed

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = frame.LEVELS[LVL]; LIFT = frame.LIFT
WATER = re.compile(r'AMB_(WaterFall_|Dam_Rising_Mist|Geyser_Mist|FountainWater)')
SMOKE = re.compile(r'AMB_(HouseSmokeVent|Chimney_Smoke_White|Ind_SmokeVent|Small_Steam_Vent)')
# How far away an emitter wakes, in metres. Water is further because falls are seen across valleys
WATER_FAR = 1500; SMOKE_FAR = 700
# Effects that ANIM models carry, with the height of the nozzle above the model's origin and the wake distance their models give (maxDist).
# FH1 turns a sprinkler's jet with its animation. Here the emitter turns it, at a rate chosen by eye, at an elevation also chosen by eye
ANIMATED = {'ANIM_WaterSprinkler': ('AMB_WaterSprinkler', 1.0, 200), 'ANIM_WaterSprinklerSmall': ('AMB_WaterSprinklerSmall', 1.0, 200)}
JET_THETA = 60; JET_TURN = {'AMB_WaterSprinkler': 40, 'AMB_WaterSprinklerSmall': 90}
# FH1's alpha suits its own soft particle shader, and is too faint in BeamNG. Chosen by eye, like the angles
ALPHA = 1.5; SPRAY_ANGLE = 60; BOX_ANGLE = 180
# Metres over which a particle fades into scenery. Mist quads are up to 24 m wide, and cut hard lines into rock without this
SOFT = 4
ROOT = f'/levels/{LVL}/art/particles'
# Ambient effects, which follow more of FH1's data than water and smoke: direction, spray cone, budget, blend, flipbooks and time of day.
# Wake distances in metres, chosen for how far each can be seen. _Composite_ entries are expanded into the effects they list
AMBIENT = [(re.compile(r'AMB_Falling_Leaf_'), 250), (re.compile(r'AMB_Road_Dust$'), 400), (re.compile(r'_Composite_CampFire$'), 800), (re.compile(r'AMB_Lantern'), 2500),
           (re.compile(r'_Composite_(Fountain|Ind_Chimney_Smoke)$'), 1500), (re.compile(r'AMB_Birds_Flocking'), 600), (re.compile(r'AMB_Balloon'), 1500),
           (re.compile(r'AMB_Camera_Flash$'), 400), (re.compile(r'_Composite_Flare(_\d)?$'), 800), (re.compile(r'_Composite_(MainStage|SubStage)$'), 1500)]
# Rate factors, chosen by eye. Bird flocks were left out once, when the user found them out of bounds; since placements were fixed they stand 10 to
# 80 m from roads. At FH1's rates the reservoir's 98 flocks hold about 8000 birds and would take the whole particle budget
THIN = [(re.compile(r'AMB_Birds_Flocking'), .25)]
# ANIM models whose bones carry effects instead of meshes: model -> (wake distance, time window or None). A bone's scale switches its effect on, and
# the bone may move, as a firework's trail climbs 53 m. fh1Extras.lua switches and moves each placement's nodes on the model's loop, at a random phase.
# FH1's effects give the fireworks no time window; night is chosen by eye, like the stage composites' 18:40 to 7:00
SEQUENCES = {'ANIM_Geyser': (2000, None), 'ANIM_Firework_': (3000, [19.0, 6.0]), 'ANIM_Firework_001_Green': (3000, [19.0, 6.0]),
             'ANIM_Firework_001_SmallSparkle': (3000, [19.0, 6.0])}
# A bone's effect is the shortest effect named in the model that starts with this: the green firework names AMB_FireworkInitialGlowGreen
SEQUENCE_EFFECTS = {'01_Firework_Trail': 'AMB_FireworkTrail', '01_Firework_TrailSmoke': 'AMB_FireworkTrailSmoke', '01_Firework_TrailSparkle': 'AMB_FireworkTrail_Sparkle',
                    '02_Firework_Flare': 'AMB_FireworkFlare', '02_Firework_FlareSmoke': 'AMB_FireworkSmoke', '02_Firework_FlareInitialGlow': 'AMB_FireworkInitialGlow',
                    '02_Firework_FlareChildTrails': 'AMB_FireworkChildInitial', '02_Firework_GlistenBurst': 'AMB_Firework_Glisten',
                    '02_Firework_SparkleSmall': 'AMB_FireworkSparkleSmall'}
SEQUENCE_KEY = .1           # seconds between path keys of a moving node
# Performance, after the user saw fps drop with the ambient batch: particles at most SMALL metres wide, and additive ones, skip sorting and soft
# blending, which change nothing visible for them
SMALL = 1.0
# BeamNG's longest ejection period, in ms. Rarer FH1 emitters, such as the lanterns, emit more often than in FH1
MAX_PERIOD = 2047
# Most frames one emitter picks from. BeamNG drops an emitter whose particle list passes 255 characters
MAX_TILES = 16


def effect(name):
    return ET.fromstring(gamezip.read('effects.zip', name + '.xml').decode('latin1').strip())


def attr(e, path, key, default=0.0):
    x = e.find(path)
    return float(x.get(key, default)) if x is not None else default


def texture(name, frames, out, cols=1):
    """DXT5 top mip as a .dds, keeping FH1's own blocks. These sit tiled and byte swapped in .xds files. Format 0x75 is DXT5: read as DXT3 the alpha turns to static."""
    b = next(gamezip.read('effects.zip', n) for n in EFFECT_FILES if n.lower() == name.lower())
    size = struct.unpack('>I', b[0x24:0x28])[0]; w = (size & 0x1fff) + 1; h = ((size >> 13) & 0x1fff) + 1
    bw, bh = (w + 3) // 4, (h + 3) // 4
    blocks = np.frombuffer(tex.untile(b[52:], bw, bh, 16), '>u2').astype('<u2').tobytes()
    # Frames sit on a grid of whole blocks, so they can be cut apart without re-encoding. Tiles run left to right, then down
    row = bh // frames; col = bw // cols; size = col * row * 16; grid = np.frombuffer(blocks, 'u1').reshape(bh, bw, 16)
    hdr = b'DDS ' + struct.pack('<7I', 124, 0x81007, row * 4, col * 4, size, 0, 1) + bytes(44)
    hdr += struct.pack('<2I4s5I', 32, 4, b'DXT5', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x1000, 0, 0, 0, 0)
    stem = Path(name).stem.lower(); n = frames * cols
    for i in range(n):
        r, c = divmod(i, cols)
        (out / f'{stem}_{n}_{i}.color.dds').write_bytes(hdr + grid[r * row:(r + 1) * row, c * col:(c + 1) * col].tobytes())


def datablocks(name):
    e = effect(name); props = e.find('EmitterProperties'); kind = props.get('type')
    box = props.find('Size[@length]'); sz = props.find('Size[@valueX]')
    life = attr(props, 'Life', 'value', 1); life_var = attr(props, 'Life', 'variation'); rate = max(attr(props, 'Quantity', 'value', 1), 1)
    speed = attr(props, 'Velocity', 'value'); speed_var = attr(props, 'Velocity', 'variation')
    grav = attr(e, 'Behavior/Gravity', 'strength')
    mat = e.find('Render/ParticleType/Material')
    a0, a1, a2 = (float(mat.find(k).get('a')) * ALPHA for k in ('RGBStart', 'RGBMid', 'RGBEnd'))
    fade_in = np.clip(attr(mat, 'FadeInTo', 'fadeInTo', .2), .01, .98); fade_out = np.clip(attr(mat, 'FadeOutFrom', 'fadeOutFrom', .6), fade_in + .01, .99)
    rgb = [float(mat.find('RGBMid').get(c)) for c in 'rgb']
    start = float(sz.get('valueX')); peak = max(float(sz.get('maxValueX')), start)
    spin = attr(props, 'SpinTime', 'value', 0)
    img = Path(e.find('Render/ParticleType/Texture').get('value')).stem.lower()
    frames = int(float(e.find('.//Animation').get('numFramesY', 1))) if e.find('.//Animation') is not None else 1
    jet = name in JET_TURN
    theta = BOX_ANGLE if kind == 'Box' else JET_THETA + 2 if jet else SPRAY_ANGLE
    offset = float(box.get('length', 0)) / 2 if box is not None else 0
    p = {'class': 'ParticleData',
         'lifetimeMS': int(life * 1000), 'lifetimeVarianceMS': int(min(life * life_var, life * .9) * 1000),
         'colors': [rgb + [a0], rgb + [a1], rgb + [a1], rgb + [a2]], 'sizes': [start, (start + peak) / 2, peak, peak], 'times': [0, float(fade_in), float(fade_out), 1],
         'gravityCoefficient': grav / 981, 'dragCoefficient': .3, 'inheritedVelFactor': 0, 'useInvAlpha': True,
         'spinRandomMin': -30, 'spinRandomMax': 30, 'spinSpeed': 1 / spin if spin else 0, 'spinInitialMax': 360}
    # BeamNG has no use of frame animation in stock particles, so each frame is its own particle and the emitter picks at random
    parts = [{**p, 'name': f'fh1p_{name}_{i}', 'textureName': f'{ROOT}/{img}_{frames}_{i}.color.dds', 'animTexName': f'{ROOT}/{img}_{frames}_{i}.color.dds'} for i in range(frames)]
    em = {'name': f'fh1e_{name}', 'class': 'ParticleEmitterData', 'particles': ' '.join(q['name'] for q in parts), 'blendStyle': 'NORMAL', **timing(rate, speed, speed_var),
          'ejectionOffset': offset, 'ejectionOffsetVariance': offset, 'thetaMin': JET_THETA - 2 if jet else 0, 'thetaMax': theta,
          'phiVariance': 6 if jet else 360, 'phiReferenceVel': JET_TURN.get(name, 0), 'reverseOrder': False, **cheap(parts), 'softnessDistance': SOFT if WATER.match(name) else .85}
    return parts, em, (img, frames, 1)


def cheap(parts, additive=False):
    """Emitter fields that skip per-frame sorting and soft blending where they don't show."""
    skip = additive or max(max(q['sizes']) for q in parts) <= SMALL
    return {'sortParticles': not skip, 'softParticles': '0' if skip else '1'}


def timing(rate, speed, speed_var):
    """Emitter fields for FH1's rate (per second) and speed. BeamNG caps the period at 2047 ms and divides by period minus its variance, so the variance stays below it."""
    period = min(max(int(round(1000 / rate)), 1), MAX_PERIOD); speed = max(speed, 0)
    return {'ejectionPeriodMS': period, 'periodVarianceMS': period // 2, 'ejectionVelocity': speed, 'velocityVariance': max(min(speed * speed_var, speed * .9), 0)}


def hours(e):
    """FH1's time-of-day window in hours, or None for all day. The waterfalls' 0:00 to 0:00 is all day too: fh1Extras.lua would read it as never"""
    t = e.find('TimeOfDayData')
    s, f = (float(t.get(k + 'H')) + float(t.get(k + 'M')) / 60 for k in ('start', 'end')) if t is not None else (0, 24)
    return None if (s, f) == (0, 24) or s == f else [round(s, 2), round(f, 2)]


def ambient(name):
    """Datablocks for an ambient effect, its ejection direction drift from FH1's wind (m/s, effect axes) and its time window. None if it draws nothing."""
    e = effect(name); props = e.find('EmitterProperties'); kind = props.get('type'); mat = e.find('Render/ParticleType/Material')
    life = attr(props, 'Life', 'value', 1); life_var = attr(props, 'Life', 'variation')
    # FH1 never has more live particles than the effect's budget
    rate = min(attr(props, 'Quantity', 'value', 1), float(e.get('budget', 1e9)) / life) * next((f for r, f in THIN if r.match(name)), 1)
    speed = attr(props, 'Velocity', 'value'); speed_var = attr(props, 'Velocity', 'variation')
    sz = props.find('Size[@valueX]'); start = float(sz.get('valueX')); peak = max(float(sz.get('maxValueX')), start)
    if peak <= 0 or rate <= 0:
        return None
    # With no grow time FH1 draws particles at full size from the start
    sizes = [peak] * 4 if float(sz.get('growforX')) == 0 else [start, (start + peak) / 2, peak, peak]
    fade_in = np.clip(attr(mat, 'FadeInTo', 'fadeInTo', .2), .01, .98); fade_out = np.clip(attr(mat, 'FadeOutFrom', 'fadeOutFrom', .6), fade_in + .01, .99)
    rgba = [[float(mat.find(k).get(c)) for c in 'rgb'] + [min(float(mat.find(k).get('a')) * ALPHA, 1)] for k in ('RGBStart', 'RGBMid', 'RGBMid', 'RGBEnd')]
    additive = mat.find('BlendType').get('value') == 'additive'
    beh = e.find('Behavior'); fric = beh.find('Friction')
    # BeamNG drag pulls particles to the level's wind. FH1 only slows particles with Friction, and leaves flutter (BubbleMotion), read here as drag
    drag = float(fric.get('strength')) if fric is not None else 1.0 if beh.find('BubbleMotion') is not None else 0
    wind = beh.find('Wind')
    drift = np.array([float(wind.get('direction.' + c)) for c in 'xyz']) * float(wind.get('strength')) / 100 * life / 2 if wind is not None else np.zeros(3)
    anim = props.find('Animation'); fx, fy = (int(float(anim.get('numFramesX'))), int(float(anim.get('numFramesY')))) if anim is not None else (1, 1)
    img = Path(e.find('Render/ParticleType/Texture').get('value')).stem.lower(); spin = attr(props, 'SpinTime', 'value', 0)
    p = {'class': 'ParticleData', 'lifetimeMS': int(life * 1000), 'lifetimeVarianceMS': int(min(life * life_var, life * .9) * 1000),
         'colors': rgba, 'sizes': sizes, 'times': [0, float(fade_in), float(fade_out), 1],
         'gravityCoefficient': attr(e, 'Behavior/Gravity', 'strength') / 981, 'dragCoefficient': drag, 'inheritedVelFactor': 0, 'useInvAlpha': not additive,
         'spinRandomMin': -30, 'spinRandomMax': 30, 'spinSpeed': 1 / spin if spin else 0, 'spinInitialMax': 360}
    # BeamNG 0.40 crashes preloading particles with animateTexture: animTexTiling reads as 0 0 and the frame list divides by it.
    # So a flipbook's tiles, like rows of variants such as leaf shapes, each become a particle the emitter picks at random.
    # An emitter's particle list must fit in 255 characters, so tiles are capped at MAX_TILES, evenly spread, under short names
    cols = fx if fx * fy > 1 and 'rowonly' not in props.find('ParticleShaderType').get('value') else 1
    n = fy * cols; short = f'fh1q{zlib.crc32(name.encode()) & 0xffff:x}'
    parts = [{**p, 'name': f'{short}_{i}', 'textureName': f'{ROOT}/{img}_{n}_{i}.color.dds', 'animTexName': f'{ROOT}/{img}_{n}_{i}.color.dds'}
             for i in range(0, n, -(-n // MAX_TILES))]
    box = props.find('Size[@length]'); reach = max(abs(float(v)) for v in box.attrib.values()) / 2 if box is not None else 0
    if kind == 'Box':
        # A flat box, such as the lanterns' 180 x 20 x 180 m, becomes a slab around the horizontal, so none start underground
        flat = max(abs(float(box.get('length'))), abs(float(box.get('width'))))
        tilt = np.degrees(np.arctan2(abs(float(box.get('height'))), flat)) if flat > abs(float(box.get('height'))) else 90
        lo, hi = 90 - tilt, 90 + tilt
    elif kind == 'Plane':
        # BeamNG offsets particles along their ejection direction, so a near-flat cone with an offset fills the plane
        lo, hi = 70, 90
    else:
        # SuperSpray: the cone's centre angle and spread from the emitter's axis, in degrees
        c, s = abs(attr(props, 'OffAxis', 'value')), attr(props, 'OffAxis', 'variation'); lo, hi = max(c - s, 0), min(c + s, 180); reach = 0
    em = {'name': f'fh1ae_{name}', 'class': 'ParticleEmitterData', 'particles': ' '.join(q['name'] for q in parts), 'blendStyle': 'ADDITIVE' if additive else 'NORMAL',
          **timing(rate, speed, speed_var), 'ejectionOffset': reach / 2, 'ejectionOffsetVariance': reach / 2, 'thetaMin': lo, 'thetaMax': hi, 'phiVariance': 360,
          'reverseOrder': False, **cheap(parts, additive), 'softnessDistance': .85}
    return parts, em, (img, fy, cols), drift * (kind != 'Box'), hours(e)


def basis(d):
    """Rotation whose z, the axis BeamNG ejects along, is d. Columns are the node's axes."""
    z = d / np.linalg.norm(d); x = np.cross([0, 1, 0] if abs(z[0]) > .9 else [1, 0, 0], z); x /= np.linalg.norm(x)
    return np.c_[x, np.cross(z, x), z]


mods = Path(beamng.find_mods()) / 'unpacked' / LVL; level = mods / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
b_ = open(gamezip.MEDIA + 'effects.zip', 'rb').read()
i_ = b_.rfind(b'PK\x05\x06'); n_ = struct.unpack('<H', b_[i_ + 10:i_ + 12])[0]; o_ = struct.unpack('<I', b_[i_ + 16:i_ + 20])[0]; EFFECT_FILES = []
for _ in range(n_):
    nl, xl, cl = struct.unpack('<HHH', b_[o_ + 28:o_ + 34]); EFFECT_FILES.append(b_[o_ + 46:o_ + 46 + nl].decode('latin1')); o_ += 46 + nl + xl + cl

root = ET.fromstring(open(gamezip.MEDIA + 'tracks/colorado/Ribbon_00/ParticleEmitters.xml', 'rb').read().decode('utf-8-sig', 'ignore'))
placed = []
for e in root:
    eff = e.find('Effect'); pos = e.find('Position')
    if eff is None or pos is None or not (WATER.match(eff.get('value')) or SMOKE.match(eff.get('value'))):
        continue
    # Same z sign as meshes, so BeamNG y is -(z - CZ)
    x, y, z = float(pos.get('v.x')) - CX, -(float(pos.get('v.z')) - CZ), float(pos.get('v.y')) + LIFT
    if max(abs(x), abs(y)) < HALF:
        placed.append((eff.get('value'), x, y, z))
far = {}
for model, (name, height, dist) in ANIMATED.items():
    for r in anim_placed(model):
        # anim_proc sets keep the .pgeo z sign, unlike ParticleEmitters.xml
        x, y, z = r[12] - CX, r[14] + CZ, r[13] + height + LIFT
        if max(abs(x), abs(y)) < HALF:
            placed.append((name, x, y, z)); far[name] = dist


def v3(x):
    return np.array([float(x.get('v.' + c)) for c in 'xyz'])


def to_b(q):
    """FH1 axes (y up, the meshes' z sign) to BeamNG's, without the level offset."""
    return np.array([q[0], -q[2], q[1]])


# Ambient placements: (effect, BeamNG position, ejection axis, wake distance, time window). Emitters eject along their local y
scene = []
for e in root:
    eff = e.find('Effect'); pos = e.find('Position'); o = e.find('Orientation')
    dist = next((f for r, f in AMBIENT if r.match(eff.get('value'))), None) if eff is not None else None
    if dist is None or pos is None:
        continue
    X, Y, Z = (v3(o.find(k)) for k in ('XAxis', 'YAxis', 'ZAxis')) if o is not None else np.eye(3)
    name = eff.get('value'); when = None; subs = [(name, np.zeros(3))]
    if name.startswith('_Composite_'):
        # A composite lists its effects with offsets in the emitter's axes, and its own time window
        c = effect(name); when = hours(c); subs = [(s.find('FileName').get('name'), v3(s.find('PosOffset'))) for s in c.findall('Effect')]
    for sub, off in subs:
        w = to_b(v3(pos) + X * off[0] + Y * off[1] + Z * off[2]) - [CX, -CZ, -LIFT]
        if max(abs(w[0]), abs(w[1])) < HALF:
            scene.append((sub, w, to_b(Y), dist, when))

def sequence(model):
    """The effect bones of an ANIM model: (loop seconds, [(effect, on [(from, to)], times, positions (n, 3), axes (n, 3))]), in Granny axes"""
    b = animcopy.find(model); g = granny2.File(b).tree(); dur = animcopy.duration(b); t = np.arange(0, dur, 1 / 60); W, _, _ = animcopy.poses(b, t)
    named = sorted({x.decode() for x in re.findall(rb'AMB_[A-Za-z0-9_]+', b)}, key=len); out = []
    for s, sk in enumerate(g['Skeletons']):
        base = SEQUENCE_EFFECTS.get(sk['Name'], 'AMB_' + sk['Name']); eff = next((n for n in named if n.startswith(base)), None)
        if eff is None:
            continue
        M = W[s][0]; sc = np.abs(np.linalg.det(M[:, :3, :3])) ** (1 / 3); lo, hi = sc.min(), sc.max()
        if hi < lo * 1.2:
            continue
        e = np.flatnonzero(np.diff(np.r_[0, (sc > (lo + hi) / 2).astype(int), 0]))
        out.append((eff, [(float(t[a]), float(t[min(z, len(t) - 1)])) for a, z in zip(e[::2], e[1::2])], t, M[:, :3, 3], M[:, :3, 1]))
    return float(dur), out


sequences = []; seq_nodes = []
for model, (dist, when) in SEQUENCES.items():
    recs = [r for r in anim_placed(model) if max(abs(r[12] - CX), abs(r[14] + CZ)) < HALF]
    if not recs:
        continue
    dur, bones = sequence(model); rng = np.random.default_rng(len(sequences))
    for r in recs:
        # Granny axes to BeamNG through the placement, as animated.py converts ride records
        L = np.array([r[0:3], r[4:7], r[8:11]]).T[[0, 2, 1]]; pos = np.array([r[12] - CX, r[14] + CZ, r[13] + LIFT]); nodes_ = []
        for eff, on, t, p, y in bones:
            w = pos + p @ L.T; i = len(seq_nodes); mid = np.searchsorted(t, (on[0][0] + on[0][1]) / 2)
            seq_nodes.append((eff, w[mid], L @ y[mid]))
            # A node that moves while it's on gets a path, keyed every SEQUENCE_KEY seconds over its on times
            k = np.unique(np.concatenate([np.searchsorted(t, np.r_[np.arange(a, z, SEQUENCE_KEY), z]) for a, z in on]).clip(0, len(t) - 1))
            path = [[round(float(t[j]), 2)] + [round(float(v), 2) for v in w[j]] for j in k] if np.ptp(w[k], 0).max() > .5 else None
            nodes_.append([i, [[round(a, 2), round(z, 2)] for a, z in on], path])
        sequences.append({'model': model, 'x': round(float(pos[0]), 1), 'y': round(float(pos[1]), 1), 'dist': dist, 'period': round(dur, 2), 'phase': round(float(rng.uniform(0, dur)), 2),
                          'hours': when, 'nodes': nodes_})
    print(model, len(recs), 'placed,', len(bones), 'effects', sorted({b_[0] for b_ in bones}))
for model, (dist, when) in SEQUENCES.items():
    # the model's sound, played each loop when its audio bone switches on (the geyser's burst, a firework's launch), else when the first effect does
    recs = [r for r in anim_placed(model) if max(abs(r[12] - CX), abs(r[14] + CZ)) < HALF]
    if not recs:
        continue
    b = animcopy.find(model); events = [e.decode() for e in re.findall(rb'eventfile=([A-Za-z0-9_]+)', b) if e.decode() in soundscape.TRIGGERS]
    if not events:
        continue
    dur = animcopy.duration(b); t = np.arange(0, dur, 1 / 60); W, _, _ = animcopy.poses(b, t); at = []
    for s, sk in enumerate(granny2.File(b).tree()['Skeletons']):
        if 'audio' in sk['Name'].lower():
            sc = np.abs(np.linalg.det(W[s][0][:, :3, :3])) ** (1 / 3)
            if sc.max() > sc.min() * 1.2:
                at = [round(float(t[i]), 2) for i in np.flatnonzero(np.diff((sc > (sc.min() + sc.max()) / 2).astype(int)) > 0) + 1]
    sound = soundscape.trigger(events[0])
    for sq in sequences:
        if sq.get('model') == model:
            sq['sounds'] = [{**sound, 'at': at or [min(w[0] for n_ in sq['nodes'] for w in n_[1])]}]
    print(model, 'sound', events[0], 'at', at or 'its first effect')
# Movers (animated.py's fh1_movers.json): vehicles that fh1Extras.lua carries along their routes, with their sprays' effects, offsets, directions and
# on-windows. Each spray gets a node, which Lua moves with its vehicle and wakes when near, in its windows and FH1's hours for the effect
movers_file = level / 'fh1_movers.json'; movers = json.loads(movers_file.read_text()) if movers_file.exists() else None
blocks = {}; images = set(); amb = {}
for name in sorted({n[0] for n in seq_nodes} | {f[0] for m in (movers or {}).get('movers', []) for f in m['fx']}):
    r = ambient(name) if name + '.xml' in EFFECT_FILES else None
    if r:
        parts, ed, img, drift, when = r; blocks.update({q['name']: q for q in parts}); blocks[ed['name']] = ed; images.add(img)
# FH1 ejects some sprays backwards along their axis (the duster's at -2.4 m/s); timing() keeps BeamNG's speed at 0 or more, so movers get a copy that ejects
# forwards, and their jet is turned round
for name in sorted({f[0] for m in (movers or {}).get('movers', []) for f in m['fx']}):
    e = f'fh1ae_{name}'; props = effect(name).find('EmitterProperties') if name + '.xml' in EFFECT_FILES else None
    if e in blocks and props is not None and attr(props, 'Velocity', 'value') < 0:
        blocks[e + '_r'] = {**blocks[e], 'name': e + '_r', **timing(1000 / blocks[e]['ejectionPeriodMS'], -attr(props, 'Velocity', 'value'), attr(props, 'Velocity', 'variation'))}
for name in sorted({p[0] for p in placed}):
    if name + '.xml' in EFFECT_FILES:
        parts, ed, img = datablocks(name); blocks.update({q['name']: q for q in parts}); blocks[ed['name']] = ed; images.add(img)
for name in sorted({s[0] for s in scene}):
    r = ambient(name) if name + '.xml' in EFFECT_FILES else None
    if r:
        parts, ed, img, drift, when = r; blocks.update({q['name']: q for q in parts}); blocks[ed['name']] = ed; images.add(img); amb[name] = (to_b(drift), when, ed['ejectionVelocity'])
out = level / 'art' / 'particles'; out.mkdir(parents=True, exist_ok=True)
for old in out.glob('*.dds'):
    old.unlink()
for img, frames, cols in sorted(images):
    texture(img + '.xds', frames, out, cols)
long = [k for k, v in blocks.items() if v['class'] == 'ParticleEmitterData' and len(v['particles']) > 255]
assert not long, f'particle lists over 255 characters: {long}'
(level / 'art' / 'fh1_particles.datablocks.json').write_text(json.dumps(blocks, indent=1))
# Node names carry the effect, so the editor's scene tree can find them
label = lambda name: re.sub(r'^(_Composite_|AMB_)', '', name)
# Lua entries: name, x, y, wake distance, particles alive when running (for fh1Extras.lua's budget), then an optional time window
live = lambda e: int(round(max(blocks[q]['lifetimeMS'] for q in blocks[e]['particles'].split()) / blocks[e]['ejectionPeriodMS']))
nodes = []; lua = []
for i, (name, x, y, z) in enumerate(p for p in placed if f'fh1e_{p[0]}' in blocks):
    nodes.append({'name': f'fh1_fx_{i}_{label(name)}', 'class': 'ParticleEmitterNode', '__parent': 'fh1_fx', 'position': [round(x, 2), round(y, 2), round(z, 2)], 'dataBlock': 'lightExampleEmitterNodeData1',
                  'emitter': f'fh1e_{name}', 'active': False})
    # with the effect's own time window, as the ambient ones have: chimneys smoke 19:00 to 5:00
    lua.append([f'fh1_fx_{i}_{label(name)}', round(x, 1), round(y, 1), far.get(name, WATER_FAR if WATER.match(name) else SMOKE_FAR), live(f'fh1e_{name}')] + (hours(effect(name)) or []))
for name, w, axis, dist, when in (s for s in scene if s[0] in amb):
    drift, own, speed = amb[name]; i = len(nodes)
    # FH1's wind pushes particles sideways. BeamNG's only blows level-wide, so the jet leans by the drift instead
    d = axis / np.linalg.norm(axis) * max(speed, .1) + drift
    nodes.append({'name': f'fh1_fx_{i}_{label(name)}', 'class': 'ParticleEmitterNode', '__parent': 'fh1_fx', 'position': [round(float(v), 2) for v in w], 'dataBlock': 'lightExampleEmitterNodeData1',
                  'rotationMatrix': [round(float(v), 5) for v in basis(d).ravel()], 'emitter': f'fh1ae_{name}', 'active': False})
    lua.append([f'fh1_fx_{i}_{label(name)}', round(float(w[0]), 1), round(float(w[1]), 1), dist, live(f'fh1ae_{name}')] + (when or own or []))
names = []
for i, (name, w, axis) in enumerate(seq_nodes):
    names.append(f'fh1_seq_{i}_{label(name)}' if f'fh1ae_{name}' in blocks else None)
    if names[-1]:
        nodes.append({'name': names[-1], 'class': 'ParticleEmitterNode', '__parent': 'fh1_fx', 'position': [round(float(v), 2) for v in w], 'dataBlock': 'lightExampleEmitterNodeData1',
                      'rotationMatrix': [round(float(v), 5) for v in basis(axis).ravel()], 'emitter': f'fh1ae_{name}', 'active': False})
for s in sequences:
    s['nodes'] = [[names[i]] + rest for i, *rest in s['nodes'] if names[i]]
(level / 'fh1_sequences.json').write_text(json.dumps(sequences))
if movers:
    for m in movers['movers']:
        for k, f in enumerate(m['fx']):
            e = f'fh1ae_{f[0]}'; name = f"fh1_mv_{m['name']}_{k}_{label(f[0])}"
            if e not in blocks:
                print('no effect for', m['name'], f[0]); f.extend([None, None, None, None]); continue
            # the jet in the vehicle's frame, as a LuaQuat (animated.py lua_quat): the conjugate of its rotation. FH1's wind lean is left out, as it's in world axes
            back = e + '_r' in blocks; e = e + '_r' if back else e; R = basis(np.array(f[4:7]) * (-1 if back else 1))
            q = (Rotation.from_matrix(R).as_quat() * [-1, -1, -1, 1]).round(5).tolist()
            nodes.append({'name': name, 'class': 'ParticleEmitterNode', '__parent': 'fh1_fx', 'position': [round(float(v), 2) for v in m['keys'][0][:3]], 'dataBlock': 'lightExampleEmitterNodeData1',
                          'rotationMatrix': [round(float(v), 5) for v in R.ravel()], 'emitter': e, 'active': False})
            # appended: node name, its LuaQuat in the vehicle's frame, FH1's hours for the effect, particles alive when running
            f.extend([name, q, hours(effect(f[0])), live(e)])
    movers_file.write_text(json.dumps(movers))
    print('movers:', sum(len(m['fx']) for m in movers['movers']), 'spray nodes on', len(movers['movers']), 'vehicles')
(items.parent / 'fh1_fx').mkdir(exist_ok=True)
(items.parent / 'fh1_fx' / 'items.level.json').write_text(''.join(json.dumps(n) + '\n' for n in nodes))
mission.add_groups(items, 'fh1_fx')
(level / 'fh1_particles.json').write_text(json.dumps(lua))
print('done', len(nodes), 'nodes', sum(v['class'] == 'ParticleEmitterData' for v in blocks.values()), 'effects', sum(f * c for _, f, c in images), 'textures ->', level)
