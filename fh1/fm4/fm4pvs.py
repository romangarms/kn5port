"""FM4 FPVS, version 27 (<track>_NN.pvs, one per ribbon). Tables in order, located from the first shader path. See docs/fm4.md.
- textures: u32 count, 28-byte records (id, parent, scale u, scale v, offset u, offset v, kind); id is the _0x<id>.bix file, or a
  sub-rectangle of `parent` (lightmap atlases) when no file has that id
- shaders: u32 count, then u32 length + 'shaders\\track\\<name>' each
- draws: u32 count, 68-byte records: u16 mesh (the Tsukubaout.<mesh>.rmb.bin file) at 0, float3 position at 38, 3x3 float16 rotation at 50
- meshes: u32 count, records of (u32 n, n texture keys, u32 m, m keys, 80 bytes holding a matrix). A material slot indexes the first key
  list; a key indexes the texture table."""
import math
import struct

import numpy as np


def _plausible(b, o, n):
    """n texture records from o + 4 look real: kind under 256, and scales and offsets 0 or between 1e-6 and 1e6 in size"""
    for k in range(n):
        r = struct.unpack_from('>2I4fI', b, o + 4 + 28 * k)
        if r[6] > 255 or any(not math.isfinite(f) or (f != 0 and not 1e-6 <= abs(f) <= 1e6) for f in r[2:6]):
            return False
    return True


def tables(b):
    i = b.find(b'shaders' + bytes([92]) + b'track') - 8
    ns = struct.unpack_from('>I', b, i)[0]
    # the count sits before its n records, so look back for a word equal to n; other data can match too (MapleValley_00 has a false 7680,
    # whose first record holds a denormal float), so keep the largest n whose records are all plausible
    fits = [n for n in range(1, i // 28) if struct.unpack_from('>I', b, i - 4 - 28 * n)[0] == n]
    nt = max(n for n in fits if _plausible(b, i - 4 - 28 * n, n)); o = i - 4 - 28 * nt
    T = [struct.unpack_from('>2I4fI', b, o + 4 + 28 * k) for k in range(nt)]
    o = i + 4; S = []
    for _ in range(ns):
        n = struct.unpack_from('>I', b, o)[0]; S.append(b[o + 4:o + 4 + n].decode('latin1')); o += 4 + n
    nd = struct.unpack_from('>I', b, o)[0]; o += 4; D = []
    for k in range(nd):
        r = b[o + 68 * k:o + 68 * (k + 1)]
        D.append(dict(mesh=struct.unpack_from('>H', r, 0)[0], pos=np.array(struct.unpack_from('>3f', r, 38)),
                      rot=np.frombuffer(r[50:68], '>f2').astype('f8').reshape(3, 3), raw=r))
    o += 68 * nd
    nm = struct.unpack_from('>I', b, o)[0]; o += 4; M = []
    for _ in range(nm):
        a = struct.unpack_from('>I', b, o)[0]; ka = struct.unpack_from('>%dI' % a, b, o + 4); o += 4 + 4 * a
        c = struct.unpack_from('>I', b, o)[0]; kb = struct.unpack_from('>%dI' % c, b, o + 4); o += 4 + 4 * c
        M.append(dict(tex=ka, tex2=kb, tail=b[o:o + 80])); o += 80
    return dict(version=struct.unpack_from('>I', b, 4)[0], textures=T, shaders=S, draws=D, meshes=M, end=o)
