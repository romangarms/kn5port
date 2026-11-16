"""FH1 sky and lighting for a built level: bakes cubemaps at several times of day from realtimesky.zip and TimeOfDayA.xml, then patches the scene.
fh1Extras.lua swaps the skybox, reflections and river cubemaps to follow BeamNG's clock, from the table in fh1_sky.json.
Run after build3.py, which wipes the level: python sky.py [fh1_colorado|fh1_redrock]. Safe to run twice. Needs no rebuild."""
import hashlib, json, os, re, shutil, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from scipy import ndimage as ni
import dds, frame, gamepath, gamezip, mission, tex
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
# BeamNG time 0 is noon and 1 a full day. The level starts at 10:00, which the user chose. MINUTE is FH1's 15:36, the reference for fog and ground bounce
TIME = (10 - 12) / 24 % 1; MINUTE = 936; FACE = 1024
# Each bake: FH1 minute, name, and the BeamNG hour it takes over from. The first is the level's default and keeps the old names, which water.py's
# rivers use. BeamNG's sun rises at 6:00 and sets at 18:00. FH1's never sets: its night runs 20:02 to 6:28, with dusk colours from 18:00.
# So bakes are matched to BeamNG's hours by phase, chosen by eye. FH1's horizon is only yellow from about 8:00 to 9:45, so morning is FH1's 10:00 and short:
# the user found 9:00 held until noon far yellower than FH1, which is blue most of the day
BAKES = [(936, 'afternoon', 14), (0, 'night', 20.5), (405, 'predawn', 5), (445, 'sunrise', 5.75), (600, 'morning', 6.75), (720, 'midday', 8.5),
         (1090, 'sunset', 16.75), (1160, 'golden', 17.5), (1195, 'dusk', 18), (1240, 'twilight', 19)]
