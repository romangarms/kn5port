"""FH1 static collision: *.fiz tiles. Big endian. Header words 9 to 11 are triangle count, vertex offset, and roughly the triangle offset.
Vertices are 16 bytes: x, y, z, then a word this reader ignores. Triangles are 32 bytes: flags, three byte offsets into the file, a normal, 0xffffff."""
import os
import numpy as np

X = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'x')


def read(name):
    """(vertices n,3 in game axes, triangles m,3, flags m)"""
    b = open(os.path.join(X, name), 'rb').read()
    h = np.frombuffer(b, '>u4', 16, 0); nt, vo, to = (int(v) for v in h[9:12])
    while True:             # the vertex count is short by a few, so the triangles start where the first record's offsets fit
        w = np.frombuffer(b, '>u4', 4, to)[1:]
        if ((w >= vo) & (w < to) & ((w - vo) % 16 == 0)).all():
            break
        to += 16
    v = np.frombuffer(b, '>f4', (to - vo) // 4, vo).reshape(-1, 4)[:, :3].astype('f8')
    t = np.frombuffer(b, '>u4', nt * 8, to).reshape(-1, 8)
    return v, (t[:, 1:4].astype(np.int64) - vo) // 16, t[:, 0].astype(np.int64)


def names():
    return sorted(f for f in os.listdir(X) if f.endswith('.fiz'))
