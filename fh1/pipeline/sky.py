"""FH1 sky and lighting for a built level: bakes cubemaps at several times of day from realtimesky.zip and TimeOfDayA.xml, then patches the scene.
fh1Extras.lua swaps the skybox, reflections and river cubemaps to follow BeamNG's clock, from the table in fh1_sky.json.
Run after build3.py, which wipes the level: python sky.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import hashlib, json, os, shutil, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy import ndimage as ni
import dds, gamezip, mission, tex
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
# BeamNG time 0 is noon and 1 a full day. The level starts at 10:00, which the user chose. MINUTE is FH1's 15:36, the time the measured gradient comes from
TIME = (10 - 12) / 24 % 1; MINUTE = 936; FACE = 1024
# Each bake: FH1 minute, name, and the BeamNG hour it takes over from. The first is the level's default and keeps the old names, which water.py's
# rivers use. BeamNG's sun rises at 6:00 and sets at 18:00. FH1's never sets: its night runs 20:02 to 6:28, with dusk colours from 18:00.
# So bakes are matched to BeamNG's hours by phase, chosen by eye. FH1's horizon is only yellow from about 8:00 to 9:45, so morning is FH1's 10:00 and short:
# the user found 9:00 held until noon far yellower than FH1, which is blue most of the day
BAKES = [(936, 'afternoon', 14), (0, 'night', 20.5), (405, 'predawn', 5), (445, 'sunrise', 5.75), (600, 'morning', 6.75), (720, 'midday', 8.5),
         (1090, 'sunset', 16.75), (1160, 'golden', 17.5), (1195, 'dusk', 18), (1240, 'twilight', 19)]
# Tuned by eye, not read from FH1's sky shader: how high the close cloud band reaches, far cloud tiling, density cutoff and strength,
# and how far close clouds shade towards FH1's dense colour
BAND_TOP = 60.0; FAR_TILE = .5; FAR_CUT = .5; FAR_ALPHA = .6; CLOSE_SHADE = .25
# sRGB sky gradient measured from clear sky in FH1 screenshots (Red Rock, Beaumont), at minute 936. They show up to about 25 degrees; the top colour
# is extrapolated. Other bakes scale it by FH1's atmosphere colours relative to minute 936
SKY_HORIZON = (205, 243, 255); SKY_TOP = (70, 150, 225); SKY_CURVE = .45
# Stars.bin is the Yale Bright Star Catalogue, brightest first: 9096 big-endian (x, y, z, w), y towards the celestial pole, RA = atan2(x, z),
# w the magnitude / 7.8 with the four brighter than 0 clamped (checked on Vega, Rigel, Betelgeuse, Polaris). Placed for FH1's Colorado latitude, with Orion on the meridian.
# Brightness falls off at half the true rate, so faint stars stay visible, chosen by eye. Stars were invisible in-game at 1.2 and 0.6 px: mips averaged
# them away, so faces with stars keep peaks in their mips
LATITUDE = 39.5; SIDEREAL_HOURS = 5.5; MAG1 = 7.8; STAR_GAMMA = .5; STAR_GAIN = 2.0; STAR_SIGMA = .9
# Moon2.xds is the full moon with its glow; Moon0 is a bare disc and 1, 3, 4 crescents. Its width across the sky is chosen by eye
MOON = 'Moon2.xds'; MOON_DEGREES = 3.0
# Bakes whose time hides the skybox, so BeamNG's own night sky shows. Its core_celestial stars are additive and stay crisp at full night, where
# stars baked into the cubemap faded with the scene: the user chose BeamNG's stars and moon. The bake still feeds reflections and rivers
BEAMNG_SKY = {'night'}
# FH1 world axes (x, up, z) to BeamNG (x, y, z up)
B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.]])

SCENE = {'theLevelInfo': {'globalEnviromentMap': 'fh1_sky_cubemap', 'fogDensity': .00006, 'fogAtmosphereHeight': 400},
         'tod': {'time': TIME, 'startTime': TIME},
         # Tuned against in-game shots. ScatterSky's own light is already blue in shade and warm in sun, so these stay near stock.
         # fogScale is FH1's Redrock fog template. skyBrightness and the flare are stock
         'sunsky': {'sunScale': [1, .82, .55, 1], 'ambientScale': [1, .89, .78, 1], 'fogScale': [.84, .94, 1, 1], 'skyBrightness': 220,
                    'flareType': 'BNG_Sunflare_3', 'flareScale': 5}}
# FH1's look by area: PostProcessingZones_Safe.xml gives each zone a fog and a bloom template (dynamicpost.zip) and a fade time in seconds.
# Its colour grade also changes by zone, which BeamNG 0.40 can't do at runtime. Fog density is relative to Redrock's 2.35, as the level's
# fog was tuned at Red Rock. fh1Extras.lua eases towards the zone under the camera
FOG_REF = 2.35; CENTRE = {'fh1_colorado': (-300.0, 500.0), 'fh1_redrock': (-6000.0, 4250.0), 'fh1_clearsprings': (1700.0, 2000.0)}[LVL]
SKYBOX = {'name': 'fh1_skybox', 'class': 'SkyBox', '__parent': 'MissionGroup', 'material': 'fh1_sky'}


def names(tag):
    """Material, cubemap and face file stem of a bake."""
    return ('fh1_sky', 'fh1_sky_cubemap', 'fh1_sky') if tag == BAKES[0][1] else (f'fh1_sky_{tag}', f'fh1_sky_{tag}_cubemap', f'fh1_sky_{tag}')


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


def curves(minute):
    t = ET.parse(gamezip.MEDIA + 'tracks/colorado/Ribbon_00/TimeOfDayA.xml').getroot(); out = {}
    for c in t:
        k = c.findall('Key')
        if k:
            ts = [float(x.get('time')) for x in k]
            out[c.tag] = np.array([np.interp(minute, ts, [float(x.get(a)) for x in k]) for a in k[0].attrib if a != 'time'], 'f4')
    return out


def directions(n):
    """Unit view vector per pixel of each face: D3D cube order and axes with z up, checked against the edges of stock skies."""
    v, u = (np.mgrid[0:n, 0:n] + .5) * 2 / n - 1; one = np.ones_like(u)
    for d in ((one, -v, -u), (-one, -v, u), (u, one, v), (u, -one, -v), (u, -v, one), (-u, -v, -one)):
        d = np.stack(d, -1); yield d / np.linalg.norm(d, axis=-1, keepdims=True)


def face_uv(d):
    """Inverse of directions for unit vectors d, shape (k, 3): face index and u, v in -1..1."""
    a = np.abs(d); k = a.argmax(1); face = 2 * k + (d[np.arange(len(d)), k] < 0)
    x, y, z = (d / a.max(1)[:, None]).T; f = [face == i for i in range(6)]
    return face, np.select(f, [-z, z, x, x, x, -x]), np.select(f, [-y, -y, z, -z, -y, -y])


def linear(c):
    c = np.array(c, 'f4') / 255
    return np.where(c <= .04045, c / 12.92, ((c + .055) / 1.055) ** 2.4)


def srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= .0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - .055)


def star_faces(n):
    """Linear star brightness on each cube face, before StarFade."""
    F = np.frombuffer(gamezip.read('realtimesky.zip', 'Stars.bin'), '>f4').reshape(-1, 4).astype('f8')
    dec = np.arcsin(np.clip(F[:, 1], -1, 1)); ra = np.arctan2(F[:, 0], F[:, 2])
    h = np.radians(SIDEREAL_HOURS * 15) - ra; lat = np.radians(LATITUDE)
    d = np.stack([-np.cos(dec) * np.sin(h), np.cos(lat) * np.sin(dec) - np.sin(lat) * np.cos(dec) * np.cos(h),
                  np.sin(lat) * np.sin(dec) + np.cos(lat) * np.cos(dec) * np.cos(h)], 1)
    level = STAR_GAIN * 10 ** (-.4 * STAR_GAMMA * F[:, 3] * MAG1) * np.clip(d[:, 2] / .1, 0, 1)
    face, u, v = face_uv(d); col = (u + 1) * n / 2 - .5; row = (v + 1) * n / 2 - .5
    out = [np.zeros((n, n), 'f4') for _ in range(6)]
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            r = np.round(row).astype(int) + dy; c = np.round(col).astype(int) + dx
            w = level * np.exp(-((r - row) ** 2 + (c - col) ** 2) / (2 * STAR_SIGMA ** 2))
            ok = (r >= 0) & (r < n) & (c >= 0) & (c < n) & (level > 0)
            for i in range(6):
                s = ok & (face == i)
                np.add.at(out[i], (r[s], c[s]), w[s])
    return out


def moon_layer(d, C, rgba):
    """FH1's moon at its night position: linear colour and alpha per pixel."""
    a = C['SunObjectMoon'][0] * min(C['SunObjectAlpha'][0] / .8, 1)
    if a <= 0:
        return None
    m = B @ C['SunObjectPos']; m /= np.linalg.norm(m)
    # e1 points left as seen from the ground, e2 up
    e1 = np.cross([0, 0, 1], m); e1 /= np.linalg.norm(e1); e2 = np.cross(m, e1)
    dm = d @ m; s = 1 / (2 * np.tan(np.radians(MOON_DEGREES) / 2) * np.clip(dm, 1e-3, None))
    n = rgba.shape[0]; uv = [(.5 - (d @ e2) * s) * n - .5, (.5 - (d @ e1) * s) * n - .5]
    alpha = ni.map_coordinates(rgba[..., 3], uv, order=1, mode='constant') * (dm > 0) * a
    tint = C['SunObjectColour'] / C['SunObjectColour'].max()
    colour = np.stack([ni.map_coordinates(rgba[..., i], uv, order=1, mode='nearest') for i in range(3)], -1) * tint
    return colour, alpha[..., None]