# FH1's sky as its own shaders draw it. They're compiled into default.xex (xshader.py disassembles them: Sky, CloseCloud, FarCloud) and the
# constants were traced through the recompiled code (docs/sky.md). Everything here is FH1's except where marked. The sun: FH1's SunObjectPos is the
# sun's disc from DAY[0] to DAY[1] (FH1 minutes) and the moon outside it; the sky's haze and the clouds' lighting both use it. Its SunPos, the light,
# never drops below 17 degrees; BeamNG has one direction for both, so it follows the disc. Bakes outside DAY take the nearest end
DAY = (389, 1201)
# The dome: elevation -6 to 84 degrees, and a vertex value w = (elevation + 6) / 90 that drives the sky's gradient and the close clouds' V
DOME_LOW, DOME_SPAN = -6.0, 90.0
# Close clouds: UV scales and the front layer's starting U offset (it then turns CloseCloudFrontRevs times an hour, which a still bake can't)
CLOSE_U0 = .5
# Far clouds: a sphere of radius 10000 centred 9700 below the camera, out to where a ray 6 degrees below the horizon meets it, rings even in angle
# (the evenness is inferred). Disc UV within +-0.5; the alpha map tiles FAR_ALPHA_UV times, the mask twice at FAR_MASK_UV and is averaged.
# Coverage: cover = lerp(0.616, 0.43, FarCloudCoverage), alpha = 1 - 0.984 ** (255 * sat(mask - cover))
FAR_R, FAR_DROP = 10000.0, 9700.0; FAR_ALPHA_UV = 5.0; FAR_MASK_UV = (1.01, 3.2); FAR_COVER = (.616, .43); FAR_SHARP = .984
# FH1's post: scene colour (stored as sqrt, so blends happen on sqrt values) times 2^EV, then Hable's filmic curve with Colorado's filmicTone,
# divided by its value at white, and written as sqrt. EV is the toneAdaptiveExposure table looked up by the scene's mean sqrt luminance, plus
# filmicTone's exposure. The key is the plain mean linear luminance of the unexposed scene (centre-weighted); a bake has no scene, so it uses the
# sky's own between the horizon and KEY_TOP degrees (an assumption). The zone's colour grade LUT, brightness, contrast and desaturation come
# after the tone map: see GRADE
KEY_TOP = 30.0
# FH1's bloom, added to the scene before the tone map. Bright pass on the exposed scene x, L its luminance:
#   e = x * max(lower L, upper L + (lower - upper) knee) / L - 1/255,  times scale
# then four levels each blurred and halved from the last, and bloom = sum of weight * 0.2 * (e, level 0..3). The base values are the zone's
# Bloom_Templates.xml entry; TimeOfDayA.xml's Bloom*Multiplier curves scale cutoff (the knee) and scale, and since the lower gradient's base is 0,
# its curve is taken as the value and the upper's as a multiplier of 1 (both inferred). The template is the level's zone's (see zone()). Persist (frame feedback, about 6%) is left out. The blur sizes are in FH1's screen texels: the bright pass is at a quarter of
# 1280 px, and the blurs (FH1's 7-tap kernel, sigma 1.49 texels) compound down the levels; BLOOM_FOV, the horizontal field of view that turns
# them into degrees, is an assumption
BLOOM_FOV = 90.0; BLOOM_PX = 1280; BLUR_SIGMA = 1.49; PANO = .25
# FH1's colour grade, after the tone map on its linear output (BloomTonemap and sub_82552028 / sub_82432628):
# - one 16^3 LUT the CPU blends each frame: identity + DayColorGradeAmount (day LUT - identity) + NightColorGradeAmount (night LUT - identity),
#   clamped, looked up trilinearly at the tone-mapped colour (texel = colour * 15). DDS bytes are BGRA, red along x, blue along the slices
# - then the zone's ColorSettings: contrast about 0.5 by (1 + Contrast), plus Brightness, then colour * DesatAmount + (1 - DesatAmount) *
#   dot(DesatLuminosity, colour) (so 0.9 keeps 90%; the direction is inferred), times DesatPostMod (whose B the game reads from R), each scaled
#   by its TimeOfDayA multiplier. ExposureBias adds to the tone map's exposure
# A bake can follow only one zone, so each level takes the one at its centre (zone()). FH1 blends zones as you drive between them
# Below the horizon FH1's dome goes on to -6 degrees in its pure bottom colour, a bright band its backdrop terrain always covers. Where a BeamNG
# level lacks that backdrop the skybox shows, and that band read as a white or yellow wall, the flat ground colour before it as grey. So below the
# horizon the bake shows the sky as it is at the horizon, fading to a ground colour between these elevations, which only reflections reach (chosen)
GROUND_FADE = (-10.0, -30.0)
# fh1Extras.lua gets the sun's azimuth and elevation every SUN_STEP minutes of BeamNG's clock and interpolates between
SUN_STEP = 10
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

SCENE = {'theLevelInfo': {'globalEnviromentMap': 'fh1_sky_cubemap', 'fogDensity': .00006, 'fogAtmosphereHeight': 400 + frame.LIFT},
         'tod': {'time': TIME, 'startTime': TIME},
         # Tuned against in-game shots. ScatterSky's own light is already blue in shade and warm in sun, so these stay near stock.
         # fogScale is FH1's Redrock fog template. skyBrightness and the flare are stock
         'sunsky': {'sunScale': [1, .82, .55, 1], 'ambientScale': [1, .89, .78, 1], 'fogScale': [.84, .94, 1, 1], 'skyBrightness': 220,
                    'flareType': 'BNG_Sunflare_3', 'flareScale': 5,
                    # FH1's night keeps a light on: its sun never sets, and at night it's SunColor (0.094, 0.136, 0.268) at SunColorMult
                    # 0.15, with a sky ambient of HLTop/HLBottom (0.011, 0.016, 0.018) between them. Stock BeamNG levels leave both black,
                    # and the user found night far too dark between lamps (2026-11-04). These are FH1's hues: the ambient at 0.15 and the
                    # moon light's blue lifted to 0.56, 0.82, 1, both by eye. Tunable live in the World Editor: ScatterSky's Night Color and Moon Light Color
                    'nightColor': [.61 * .15, .89 * .15, 1 * .15, 1], 'moonLightColor': [.35 * 1.6, .51 * 1.6, 1, 1]}}
