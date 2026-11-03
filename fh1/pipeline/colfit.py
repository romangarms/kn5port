"""Simple collision shapes for placed props, which BeamNG copies into physics once per object at about 41.5 bytes a triangle.
A shape's own geometry is the model frame, z up. Everything returns a triangle mesh wound outward, as BeamNG collision is one-sided:
    boxes(P)    one box, or two stacked when the bottom is much wider than the top (barrier feet, a pole's crossarm)
    generic(P)  a convex hull for small near-convex things (rocks, logs), else a skyline of boxes (buildings, tents)
    rails(tris) chains of turned boxes along world-mesh barriers, guardrails and fences"""
import numpy as np
from scipy.spatial import ConvexHull, QhullError
from daewrite import box_mesh

BOXMIN = 0.1                    # metres: flat signs still get a solid slab
HULL_POINTS = 24                # a hull of this many points has at most 44 triangles
CONVEX = 0.7                    # skyline volume over hull volume above which a hull fits well enough
HULL_MAX = 8.                   # metres across: a building's hull slopes from the eaves to the ground, out in front of its walls
MAX_BOXES = 40


def sample(p_):
    """points over triangles (n, 3, 3), 25 cm apart along each one's longest edge"""
    out = []
    e_ = np.linalg.norm(p_ - p_[:, [1, 2, 0]], axis=2).max(1); K = np.clip(np.ceil(e_ / .25), 1, 40).astype(int)
    for k in np.unique(K):
        w_ = np.array([(a, b, k - a - b) for a in range(k + 1) for b in range(k + 1 - a)], 'f8') / k
        out.append(np.einsum('kj,tjd->tkd', w_, p_[K == k]).reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3))


def points(groups, collide, farcut):
    """points spread over the triangles that collide today, so thin walls and long triangles are seen"""
    out = [sample(g[1][np.asarray(g[4]).reshape(-1, 3)]) for g in groups if collide in g[0][1] or collide == 2 and farcut in g[0][1]]
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


RAIL_W = .5; RAIL_L = 8.; RAIL_MIN = .3      # metres: a box this thin and short follows a curved rail; none thinner, so wheels don't pass through


def _rail(P, out, depth=0):
    xy = P[:, :2]; c = xy.mean(0)
    w, v = np.linalg.eigh(np.cov((xy - c).T) if len(P) > 2 else np.eye(2))
    a = v[:, 1]; b = np.array([-a[1], a[0]])          # b is a turned a quarter left, so the frame is a rotation and winding keeps
    s = (xy - c) @ a; t = (xy - c) @ b
    if depth < 24 and len(P) > 8 and (np.ptp(t) > RAIL_W and np.ptp(s) > RAIL_MIN or np.ptp(s) > RAIL_L):
        m = (s.min() + s.max()) / 2
        for half in (P[s < m], P[s >= m]):
            if len(half):
                _rail(half, out, depth + 1)
        return
    tc = (t.min() + t.max()) / 2; th = max(np.ptp(t), RAIL_MIN) / 2
    lo = np.array([s.min() - .05, tc - th, P[:, 2].min()]); hi = np.array([s.max() + .05, tc + th, max(P[:, 2].max(), P[:, 2].min() + BOXMIN)])
    out.append((c, a, b, lo, hi))


def rails(tris):
    """oriented boxes along barriers, guardrails and fences: triangles (n, 3, 3) in world space, z up, to (vertices, triangle indices).
    Each connected piece is halved along its long axis until a box is at most RAIL_W thick and RAIL_L long, so chains follow curves"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    if not len(tris):
        return np.zeros((0, 3)), np.zeros(0, np.int64)
    _, inv = np.unique(np.rint(tris.reshape(-1, 3) * 100).astype(np.int64), axis=0, return_inverse=True); inv = inv.reshape(-1, 3)
    e_ = np.r_[inv[:, [0, 1]], inv[:, [1, 2]]]; n_ = inv.max() + 1
    _, lab = connected_components(coo_matrix((np.ones(len(e_)), (e_[:, 0], e_[:, 1])), shape=(n_, n_)), directed=False)
    out = []
    tl = lab[inv[:, 0]]
    for k in np.unique(tl):
        _rail(sample(tris[tl == k]), out)
    V = []; T = []
    for c, a, b, lo, hi in out:
        bp, bi = box_mesh([(lo, hi)])
        V.append(np.c_[c + bp[:, :1] * a + bp[:, 1:2] * b, bp[:, 2]]); T.append(bi + 8 * len(T))
    return np.concatenate(V), np.concatenate(T)


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