def bake(d, C, R, band, density, cirrus, stars, moon):
    """C: FH1's curves at this bake's minute, R: at minute 936, which the measured gradient matches."""
    x, y, z = d[..., 0], d[..., 1], d[..., 2]; up = np.clip(z, 0, 1)[..., None]
    gain = C['SkyGain'] / R['SkyGain']
    low = linear(SKY_HORIZON) * gain * C['AtmosphereBottomColour'] / np.maximum(R['AtmosphereBottomColour'], 1e-3)
    high = linear(SKY_TOP) * gain * C['AtmosphereTopColour'] / np.maximum(R['AtmosphereTopColour'], 1e-3)
    col = low + (high - low) * up ** SKY_CURVE
    col = col + stars[..., None] * C['StarFade'][0]
    if moon is not None:
        m = moon_layer(d, C, moon)
        if m is not None:
            col = col * (1 - m[1]) + m[0] * m[1]
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
    # Ground bounce below the horizon, which only reflections see. HLBottomColour barely changes at night, so it dims with FH1's horizon
    g = np.clip(-z / .05, 0, 1)[..., None]
    dim = min(1, float((C['AtmosphereBottomColour'] / np.maximum(R['AtmosphereBottomColour'], 1e-3)).mean() * gain[0]))
    col = col * (1 - g) + C['HLBottomColour'] * 2 * dim * g
    return (srgb(col) * 255 + .5).astype('u1')