# FH1's look by area: PostProcessingZones_Safe.xml gives each zone a fog and a bloom template (dynamicpost.zip) and a fade time in seconds.
# Its colour grade also changes by zone, which BeamNG 0.40 can't do at runtime. Fog density is relative to Redrock's 2.35, as the level's
# fog was tuned at Red Rock. fh1Extras.lua eases towards the zone under the camera
FOG_REF = 2.35; CENTRE = {'fh1_colorado': (-300.0, 500.0), 'fh1_redrock': (-6000.0, 4250.0), 'fh1_clearsprings': (1700.0, 2000.0), 'fh1_coloradodirt': (-300.0, 500.0)}[LVL]
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
    t = ET.parse(gamepath.RIBBON + 'TimeOfDayA.xml').getroot(); out = {}
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


def sun_dir(minute):
    """FH1's sun disc in BeamNG axes, unit, held at the ends of DAY outside it."""
    s = B @ curves(min(max(minute, DAY[0]), DAY[1]))['SunObjectPos']
    return s / np.linalg.norm(s)


def post():
    """Colorado's filmicTone and toneAdaptiveExposure from Ribbon_00/TimeOfDay.xml."""
    # Read by pattern: the file isn't valid XML (its bloom tag repeats an attribute)
    t = open(gamepath.RIBBON + 'TimeOfDay.xml', encoding='utf-8').read()
    tag = lambda n: {k: float(v) for k, v in re.findall(r'(\w+)="([-\d.]+)"', re.search(rf'<{n}\b[^>]*>', t).group(0))}
    f, a = tag('filmicTone'), tag('toneAdaptiveExposure')
    keys = [a['key_' + k] / 255 for k in ('extraDark', 'dark', 'light', 'extraLight')]
    evs = [a['exp_' + k] for k in ('extraDark', 'dark', 'light', 'extraLight')]
    return f, keys, evs


def hable(x, f):
    A, Bs, Cs, D, E, F = f['shoulderStrength'], f['linearStrength'], f['linearAngle'], f['toeStrength'], f['toeNumerator'], f['toeDenominator']
    return (x * (A * x + Cs * Bs) + D * E) / (x * (A * x + Bs) + D * F) - E / F


def exposure(key, P, bias=0.0):
    """BloomTonemap's vertex shader: the segment of the key table the luminance falls in, lerped, plus filmicTone's exposure."""
    f, k, e = P
    i = 0 if key < k[1] else 2 if key >= k[2] else 1
    return e[i] + (e[i + 1] - e[i]) * min(max((key - k[i]) / (k[i + 1] - k[i]), 0), 1) + f['exposure'] + bias


def sky(d, C, sun):
    """Sky_VS/Sky_PS: FH1's linear sky after Gain."""
    sat = lambda v: np.clip(v, 0, 1)
    w = sat((np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1))) - DOME_LOW) / DOME_SPAN)
    c, bc = C['AtmosphereClampPower'][0], C['AtmosphereBottomClampPower'][0]
    t = sat((w - bc) / (c - bc)) ** max(C['AtmosphereColourPower'][0], .01)
    col = C['AtmosphereBottomColour'] + (C['AtmosphereTopColour'] - C['AtmosphereBottomColour']) * t[..., None]
    # The haze squashes by EllipticalScale on the side of the sun away from the ground: up, less its part along the sun
    up = np.array([0, 0, 1.]); u = up - sun * sun[2]; u = u / max(np.linalg.norm(u), 1e-6)
    h = (sat(d @ sun) * sat(1 + (C['AtmosphereHazeEllipticalScale'][0] - 1) * sat(d @ u))) ** C['AtmosphereHazePower'][0] * C['AtmosphereHazeAlpha'][0]
    haze = C['AtmosphereHazeColour'] * C['AtmosphereHazeIntensity'][0]
    return sat(col + (haze - col) * h[..., None]) * C['SkyGain'][0]


