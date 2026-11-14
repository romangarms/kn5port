"""Where the tables sit in a track's FPVS (<track>_00.pvs), walked from the texture table's end instead of fixed offsets.

After the last 29-byte texture record (p3.py): u32 260, u32 shader count, then each shader path as u32 length + bytes;
u32 draw count and the 18-byte draw records (p5.py); u32 mesh count and the per-mesh records (p7.py).
Colorado: 173 shaders at 0x861e3, 62,173 draws at 0x87d51, 14,560 meshes at 0x1990df. Colorado Dirt: 150, 24,154, 9,979.
"""
import struct

import gamepath


def tables(b=None, R=None):
    """{'shaders': (offset, count), 'draw': (offset, count), 'mesh': (offset, count)}, each offset at its first record"""
    import pickle
    b = b if b is not None else open(gamepath.PVS, 'rb').read()
    R = R if R is not None else pickle.load(open('../pvsrec.pkl', 'rb'))
    o = R[-1][0] + 29
    tag, ns = struct.unpack_from('>II', b, o)
    if tag != 260:
        raise ValueError(f'{gamepath.PVS}: expected 260 after the texture table at {o:#x}, found {tag}')
    o += 8; out = {'shaders': (o, ns)}
    for _ in range(ns):
        o += 4 + struct.unpack_from('>I', b, o)[0]
    nd = struct.unpack_from('>I', b, o)[0]; out['draw'] = (o + 4, nd); o += 4 + 18 * nd
    out['mesh'] = (o + 4, struct.unpack_from('>I', b, o)[0])
    return out
