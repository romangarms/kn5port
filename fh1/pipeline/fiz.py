"""FH1 static collision: *.fiz tiles. Big endian. Offsets in the file count from byte 0x20. Header words 8 to 11 are vertex count,
triangle count, vertex offset and triangle offset; words 16 to 22 describe a 10 m lookup grid after the triangles, which this reader skips.
Vertices are 16 bytes: x, y, z, then a word this reader ignores. Triangles are 32 bytes: flags, three vertex offsets, a unit normal
(the face's real side: the winding disagrees with it on about 42% of triangles), then a word that is 0xffffff on most."""
import os
import numpy as np

X = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'x')
BASE = 0x20


def read(name):
    """(vertices n,3 in game axes, triangles m,3, flags m, normals m,3)"""
    b = open(os.path.join(X, name), 'rb').read()
    nv, nt, vo, to = (int(v) for v in np.frombuffer(b, '>u4', 4, 32))
    v = np.frombuffer(b, '>f4', nv * 4, BASE + vo).reshape(-1, 4)[:, :3].astype('f8')
    r = np.frombuffer(b, '>u4', nt * 8, BASE + to).reshape(-1, 8)
    n = r[:, 4:7].copy().view('>f4').astype('f8')
    return v, (r[:, 1:4].astype(np.int64) - vo) // 16, r[:, 0].astype(np.int64), n


def names():
    return sorted(f for f in os.listdir(X) if f.endswith('.fiz'))