def far_clouds(d, C, maps):
    """FarCloud_VS/PS, front and back layers over each other: linear colour and alpha. No sun term: FH1 lights them only by its curves."""
    z = d[..., 2]; b = FAR_R ** 2 - FAR_DROP ** 2
    t = -FAR_DROP * z + np.sqrt((FAR_DROP * z) ** 2 + b)
    ang = lambda zz, tt: np.arccos(np.clip((tt * zz + FAR_DROP) / FAR_R, -1, 1))
    zr = np.sin(np.radians(DOME_LOW)); rim = ang(zr, -FAR_DROP * zr + np.sqrt((FAR_DROP * zr) ** 2 + b))
    rho = np.clip(ang(z, t) / rim, 0, 1); az = np.arctan2(d[..., 0], -d[..., 1])
    uv = .5 * rho[..., None] * np.stack([np.sin(az), np.cos(az)], -1)

    def tap(img, scale):
        n = img.shape[0]; q = uv * scale
        return ni.map_coordinates(img, [(q[..., 1] % 1) * n, (q[..., 0] % 1) * n], order=1, mode='grid-wrap')
    mask = (tap(maps['mask'], FAR_MASK_UV[0]) + tap(maps['mask'], FAR_MASK_UV[1])) / 2
    tex_a = tap(maps['far'], FAR_ALPHA_UV)
    cover = FAR_COVER[0] + (FAR_COVER[1] - FAR_COVER[0]) * C['FarCloudCoverage'][0]
    dens = (1 - FAR_SHARP ** (255 * np.clip(mask - cover, 0, 1))) * tex_a
    out_c, out_a = 0, 0
    for side in ('', 'Back'):
        thin = C[f'FarCloud{side}ThinColour'] * C['FarCloudThinIntensity'][0]; dense = C[f'FarCloud{side}DenseColour']
        ca, ea = C[f'FarCloud{side}CentreAlpha'][0], C[f'FarCloud{side}EdgeAlpha'][0]
        a = dens * (ca + (ea - ca) * rho ** C['FarCloudAlphaPower'][0])
        col = thin + (dense - thin) * dens[..., None]
        # back first, then front over it, as the shader composites them
        out_c, out_a = (col * a[..., None], a) if side == 'Back' else (out_c, out_a)
        if side == '':
            front = (col, a)
    col, a = front
    tot = a + out_a * (1 - a)
    rgb = (col * a[..., None] + out_c * (1 - a)[..., None]) / np.maximum(tot, 1e-6)[..., None]
    # the cap ends 6 degrees below the horizon
    return rgb, tot * (z >= zr)


def close_clouds(d, C, maps, sun):
    """CloseCloud_VS/PS, front layer (CloseCloudBackAlpha is 0 in Colorado): linear colour and alpha."""
    z = d[..., 2]; w = np.clip((np.degrees(np.arcsin(np.clip(z, -1, 1))) - DOME_LOW) / DOME_SPAN, 0, 1)
    # FH1's dome azimuth is atan2(z, x) in its axes, and its z is BeamNG's -y
    u = (np.arctan2(-d[..., 1], d[..., 0]) / (2 * np.pi)) % 1
    h, wd = maps['band'].shape
    uv = [(1 - w) * (h - 1), ((1 - u + CLOSE_U0) % 1) * wd]
    lf = C['CloseCloudFrontAlpha'][0]
    a, dens, amb, top, bot = (ni.map_coordinates(maps[k], uv, order=1, mode='grid-wrap') * lf for k in ('band', 'density', 'amb', 'top', 'bot'))
    s = .5 + .5 * np.clip(d @ sun, -1, 1)
    light = lambda name, power: C[f'CloseCloud{name}Colour'] * C[f'CloseCloud{name}Intensity'][0] * (s ** C[f'CloseCloud{power}'][0])[..., None]
    dense = C['CloseCloudDenseColour'] * C['CloseCloudDenseColourIntensity'][0]; thin = C['CloseCloudThinColour'] * C['CloseCloudThinColourIntensity'][0]
    col = (dense + (thin - dense) * amb[..., None] + light('Back', 'BackPower') * (1 - dens)[..., None]
           + light('Top', 'TopPower') * (top ** C['CloseCloudTopMaskPower'][0])[..., None]
           + light('Bottom', 'BottomPower') * (bot ** C['CloseCloudBottomMaskPower'][0])[..., None])
    ta, ba = C['CloseCloudTopAlpha'][0], C['CloseCloudBottomAlpha'][0]
    return col, a * (ta + (ba - ta) * (1 - w) ** C['CloseCloudAlphaCurve'][0])


