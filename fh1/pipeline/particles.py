"""FH1's water and smoke particles for a built level: effects.zip becomes BeamNG particle datablocks, ParticleEmitters.xml becomes nodes, which fh1Extras.lua wakes near the player.
Run after levelinfo.py: python particles.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, os, re, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import gamezip, tex

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
CX, CZ, HALF = {'fh1_colorado': (-300.0, 500.0, 8192.0), 'fh1_redrock': (-6000.0, 4250.0, 2048.0)}[LVL]
WATER = re.compile(r'AMB_(WaterFall_|Dam_Rising_Mist|Geyser_Mist|FountainWater)')
SMOKE = re.compile(r'AMB_(HouseSmokeVent|Chimney_Smoke_White|Ind_SmokeVent|Small_Steam_Vent)')
# How far away an emitter wakes, in metres. Water is further because falls are seen across valleys
WATER_FAR = 1500; SMOKE_FAR = 700
# FH1's alpha suits its own soft particle shader, and is too faint in BeamNG. Chosen by eye, like the angles
ALPHA = 1.5; SPRAY_ANGLE = 60; BOX_ANGLE = 180
# Metres over which a particle fades into scenery. Mist quads are up to 24 m wide, and cut hard lines into rock without this
SOFT = 4
ROOT = f'/levels/{LVL}/art/particles'


def effect(name):
    return ET.fromstring(gamezip.read('effects.zip', name + '.xml').decode('latin1').strip())


def attr(e, path, key, default=0.0):
    x = e.find(path)
    return float(x.get(key, default)) if x is not None else default


def texture(name, frames, out):
    """DXT5 top mip as a .dds, keeping FH1's own blocks. These sit tiled and byte swapped in .xds files. Format 0x75 is DXT5: read as DXT3 the alpha turns to static."""
    b = next(gamezip.read('effects.zip', n) for n in EFFECT_FILES if n.lower() == name.lower())
    size = struct.unpack('>I', b[0x24:0x28])[0]; w = (size & 0x1fff) + 1; h = ((size >> 13) & 0x1fff) + 1
    bw, bh = (w + 3) // 4, (h + 3) // 4
    blocks = np.frombuffer(tex.untile(b[52:], bw, bh, 16), '>u2').astype('<u2').tobytes()
    # Frames are stacked, so whole rows of blocks can be cut apart without re-encoding
    row = bh // frames; size = bw * row * 16
    hdr = b'DDS ' + struct.pack('<7I', 124, 0x81007, row * 4, w, size, 0, 1) + bytes(44)
    hdr += struct.pack('<2I4s5I', 32, 4, b'DXT5', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x1000, 0, 0, 0, 0)
    stem = Path(name).stem.lower()
    for i in range(frames):
        (out / f'{stem}_{frames}_{i}.color.dds').write_bytes(hdr + blocks[i * size:(i + 1) * size])


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
    period = max(int(round(1000 / rate)), 1)
    theta = BOX_ANGLE if kind == 'Box' else SPRAY_ANGLE
    offset = float(box.get('length', 0)) / 2 if box is not None else 0
    p = {'class': 'ParticleData',
         'lifetimeMS': int(life * 1000), 'lifetimeVarianceMS': int(min(life * life_var, life * .9) * 1000),
         'colors': [rgb + [a0], rgb + [a1], rgb + [a1], rgb + [a2]], 'sizes': [start, (start + peak) / 2, peak, peak], 'times': [0, float(fade_in), float(fade_out), 1],
         'gravityCoefficient': grav / 981, 'dragCoefficient': .3, 'inheritedVelFactor': 0, 'useInvAlpha': True,
         'spinRandomMin': -30, 'spinRandomMax': 30, 'spinSpeed': 1 / spin if spin else 0, 'spinInitialMax': 360}
    # BeamNG has no use of frame animation in stock particles, so each frame is its own particle and the emitter picks at random
    parts = [{**p, 'name': f'fh1p_{name}_{i}', 'textureName': f'{ROOT}/{img}_{frames}_{i}.color.dds', 'animTexName': f'{ROOT}/{img}_{frames}_{i}.color.dds'} for i in range(frames)]
    em = {'name': f'fh1e_{name}', 'class': 'ParticleEmitterData', 'particles': ' '.join(q['name'] for q in parts), 'blendStyle': 'NORMAL', 'ejectionPeriodMS': period, 'periodVarianceMS': period // 2,
          'ejectionVelocity': speed, 'velocityVariance': min(speed * speed_var, speed * .9), 'ejectionOffset': offset, 'ejectionOffsetVariance': offset, 'thetaMin': 0, 'thetaMax': theta,
          'phiVariance': 360, 'sortParticles': True, 'reverseOrder': False, 'softParticles': '1', 'softnessDistance': SOFT if WATER.match(name) else .85}
    return parts, em, (img, frames)


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
    x, y, z = float(pos.get('v.x')) - CX, -(float(pos.get('v.z')) - CZ), float(pos.get('v.y'))
    if max(abs(x), abs(y)) < HALF:
        placed.append((eff.get('value'), x, y, z))
blocks = {}; images = set()
for name in sorted({p[0] for p in placed}):
    if name + '.xml' in EFFECT_FILES:
        parts, ed, img = datablocks(name); blocks.update({q['name']: q for q in parts}); blocks[ed['name']] = ed; images.add(img)
out = level / 'art' / 'particles'; out.mkdir(parents=True, exist_ok=True)
for old in out.glob('*.dds'):
    old.unlink()
for img, frames in sorted(images):
    texture(img + '.xds', frames, out)
(level / 'art' / 'fh1_particles.datablocks.json').write_text(json.dumps(blocks, indent=1))
nodes = []; lua = []
for i, (name, x, y, z) in enumerate(p for p in placed if f'fh1e_{p[0]}' in blocks):
    nodes.append({'name': f'fh1_fx_{i}', 'class': 'ParticleEmitterNode', '__parent': 'fh1_fx', 'position': [round(x, 2), round(y, 2), round(z, 2)], 'dataBlock': 'lightExampleEmitterNodeData1',
                  'emitter': f'fh1e_{name}', 'active': False})
    lua.append([f'fh1_fx_{i}', round(x, 1), round(y, 1), WATER_FAR if WATER.match(name) else SMOKE_FAR])
(items.parent / 'fh1_fx').mkdir(exist_ok=True)
(items.parent / 'fh1_fx' / 'items.level.json').write_text(''.join(json.dumps(n) + '\n' for n in nodes))
rows = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
if not any(o.get('name') == 'fh1_fx' for o in rows):
    rows.append({'name': 'fh1_fx', 'class': 'SimGroup', '__parent': 'MissionGroup'})
    items.write_text(''.join(json.dumps(o) + '\n' for o in rows))
(level / 'fh1_particles.json').write_text(json.dumps(lua))
print('done', len(nodes), 'nodes', sum(v['class'] == 'ParticleEmitterData' for v in blocks.values()), 'effects', sum(f for _, f in images), 'textures ->', level)
