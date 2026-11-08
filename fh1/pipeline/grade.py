"""Saturation and contrast baked into a built level's colour textures, because BeamNG 0.40 ignores colour ramps.
Run after build3.py: python grade.py [fh1_colorado|fh1_redrock]. Grades from copies in art/textures_ungraded, so rerunning with new values is safe."""
import struct, sys, os
from pathlib import Path
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import gamezip

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
# FH1's Reservoir grade, its most used zone map, as per-channel ramps: its 3D LUT is within 2/255 of that. It warms and lifts midtones.
# Saturation is set against screenshots: BeamNG showed a median of 0.1 where FH1 shows 0.3. Contrast turns about PIVOT, in sRGB.
# FH1's textures average about 0.3: a 1.3 gamma with 1.2 contrast about 0.45 took grass from 49 to 19
LUT = 'ColourGradingMaps/Reservoir.dds'; SATURATION = 1.4; CONTRAST = 1.1; PIVOT = .3
LUMA = np.array([.2126, .7152, .0722])


def ramps():
    """16x16x16 BGRA LUT with red along x, reduced to one curve per channel."""
    a = np.frombuffer(gamezip.read('dynamicpost.zip', LUT), 'u1', offset=128).reshape(16, 16, 16, 4) / 255
    return np.stack([a[..., 2].mean((0, 1)), a[..., 1].mean((0, 2)), a[..., 0].mean((1, 2))], 1)


AXIS = np.linspace(0, 1, 16); RAMP = ramps()


def grade(c):
    """u16 565 colours to graded 565 colours."""
    rgb = np.stack([(c >> 11) / 31, ((c >> 5) & 63) / 63, (c & 31) / 31], -1)
    y = (rgb @ LUMA)[..., None]
    rgb = np.clip(y + (rgb - y) * SATURATION, 0, 1)
    rgb = np.stack([np.interp(rgb[..., i], AXIS, RAMP[:, i]) for i in range(3)], -1)
    rgb = np.clip(PIVOT + (rgb - PIVOT) * CONTRAST, 0, 1)
    r, g, b = np.rint(rgb * [31, 63, 31]).astype('u4').T
    return (r << 11) | (g << 5) | b


def blocks(a):
    """Grade DXT1 endpoints in place. Colour order picks a block's mode, so swapped endpoints need their indices remapped to keep it."""
    c0 = a[:, 0] & 0xffff; c1 = a[:, 0] >> 16; idx = a[:, 1].copy()
    four = c0 > c1
    n0 = TABLE[c0]; n1 = TABLE[c1]
    swap4 = four & (n0 < n1); idx[swap4] ^= 0x55555555             # 0-1 and 2-3 trade places
    same = four & (n0 == n1); idx[same] = 0                        # equal endpoints are one colour, and index 3 would turn transparent
    swap3 = ~four & (n0 > n1)                                      # 0 and 1 trade places, 2 is the midpoint and 3 stays transparent
    idx[swap3] ^= ~(idx[swap3] >> 1) & 0x55555555
    flip = swap4 | swap3
    n0[flip], n1[flip] = n1[flip], n0[flip]
    a[:, 0] = n0 | (n1 << 16); a[:, 1] = idx


TABLE = grade(np.arange(1 << 16, dtype='u4'))       # every 565 colour graded once, then looked up

level = Path(beamng.find_mods()) / 'unpacked' / LVL / 'levels' / LVL
tex = level / 'art' / 'textures'; keep = level / 'art' / 'textures_ungraded'
if not tex.exists():
    sys.exit(f'{tex} is missing: run build3.py first')
keep.mkdir(exist_ok=True)
done = skipped = 0
for f in sorted(tex.glob('*.color.dds')):
    src = keep / f.name; first = not src.exists()
    b = (f if first else src).read_bytes()
    if b[84:88] != b'DXT1':
        skipped += 1; continue
    if first:
        os.replace(f, src)      # the build's file becomes the ungraded copy, and the graded one is written in its place
    a = np.frombuffer(b, '<u4', offset=128).reshape(-1, 2).copy()
    blocks(a)
    f.write_bytes(b[:128] + a.astype('<u4').tobytes()); done += 1
print('done', done, 'textures', skipped, 'skipped ->', tex)