def scene(d, C, maps, sun):
    """What FH1 stores for the sky pass: sky, far clouds, close clouds, blended over each other on their sqrt values. Returns linear (squared)."""
    v = np.sqrt(sky(d, C, sun))
    for col, a in (far_clouds(d, C, maps), close_clouds(d, C, maps, sun)):
        v = v * (1 - a[..., None]) + np.sqrt(np.maximum(col, 0)) * a[..., None]
    return v ** 2


def key_of(C, maps, sun):
    """The bake's stand-in for FH1's scene luminance: mean linear luminance of the sky between the horizon and KEY_TOP degrees."""
    az, el = np.meshgrid(np.linspace(0, 2 * np.pi, 180, endpoint=False), np.radians(np.linspace(.5, KEY_TOP, 30)))
    d = np.stack([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)], -1)
    return float((scene(d, C, maps, sun) @ [.2126, .7152, .0722]).mean())


def sky_pass(d, C, maps, sun):
    """The scene the bake shows: FH1's sky pass above the horizon, and below it the sky alone as it is at the horizon (see GROUND_FADE)."""
    level = d.copy(); level[..., 2] = np.maximum(level[..., 2], 0); level /= np.linalg.norm(level, axis=-1, keepdims=True)
    return np.where((d[..., 2] >= 0)[..., None], scene(d, C, maps, sun), sky(level, C, sun))


def bloom_template():
    t = ET.fromstring(gamezip.read('dynamicpost.zip', 'Tracks/Colorado/Bloom_Templates.xml'))
    a = {k.lower(): float(v) for k, v in next(e for e in t if e.get('Name') == zone()['BloomTemplate']).attrib.items() if k != 'Name'}
    return a['cutoff'], a['scale'], [a.get(f'weight{i}', 1.0) for i in range(5)]


def bloom(C, maps, sun, ev):
    """FH1's bloom of the sky, linear, as an equirectangular panorama (PANO degrees a pixel, rows from +90 to -90 elevation)."""
    from scipy.ndimage import gaussian_filter1d
    h, w = int(180 / PANO), int(360 / PANO)
    el, az = np.radians(90 - (np.arange(h) + .5) * PANO), np.radians((np.arange(w) + .5) * PANO)
    el, az = np.meshgrid(el, az, indexing='ij')
    d = np.stack([np.sin(az) * np.cos(el), np.cos(az) * np.cos(el), np.sin(el)], -1)
    x = sky_pass(d, C, maps, sun) * 2 ** ev
    cutoff, scale, weights = bloom_template()
    knee = cutoff * C['BloomCutoffMultiplier'][0]; scale *= C['BloomScaleMultiplier'][0]
    lower, upper = C['BloomLowerGradientMultiplier'][0], C['BloomUpperGradientMultiplier'][0]
    L = np.maximum(x @ [.2125, .7154, .0721], 1e-6)[..., None]
    e = np.maximum(x * np.maximum(lower * L, upper * L + (lower - upper) * knee) / L - 1 / 255, 0) * scale
    texel = BLOOM_FOV / (BLOOM_PX / 4)  # degrees per bright-pass texel
    out = weights[0] * .2 * e; level = e; sigma = 0.0; cos = np.maximum(np.cos(el[:, 0]), .05)
    for k in range(4):
        texel *= 2; sigma = np.hypot(sigma, BLUR_SIGMA * texel)
        level = gaussian_filter1d(e, sigma / PANO, axis=0, mode='nearest')
        level = np.stack([gaussian_filter1d(level[r], sigma / PANO / cos[r], axis=0, mode='wrap') for r in range(h)])
        out = out + weights[k + 1] * .2 * level
    return out


