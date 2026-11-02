"""FH1 sky and lighting for a built level: bakes a fixed-time cubemap from realtimesky.zip and TimeOfDayA.xml, then patches the scene.
Run after build3.py, which wipes the level: python sky.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import json, os, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy import ndimage as ni
import dds, gamezip, tex
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
# BeamNG time 0 is noon and 1 a full day, so 0.15 is 15:36, minute 936 of FH1's curves
TIME = .15; MINUTE = 936; FACE = 1024
# Tuned by eye, not read from FH1's sky shader: how high the close cloud band reaches, far cloud tiling, density cutoff and strength,
# and how far close clouds shade towards FH1's dense colour
BAND_TOP = 60.0; FAR_TILE = .5; FAR_CUT = .5; FAR_ALPHA = .6; CLOSE_SHADE = .25
# sRGB sky gradient measured from clear sky in FH1 screenshots (Red Rock, Beaumont). They show up to about 25 degrees; the top colour is extrapolated
SKY_HORIZON = (205, 243, 255); SKY_TOP = (70, 150, 225); SKY_CURVE = .45

SCENE = {'theLevelInfo': {'globalEnviromentMap': 'fh1_sky_cubemap', 'fogDensity': .00006, 'fogAtmosphereHeight': 400, 'visibleDistance': 6000},
         'tod': {'time': TIME, 'startTime': TIME},
         # Tuned against in-game shots. ScatterSky's own light is already blue in shade and warm in sun, so these stay near stock.
         # fogScale is FH1's Redrock fog template. skyBrightness and the flare are stock
         'sunsky': {'sunScale': [1, .82, .55, 1], 'ambientScale': [1, .89, .78, 1], 'fogScale': [.84, .94, 1, 1], 'skyBrightness': 220,
                    'flareType': 'BNG_Sunflare_3', 'flareScale': 5}}
SKYBOX = {'name': 'fh1_skybox', 'class': 'SkyBox', '__parent': 'MissionGroup', 'material': 'fh1_sky'}


def bc4(a):
    """Blocks of 8 bytes to 4x4 values, shape (n, 4, 4)."""
    a0 = a[:, 0].astype(int); a1 = a[:, 1].astype(int); t = np.arange(1, 7)[None, :]
    p6 = ((7 - t) * a0[:, None] + t * a1[:, None]) // 7
    p4 = ((5 - t[:, :4]) * a0[:, None] + t[:, :4] * a1[:, None]) // 5
    p4 = np.hstack([p4, np.zeros_like(a0)[:, None], np.full_like(a0, 255)[:, None]])
    pal = np.hstack([a0[:, None], a1[:, None], np.where((a0 > a1)[:, None], p6, p4)])
    bits = sum(a[:, 2 + i].astype(np.uint64) << np.uint64(8 * i) for i in range(6))
    sel = np.stack([(bits >> np.uint64(3 * i)) & np.uint64(7) for i in range(16)], 1).astype(int)
    return np.take_along_axis(pal, sel, 1).reshape(-1, 4, 4)


def xds(name):
    """Tiled BC5 .xds, top mip, as float red and green. Size is in the Xenos fetch constant."""
    b = gamezip.read('realtimesky.zip', name); size = struct.unpack('>I', b[0x24:0x28])[0]
    w = (size & 0x1fff) + 1; h = ((size >> 13) & 0x1fff) + 1; bw, bh = w // 4, h // 4
    r = np.frombuffer(tex.untile(b[52:], bw, bh, 16), 'u1').reshape(bh * bw, 16)
    r = r[:, [1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14]]
    return [bc4(r[:, s:s + 8]).reshape(bh, bw, 4, 4).transpose(0, 2, 1, 3).reshape(h, w).astype('f4') / 255 for s in (0, 8)]


def curves():
    t = ET.parse(gamezip.MEDIA + 'tracks/colorado/Ribbon_00/TimeOfDayA.xml').getroot(); out = {}
    for c in t:
        k = c.findall('Key')
        if k:
            ts = [float(x.get('time')) for x in k]
            out[c.tag] = np.array([np.interp(MINUTE, ts, [float(x.get(a)) for x in k]) for a in k[0].attrib if a != 'time'], 'f4')
    return out


def directions(n):
    """Unit view vector per pixel of each face: D3D cube order and axes with z up, checked against the edges of stock skies."""
    v, u = (np.mgrid[0:n, 0:n] + .5) * 2 / n - 1; one = np.ones_like(u)
    for d in ((one, -v, -u), (-one, -v, u), (u, one, v), (u, -one, -v), (u, -v, one), (-u, -v, -one)):
        d = np.stack(d, -1); yield d / np.linalg.norm(d, axis=-1, keepdims=True)


def linear(c):
    c = np.array(c, 'f4') / 255
    return np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)


def srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= .0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - .055)


def bake(d, C, band, density, cirrus):
    x, y, z = d[..., 0], d[..., 1], d[..., 2]; up = np.clip(z, 0, 1)[..., None]
    low, high = linear(SKY_HORIZON), linear(SKY_TOP)
    col = low + (high - low) * up ** SKY_CURVE
    thin = C['FarCloudThinColour'] * C['FarCloudThinIntensity'][0]; dense = C['FarCloudDenseColour'] * C['SkyGain'][0]
    # Far cirrus on a plane overhead, fading out towards the horizon
    s = 1 / (z + .15).clip(.05)
    h, w = cirrus.shape
    a = ni.map_coordinates(cirrus, [(y * s * FAR_TILE % 1) * h, (x * s * FAR_TILE % 1) * w], order=1, mode='grid-wrap')
    a = (np.clip((a - FAR_CUT) / (1 - FAR_CUT), 0, 1) ** .7 * FAR_ALPHA * np.clip(z / .25, 0, 1))[..., None]
    col = col * (1 - a) + thin * a
    # Close clouds: a full-circle band whose bottom row is the horizon
    h, w = band.shape; el = np.degrees(np.arcsin(np.clip(z, -1, 1)))
    uv = [(1 - el / BAND_TOP) * (h - 1), (np.arctan2(y, x) / (2 * np.pi) % 1) * w]
    a = ni.map_coordinates(band, uv, order=1, mode='constant')[..., None] * (el > 0)[..., None]
    t = ni.map_coordinates(density, uv, order=1, mode='constant')[..., None]
    col = col * (1 - a) + (thin * (1 - t * CLOSE_SHADE) + dense * t * CLOSE_SHADE) * a
    # Ground bounce below the horizon, which only reflections see
    g = np.clip(-z / .05, 0, 1)[..., None]
    col = col * (1 - g) + C['HLBottomColour'] * 2 * g
    return (srgb(col) * 255 + .5).astype('u1')


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
C = curves(); band, density = xds('CloseCloudAlphaDensityFront0.xds'); cirrus = xds('FarCloudMapBack0.xds')[0]
out = level / 'art' / 'skies' / 'fh1_sky'; out.mkdir(parents=True, exist_ok=True)
faces = []
for i, d in enumerate(directions(FACE), 1):
    p = out / f'fh1_sky_{i}.color.dds'; dds.save(p, bake(d, C, band, density, cirrus)); faces.append(f'/levels/{LVL}/art/skies/fh1_sky/{p.name}')
(out / 'main.materials.json').write_text(json.dumps({
    'fh1_sky': {'name': 'fh1_sky', 'mapTo': 'unmapped_mat', 'class': 'Material', 'Stages': [{}, {}, {}, {}], 'cubemap': 'fh1_sky_cubemap', 'materialTag0': 'beamng'},
    'fh1_sky_cubemap': {'name': 'fh1_sky_cubemap', 'class': 'CubemapData', 'cubeFace': faces}}, indent=2))
rows = [json.loads(l) for l in items.read_text().splitlines() if l.strip()]
for o in rows:
    o.update(SCENE.get(o.get('name'), {}))
if not any(o.get('name') == SKYBOX['name'] for o in rows):
    rows.append(SKYBOX)
items.write_text(''.join(json.dumps(o) + '\n' for o in rows))
print('done', out)
