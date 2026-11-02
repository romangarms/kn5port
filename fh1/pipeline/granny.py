"""Meshes from the Granny files in animatedobjects.zip. These are stored flat, without usable pointers, so vertices and triangles are found by shape. Layout is in docs/formats.md.
UVs aren't decoded, so this gives geometry only."""
import struct
import numpy as np
from scipy.spatial import cKDTree
import gamezip

MAX_COORD = 80; MAX_EDGE = 6; MIN_RUN = 12


def vertices(b):
    """12-byte rows at the end of the file: 3 half-floats of position, then data that isn't decoded. Returns their file offset and positions in BeamNG axes, unflipped."""
    size = struct.unpack('>I', b[0x50:0x54])[0]; sec1 = len(b) - size
    o = len(b) - 12; bad = 0; first = len(b)
    while o >= sec1:
        v = np.frombuffer(b, '>f2', 3, o).astype('f8')
        if np.isfinite(v).all() and 1e-3 < np.abs(v).max() < MAX_COORD:
            first = o; bad = 0
        else:
            bad += 1
            if bad > 8:
                break
        o -= 12
    n = (len(b) - first) // 12
    p = np.frombuffer(b, '>f2', n * 6, first).astype('f8').reshape(-1, 6)[:, :3]
    return sec1, first, np.c_[p[:, 0], p[:, 2], p[:, 1]]


def triangles(b, sec1, first, p):
    """Every stretch of the index area that reads as triangles with short edges, in file order for each phase. The vertex maps stored beside them count upward, so those are left out."""
    I = np.frombuffer(b, '>u2', (first - sec1) // 2, sec1).astype('i8'); out = []
    for phase in range(3):
        at = np.arange(phase, len(I) - 2, 3); t = np.c_[I[at], I[at + 1], I[at + 2]]
        good = (t < len(p)).all(1) & (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        edge = np.full(len(t), np.inf); q = t[good]
        edge[good] = np.maximum.reduce([np.linalg.norm(p[q[:, a]] - p[q[:, c]], axis=1) for a, c in ((0, 1), (1, 2), (2, 0))])
        short = edge < MAX_EDGE; run = 0
        for i, s in enumerate(np.r_[short, False]):
            if s:
                run += 1
                continue
            if run >= MIN_RUN:
                r = t[i - run:i]
                if (np.diff(r.ravel()) == 1).mean() < .5:
                    out.append(r)
            run = 0
    return out


def upright(p, tris):
    """Rotate a vehicle to stand level with its nose along +Y, from its own shape: the flattest axis is up, the mesh is mirror symmetric about its length, the landing gear is down, and the wings sit ahead of the middle."""
    used = np.unique(np.concatenate(tris)); c = (p[used].min(0) + p[used].max(0)) / 2; q = p[used] - c
    v = np.linalg.svd(q - q.mean(0), full_matrices=False)[2]
    side, fwd, up = sorted(v, key=lambda x: -np.ptp(q @ x))
    if np.dot(np.cross(side, fwd), up) < 0:
        up = -up
    R = np.array([side, fwd, up]); r = q @ R.T

    def asymmetry(deg):
        a = np.radians(deg); x = r[:, 0] * np.cos(a) - r[:, 1] * np.sin(a); y = r[:, 0] * np.sin(a) + r[:, 1] * np.cos(a)
        x = x - (x.min() + x.max()) / 2; w = np.c_[x, y, r[:, 2]]
        return cKDTree(w).query(w * (-1, 1, 1))[0].mean()
    best = min(np.arange(-90, 90, 1.), key=asymmetry); a = np.radians(best)
    R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]) @ R; r = q @ R.T
    y = r[:, 1] - (r[:, 1].min() + r[:, 1].max()) / 2
    if y[np.abs(r[:, 0]) > .6 * np.abs(r[:, 0]).max()].mean() < 0:           # wing tips are ahead of the middle
        R = R * ((-1,), (-1,), (1,))
    h = (q @ R.T)[:, 2]; h = h - np.median(h)
    if h.max() > -h.min():                                                   # the gear hangs furthest from the body
        R = R * ((-1,), (1,), (-1,))
    return (p - c) @ R.T


def smooth_normals(p, t):
    n = np.zeros_like(p); f = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    for c in range(3):
        np.add.at(n, t[:, c], f)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)


def mesh(name):
    """Positions and triangles of a model's first whole mesh, which opens the index area, set upright with only the used vertices kept. Faces point outward."""
    b = gamezip.read('animatedobjects.zip', name)
    sec1, first, p = vertices(b); r = triangles(b, sec1, first, p)[0]
    used, inv = np.unique(r, return_inverse=True); q = upright(p, [r])[used]; t = inv.reshape(-1, 3)
    if (np.cross(q[t[:, 0]], q[t[:, 1]]) * q[t[:, 2]]).sum() < 0:
        t = t[:, ::-1]
    return q, t