def pano_at(pano, d):
    """Sample an equirectangular panorama from bloom() at unit directions d."""
    h, w = pano.shape[:2]
    row = (90 - np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1)))) / PANO - .5
    col = (np.degrees(np.arctan2(d[..., 0], d[..., 1])) % 360) / PANO - .5
    return np.stack([ni.map_coordinates(pano[..., i], [row, col], order=1, mode='grid-wrap') for i in range(3)], -1)


def zone():
    """The FH1 post-processing zone at the level's centre: its attributes, or Inner_Festival's where no zone covers it."""
    zs = ET.parse(gamepath.RIBBON + 'PostProcessingZones_Safe.xml').getroot(); x, y = CENTRE[0], -CENTRE[1]

    def inside(t):
        (x1, y1), (x2, y2), (x3, y3) = t; den = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / den; b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / den
        return a >= 0 and b >= 0 and a + b <= 1
    hit = [z for z in zs for t in z if inside([(float(q.get('posX')), float(q.get('posZ'))) for q in t])]
    return dict((hit or [z for z in zs if z.get('Name') == 'Inner_Festival'])[0].attrib)


def lut(name):
    """A 16^3 grading LUT from dynamicpost.zip as [blue][green][red] -> rgb, 0..1."""
    a = np.frombuffer(gamezip.read('dynamicpost.zip', 'ColourGradingMaps/' + name), 'u1', offset=128).reshape(16, 16, 16, 4) / 255
    return a[..., [2, 1, 0]]


def grading(C, Z):
    """This bake's blended LUT and its colour settings (template values times their TimeOfDayA multipliers)."""
    ident = np.stack(np.meshgrid(*[np.arange(16) / 15] * 3, indexing='ij')[::-1], -1)  # [b][g][r] -> (r, g, b)
    table = np.clip(ident + C['DayColorGradeAmount'][0] * (lut(Z['ColorGradingMap']) - ident)
                    + C['NightColorGradeAmount'][0] * (lut(Z['ColorGradingMapNight']) - ident), 0, 1)
    t = {e.get('Name'): {k: float(v) for k, v in e.attrib.items() if k != 'Name'}
         for e in ET.fromstring(gamezip.read('dynamicpost.zip', 'Tracks/Colorado/ColorSettings_Templates.xml'))}[Z['ColorSettings']]
    m = lambda k, n=1: C[k][:n] if k in C else np.ones(n)
    return {'lut': table, 'contrast': t['Contrast'] * m('ContrastMultiplier')[0], 'brightness': t['Brightness'],
            'desat': t['DesatAmount'] * m('DesatAmountMultiplier')[0],
            'lum': np.array([t['DesatLuminosityR'], t['DesatLuminosityG'], t['DesatLuminosityB']]) * m('DesatLuminosityMultiplier', 3),
            'post': np.array([t['DesatPostModR'], t['DesatPostModG'], t['DesatPostModR']]) * m('DesatPostModMultiplier', 3),
            'bias': t['ExposureBias']}


def grade(c, G):
    """FH1's grade of linear tone-mapped colour c: LUT, then contrast, brightness, desaturation and post-mod, saturated."""
    ix = np.clip(c, 0, 1) * 15; i0 = np.minimum(ix.astype(int), 14); f = ix - i0
    out = 0
    for db in (0, 1):
        for dg in (0, 1):
            for dr in (0, 1):
                wgt = ((f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1]) * (f[..., 2] if db else 1 - f[..., 2]))[..., None]
                out = out + wgt * G['lut'][i0[..., 2] + db, i0[..., 1] + dg, i0[..., 0] + dr]
    out = (out - .5) * (1 + G['contrast']) + .5 + G['brightness']
    out = out * G['desat'] + (1 - G['desat']) * (out @ G['lum'])[..., None]
    return np.clip(out * G['post'], 0, 1)


