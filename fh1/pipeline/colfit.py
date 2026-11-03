"""Simple collision shapes for placed props, which BeamNG copies into physics once per object at about 41.5 bytes a triangle.
A shape's own geometry is the model frame, z up. Everything returns a triangle mesh wound outward, as BeamNG collision is one-sided:
    boxes(P)    one box, or two stacked when the bottom is much wider than the top (barrier feet, a pole's crossarm)
    generic(P)  a convex hull for small near-convex things (rocks, logs), else a skyline of boxes (buildings, tents)"""
import numpy as np
from scipy.spatial import ConvexHull, QhullError
from daewrite import box_mesh

BOXMIN = 0.1                    # metres: flat signs still get a solid slab
HULL_POINTS = 24                # a hull of this many points has at most 44 triangles
CONVEX = 0.7                    # skyline volume over hull volume above which a hull fits well enough
HULL_MAX = 8.                   # metres across: a building's hull slopes from the eaves to the ground, out in front of its walls
MAX_BOXES = 40


def points(groups, collide, farcut):
    """points spread over the triangles that collide today, about 4 per cell of `step` metres, so thin walls and long triangles are seen"""
    out = []
    for g in groups:
        if collide in g[0][1] or collide == 2 and farcut in g[0][1]:
            p_ = g[1][np.asarray(g[4]).reshape(-1, 3)]
            e_ = np.linalg.norm(p_ - p_[:, [1, 2, 0]], axis=2).max(1)
            for k in np.unique(np.clip(np.ceil(e_ / .25), 1, 40).astype(int)):       # 25 cm apart along the longest edge
                t_ = p_[np.clip(np.ceil(e_ / .25), 1, 40).astype(int) == k]
                w_ = np.array([(a, b, k - a - b) for a in range(k + 1) for b in range(k + 1 - a)], 'f8') / k
                out.append(np.einsum('kj,tjd->tkd', w_, t_).reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3))


def _box(Q):
    lo = Q.min(0); hi = Q.max(0); c = (lo + hi) / 2; h = np.maximum((hi - lo) / 2, BOXMIN / 2)
    return c - h, c + h


def _vol(bs):
    return sum(float(np.prod(hi - lo)) for lo, hi in bs)


def boxes(P):
    """one box, or two stacked boxes when that cuts the volume by 30%"""
    best = [_box(P)]
    z0, z1 = P[:, 2].min(), P[:, 2].max()
    for f in np.linspace(.1, .6, 11):
        zc = z0 + f * (z1 - z0); dn = P[P[:, 2] <= zc]; up = P[P[:, 2] >= zc]
        if len(dn) < 3 or len(up) < 3:
            continue
        bs = [_box(dn), _box(up)]
        bs = [(bs[0][0], np.r_[bs[0][1][:2], max(zc, bs[0][0][2] + BOXMIN)]), (np.r_[bs[1][0][:2], zc], bs[1][1])]      # abut at the cut
        if _vol(bs) < _vol(best) * (.7 if len(best) == 1 else 1):
            best = bs
    return box_mesh(best)


def hull(P):
    """convex hull of at most HULL_POINTS points, picked farthest first from the full hull, as triangles wound outward"""
    try:
        h = ConvexHull(P)
    except QhullError:
        return None
    V = P[h.vertices]
    if len(V) > HULL_POINTS:
        pick = [int(np.argmax(np.linalg.norm(V - V.mean(0), axis=1)))]; d = np.linalg.norm(V - V[pick[0]], axis=1)
        while len(pick) < HULL_POINTS:
            pick.append(int(np.argmax(d))); d = np.minimum(d, np.linalg.norm(V - V[pick[-1]], axis=1))
        V = V[pick]
    try:
        h = ConvexHull(V)               # on its own points, so the triangles index V
    except QhullError:
        return None
    T = h.simplices.copy(); c = V.mean(0)
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    flip = (n * (V[T].mean(1) - c)).sum(1) < 0; T[flip] = T[flip][:, [0, 2, 1]]
    return V, T.ravel()


def skyline(P, cell):
    """columns of `cell` metres from each column's lowest to highest point, merged into rectangles of matching height. A column's lowest
    point is its lowest surface, so eaves, carport roofs and the lintel over a doorway float and can be driven under"""
    lo = P.min(0); ij = np.floor((P[:, :2] - lo[:2]) / cell).astype(int); nx, ny = ij.max(0) + 1
    zmin = np.full((nx, ny), np.inf); zmax = np.full((nx, ny), -np.inf)
    np.minimum.at(zmin, (ij[:, 0], ij[:, 1]), P[:, 2]); np.maximum.at(zmax, (ij[:, 0], ij[:, 1]), P[:, 2])
    full = np.isfinite(zmin); tol = max(.3, (P[:, 2].max() - lo[2]) / 10)
    qa = np.round(zmin / tol); qb = np.round(zmax / tol); used = ~full; out = []
    for i in range(nx):
        for j in range(ny):
            if used[i, j]:
                continue
            k = (qa[i, j], qb[i, j]); j1 = j
            while j1 + 1 < ny and not used[i, j1 + 1] and (qa[i, j1 + 1], qb[i, j1 + 1]) == k:
                j1 += 1
            i1 = i
            while i1 + 1 < nx and all(not used[i1 + 1, q] and (qa[i1 + 1, q], qb[i1 + 1, q]) == k for q in range(j, j1 + 1)):
                i1 += 1
            used[i:i1 + 1, j:j1 + 1] = True
            z0 = zmin[i:i1 + 1, j:j1 + 1].min(); z1 = max(zmax[i:i1 + 1, j:j1 + 1].max(), z0 + BOXMIN)
            out.append((np.array([lo[0] + i * cell, lo[1] + j * cell, z0]), np.array([lo[0] + (i1 + 1) * cell, lo[1] + (j1 + 1) * cell, z1])))
    return out, float(sum((zmax[full] - zmin[full])) * cell * cell)


def generic(P):
    """('hull' or 'skyline', (vertices, triangle indices)) for any prop, or None when neither works"""
    if len(P) < 4:
        return None
    ext = np.ptp(P, 0); cell = float(np.clip(max(ext[:2]) / 24, .5, 2.))
    while True:
        bs, vol = skyline(P, cell)
        if len(bs) <= MAX_BOXES or cell > 8:
            break
        cell *= 1.4
    h = hull(P) if max(ext[:2]) < HULL_MAX else None
    if h is not None:
        try:
            hv = ConvexHull(P).volume
        except QhullError:
            hv = 0
        if hv > 0 and vol / hv > CONVEX:
            return 'hull', h
    return 'skyline', box_mesh(bs)
