"""FM4 track collision, Ribbon_NN/<track>_track_NN.col (docs/fm4.md, Collision). Big endian.
Header: u32 cells x, cells z, float x0, z0, x1, z1 (bounds), a little-endian float cell size twice (100), two float 0.01, u32 file size,
u32 2, magic 0xABCD1234, float cell size twice, 8, 8, little-endian 10, 10, u32 occupied cells n, u32 0x54, u32 first block offset (0x800), then from 0x58 n entries of 24 bytes
(index, cell, offset, size, 0, offset + size).
A cell block: u32 vertex count nv, triangle count nt, 0x40 (vertex offset), triangle offset, u32 sub-grid nx, nz, float origin x, z,
float sub-cell size x, z (10), float 1 / size x, z, u32 sub-grid offset, 0.
Vertices: 32 bytes, float3 position in the draw frame (x east, y up, z north), 0, u32 packed (not decoded), 12 zero bytes.
Triangles: 32 bytes, u16 surface, u16 0, three u32 byte offsets of its vertices within the cell, float3 face normal, 0.
python fm4col.py <file.col>          prints cells, triangles and surfaces"""
import collections
import struct
import sys

import numpy as np


def cells(path):
    """-> [(positions (n, 3), triangles (m, 3) indexing them, surface ids (m,), face normals (m, 3))] per occupied cell, in the draw frame
    (x east, y up, z north: mesh z flipped, as fm4pvs draws); surface ids index Media/physics/surfaceTypes.xml's SurfaceTypes"""
    b = open(path, 'rb').read(); out = []
    for k in range(struct.unpack_from('>I', b, 0x4c)[0]):
        _, cell, off, size, _, _ = struct.unpack_from('>6I', b, 0x58 + 24 * k)
        nv, nt, vo, to = struct.unpack_from('>4I', b, off)
        v = np.frombuffer(b, '>f4', nv * 8, off + vo).reshape(nv, 8)[:, :3].astype('f8')
        t = np.frombuffer(b, '>u4', nt * 8, off + to).reshape(nt, 8)
        idx = (t[:, 1:4].astype(np.int64) - vo) // 32
        if idx.min() < 0 or idx.max() >= nv: raise ValueError(f'cell {cell}: vertex offsets out of range')
        out.append((v, idx, (t[:, 0] >> 16).astype(int), t[:, 4:7].copy().view('>f4').astype('f8')))
    return out


def read(path):
    """every cell at once: (positions, triangles, surface ids, normals)"""
    C = cells(path); at = np.cumsum([0] + [len(c[0]) for c in C])
    return (np.concatenate([c[0] for c in C]), np.concatenate([c[1] + a for c, a in zip(C, at)]), np.concatenate([c[2] for c in C]),
            np.concatenate([c[3] for c in C]))


if __name__ == '__main__':
    P, T, S, N = read(sys.argv[1])
    e = P[T[:, 1]] - P[T[:, 0]]; f = P[T[:, 2]] - P[T[:, 0]]; cr = np.cross(e, f); area = np.linalg.norm(cr, axis=1) / 2
    agree = np.mean(np.sum(cr * N, 1) > 0)
    print(f'{len(P)} vertices, {len(T)} triangles, {area.sum() / 1e4:.1f} ha; stored normals agree with (b - a) x (c - a) on {agree:.3f}')
    print('bounds', P.min(0).round(1), P.max(0).round(1))
    A = collections.Counter()
    for s, a, ny in zip(S, area, N[:, 1]): A[s] += a
    for s, a in sorted(A.items(), key=lambda x: -x[1]):
        k = S == s; print(f'  surface {s:3d}: {k.sum():6d} triangles, {a:9.0f} m2, median normal y {np.median(N[k, 1]):.2f}')