def zones():
    """FH1's post-processing zones as BeamNG x, y triangles, with fog density and bloom cutoff and scale relative to the level's own."""
    t = lambda n: {e.get('Name'): e.attrib for e in ET.fromstring(gamezip.read('dynamicpost.zip', f'Tracks/Colorado/{n}_Templates.xml'))}
    fog, bloom = t('Fog'), t('Bloom'); out = []
    for z in ET.parse(gamezip.MEDIA + 'tracks/colorado/Ribbon_00/PostProcessingZones_Safe.xml').getroot():
        b = bloom[z.get('BloomTemplate')]
        out.append({'name': z.get('Name'), 'fog': round(float(fog[z.get('Fog')]['Density']) / FOG_REF, 3), 'bloomCutoff': float(b['Cutoff']),
                    'bloomScale': float(b['Scale']), 'lerp': float(z.get('LerpTime')),
                    'tris': [[[round(float(p.get('posX')) - CENTRE[0], 1), round(float(p.get('posZ')) + CENTRE[1], 1)] for p in tri] for tri in z]})
    return out


def table():
    """Bake index, from 1, for each quarter hour of BeamNG's clock from midnight: the bake that took over last, wrapping at midnight."""
    since = (np.arange(96)[:, None] / 4 - np.array([b[2] for b in BAKES])[None, :]) % 24
    return [int(i) + 1 for i in since.argmin(1)]


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
R = curves(MINUTE)
out = level / 'art' / 'skies' / 'fh1_sky'; out.mkdir(parents=True, exist_ok=True)
# The faces don't depend on the level and FH1's files never change, so they're baked once per version of this code and copied after that
CACHE = Path('../ddscache') / ('sky-' + hashlib.md5(b''.join(Path(f).read_bytes() for f in (__file__, dds.__file__, tex.__file__))).hexdigest()[:12])
if not all((CACHE / f'{names(tag)[2]}_{i}.color.dds').exists() for _, tag, _ in BAKES for i in range(1, 7)):
    band, density = xds('CloseCloudAlphaDensityFront0.xds'); cirrus = xds('FarCloudMapBack0.xds')[0]
    b = gamezip.read('realtimesky.zip', MOON); moon = tex.decode(b[52:], 256, 256, 0x54).astype('f4') / 255
    moon[..., :3] = linear(moon[..., :3] * 255)
    stars = star_faces(FACE); dirs = list(directions(FACE)); CACHE.mkdir(parents=True, exist_ok=True)