def bake(d, C, R, maps, sun, stars, moon, ev, P, glow, G):
    """One cube face: FH1's sky pass plus its bloom (glow, from bloom()) through its tone map and grade (G, from grading()), as sRGB bytes, then
    the stars, moon and ground bounce."""
    x = (sky_pass(d, C, maps, sun) + pano_at(glow, d)) * 2 ** ev
    col = linear(np.sqrt(grade(np.clip(hable(x, P[0]) / hable(P[0]['white'], P[0]), 0, 1), G)) * 255)
    col = col + stars[..., None] * C['StarFade'][0]
    if moon is not None:
        m = moon_layer(d, C, moon)
        if m is not None:
            col = col * (1 - m[1]) + m[0] * m[1]
    # Ground bounce well below the horizon, for reflections. HLBottomColour barely changes at night, so it dims with FH1's horizon
    el = np.degrees(np.arcsin(np.clip(d[..., 2], -1, 1)))
    g = np.clip((el - GROUND_FADE[0]) / (GROUND_FADE[1] - GROUND_FADE[0]), 0, 1)[..., None]
    dim = min(1, float((C['AtmosphereBottomColour'] / np.maximum(R['AtmosphereBottomColour'], 1e-3)).mean() * C['SkyGain'][0] / R['SkyGain'][0]))
    col = col * (1 - g) + C['HLBottomColour'] * 2 * dim * g
    return (srgb(col) * 255 + .5).astype('u1')


def zones():
    """FH1's post-processing zones as BeamNG x, y triangles, with fog density and bloom cutoff and scale relative to the level's own."""
    t = lambda n: {e.get('Name'): e.attrib for e in ET.fromstring(gamezip.read('dynamicpost.zip', f'Tracks/Colorado/{n}_Templates.xml'))}
    fog, bloom = t('Fog'), t('Bloom'); out = []
    for z in ET.parse(gamepath.RIBBON + 'PostProcessingZones_Safe.xml').getroot():
        b = bloom[z.get('BloomTemplate')]
        out.append({'name': z.get('Name'), 'fog': round(float(fog[z.get('Fog')]['Density']) / FOG_REF, 3), 'bloomCutoff': float(b['Cutoff']),
                    'bloomScale': float(b['Scale']), 'lerp': float(z.get('LerpTime')),
                    'tris': [[[round(float(p.get('posX')) - CENTRE[0], 1), round(float(p.get('posZ')) + CENTRE[1], 1)] for p in tri] for tri in z]})
    return out


def table():
    """Bake index, from 1, for each quarter hour of BeamNG's clock from midnight: the bake that took over last, wrapping at midnight."""
    since = (np.arange(96)[:, None] / 4 - np.array([b[2] for b in BAKES])[None, :]) % 24
    return [int(i) + 1 for i in since.argmin(1)]


def sun_path():
    """FH1's sun for fh1Extras.lua: [azimuth clockwise from north, elevation] in degrees every SUN_STEP minutes of BeamNG's clock from midnight,
    false where FH1's disc is the moon and BeamNG's own sun takes over. BeamNG hours map to FH1 minutes piecewise, each bake's minute in the
    middle of the hours it's shown, so its glow stays nearest the sun across its span"""
    hours = sorted(b[2] for b in BAKES)
    mids = sorted(((h + ((hours + [hours[0] + 24])[hours.index(h) + 1] - h) / 2) % 24, m) for m, _, h in BAKES)
    xs = [h for h, _ in mids]; ys = np.unwrap([m for _, m in mids], period=1440)
    assert np.all(np.diff(ys) > 0), ys
    xs = [xs[-1] - 24] + xs + [xs[0] + 24]; ys = [ys[-1] - 1440] + list(ys) + [ys[0] + 1440]
    out = []
    for h in np.arange(0, 24, SUN_STEP / 60):
        m = float(np.interp(h, xs, ys)) % 1440
        if not DAY[0] <= m <= DAY[1]:
            out.append(False); continue
        s = sun_dir(m)
        out.append([round(float(np.degrees(np.arctan2(s[0], s[1]))) % 360, 2), round(float(np.degrees(np.arcsin(s[2]))), 2)])
    return out


