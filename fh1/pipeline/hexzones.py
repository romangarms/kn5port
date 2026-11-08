"""FH1's streaming zones: Colorado_00.hex places each .pvsz zone on a 100 m hex grid, and each zone lists what FH1 draws from it.
Layout is in docs/formats.md. Zone z is opposite to mesh z, as in colorado.nav."""
import struct
import numpy as np
import gamezip


def hexes():
    """-> {zone number: (x, zone z) hex centre}, hex radius"""
    b = open(gamezip.MEDIA + 'tracks/colorado/Ribbon_00/Colorado_00.hex', 'rb').read()
    _, _, R, X0, Z0, cnt, W, H = struct.unpack_from('>4sI3fIII', b, 0)
    g = np.frombuffer(b, '>u4', W * H, 0x20).astype(np.int64); out = {}
    for k in np.where(g < cnt)[0]:
        r, c = divmod(int(k), W)       # flat-topped hexes, odd columns half a row up
        out[int(g[k])] = (X0 + (c + .5) * 1.5 * R, Z0 + (r + .5 + .5 * (c % 2)) * np.sqrt(3) * R)
    return out, R


def drawn(zone):
    """draw ids FH1 draws from a zone: its id list with bit 31 set"""
    b = open(f'../x/__R00Z{zone:05d}.pvsz', 'rb').read()
    n = struct.unpack_from('>I', b, 0)[0]
    return [v & 0x7fffffff for v in struct.unpack_from('>%dI' % n, b, 4) if v >> 31]