materials = {}; bakes = []
for minute, tag, _ in BAKES:
    C = curves(minute); material, cubemap, stem = names(tag); faces = []
    for i in range(1, 7):
        p = out / f'{stem}_{i}.color.dds'; c_ = CACHE / p.name
        if not c_.exists():
            dds.save(c_.with_suffix('.tmp'), bake(dirs[i - 1], C, R, band, density, cirrus, stars[i - 1], moon), peak=C['StarFade'][0] > .1)
            os.replace(c_.with_suffix('.tmp'), c_)
        shutil.copyfile(c_, p); faces.append(f'/levels/{LVL}/art/skies/fh1_sky/{p.name}')
    materials[material] = {'name': material, 'mapTo': 'unmapped_mat', 'class': 'Material', 'Stages': [{}, {}, {}, {}], 'cubemap': cubemap, 'materialTag0': 'beamng'}
    materials[cubemap] = {'name': cubemap, 'class': 'CubemapData', 'cubeFace': faces}
    # FH1's fog is about 2.7 times denser at night than by day
    bakes.append({'name': tag, 'material': material, 'cubemap': cubemap, 'hideBox': tag in BEAMNG_SKY, 'fog': round(float(C['FogDensity'][0] / R['FogDensity'][0]), 3)})
    print(tag, minute, 'stars', round(float(C['StarFade'][0]), 2), 'moon', round(float(C['SunObjectMoon'][0] * C['SunObjectAlpha'][0]), 2))
(out / 'main.materials.json').write_text(json.dumps(materials, indent=2))
(level / 'fh1_sky.json').write_text(json.dumps({'bakes': bakes, 'quarterHours': table(), 'fogDensity': SCENE['theLevelInfo']['fogDensity'], 'zones': zones()}))
with mission.edit(items) as rows:
    for o in rows:
        o.update(SCENE.get(o.get('name'), {}))
    if not any(o.get('name') == SKYBOX['name'] for o in rows):
        rows.append(SKYBOX)
print('done', out)