level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
items = level / 'main' / 'MissionGroup' / 'items.level.json'
if not items.exists():
    sys.exit(f'{items} is missing: run build3.py first')
R = curves(MINUTE)
out = level / 'art' / 'skies' / 'fh1_sky'; out.mkdir(parents=True, exist_ok=True)
# Faces depend on this code and the level's FH1 zone (its grade and bloom), so they're cached per both
CACHE = Path('../ddscache') / ('sky-' + hashlib.md5(b''.join(Path(f).read_bytes() for f in (__file__, dds.__file__, tex.__file__)) + zone()['Name'].encode()).hexdigest()[:12])
if not all((CACHE / f'{names(tag)[2]}_{i}.color.dds').exists() for _, tag, _ in BAKES for i in range(1, 7)):
    band, density = xds('CloseCloudAlphaDensityFront0.xds'); top, bot = xds('CloseCloudTopBottomAmbientFront0.xds')
    maps = {'band': band, 'density': density, 'amb': xds('CloseCloudAmbientFront0.xds')[1], 'top': top, 'bot': bot,
            'far': xds('FarCloudMapFront0.xds')[0], 'mask': xds('FarCloudMask.xds')[0]}
    b = gamezip.read('realtimesky.zip', MOON); moon = tex.decode(b[52:], 256, 256, 0x54).astype('f4') / 255
    moon[..., :3] = linear(moon[..., :3] * 255)
    stars = star_faces(FACE); dirs = list(directions(FACE)); CACHE.mkdir(parents=True, exist_ok=True)
materials = {}; bakes = []; P = post(); Z = zone(); print('zone', Z['Name'], Z['ColorGradingMap'], Z['BloomTemplate'], Z['ColorSettings'])
for minute, tag, _ in BAKES:
    C = curves(minute); material, cubemap, stem = names(tag); faces = []; ev = None
    for i in range(1, 7):
        p = out / f'{stem}_{i}.color.dds'; c_ = CACHE / p.name
        if not c_.exists():
            if ev is None:
                G = grading(C, Z); ev = exposure(key_of(C, maps, sun_dir(minute)), P, G['bias'])
                glow = bloom(C, maps, sun_dir(minute), ev); print(tag, 'EV', round(ev, 2))
            dds.save(c_.with_suffix('.tmp'), bake(dirs[i - 1], C, R, maps, sun_dir(minute), stars[i - 1], moon, ev, P, glow, G), peak=C['StarFade'][0] > .1)
            os.replace(c_.with_suffix('.tmp'), c_)
        shutil.copyfile(c_, p); faces.append(f'/levels/{LVL}/art/skies/fh1_sky/{p.name}')
    materials[material] = {'name': material, 'mapTo': 'unmapped_mat', 'class': 'Material', 'Stages': [{}, {}, {}, {}], 'cubemap': cubemap, 'materialTag0': 'beamng'}
    materials[cubemap] = {'name': cubemap, 'class': 'CubemapData', 'cubeFace': faces}
    # FH1's fog is about 2.7 times denser at night than by day
    bakes.append({'name': tag, 'material': material, 'cubemap': cubemap, 'hideBox': tag in BEAMNG_SKY, 'fog': round(float(C['FogDensity'][0] / R['FogDensity'][0]), 3)})
    print(tag, minute, 'stars', round(float(C['StarFade'][0]), 2), 'moon', round(float(C['SunObjectMoon'][0] * C['SunObjectAlpha'][0]), 2))
(out / 'main.materials.json').write_text(json.dumps(materials, indent=2))
(level / 'fh1_sky.json').write_text(json.dumps({'bakes': bakes, 'quarterHours': table(), 'sunStep': SUN_STEP, 'sunPath': sun_path(), 'fogDensity': SCENE['theLevelInfo']['fogDensity'], 'zones': zones()}))
with mission.edit(items) as rows:
    for o in rows:
        o.update(SCENE.get(o.get('name'), {}))
    if not any(o.get('name') == SKYBOX['name'] for o in rows):
        rows.append(SKYBOX)
print('done', out)
