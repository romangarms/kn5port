"""Simple collision shapes for placed props, which BeamNG copies into physics once per object at about 41.5 bytes a triangle.
A shape's own geometry is the model frame, z up. Everything returns a triangle mesh wound outward, as BeamNG collision is one-sided:
    boxes(P)    one box, or two stacked when the bottom is much wider than the top (barrier feet, a pole's crossarm)
    generic(P, T) a convex hull for small near-convex things (rocks, logs), else a skyline of boxes (buildings, tents) turned to their walls
    rails(cells) walls swept along world-mesh barriers, guardrails and fences"""
import collections
import numpy as np
from scipy.spatial import ConvexHull, QhullError
from daewrite import box_mesh

BOXMIN = 0.1                    # metres: flat signs still get a solid slab
HULL_POINTS = 24                # a hull of this many points has at most 44 triangles
CONVEX = 0.7                    # skyline volume over hull volume above which a hull fits well enough
HULL_MAX = 8.                   # metres across: a building's hull slopes from the eaves to the ground, out in front of its walls
MAX_BOXES = 40


def sample(p_, step=.25):
    """points over triangles (n, 3, 3), step metres apart along each one's longest edge, corners included"""
    out = []
    e_ = np.linalg.norm(p_ - p_[:, [1, 2, 0]], axis=2).max(1); K = np.clip(np.ceil(e_ / step), 1, 40).astype(int)
    for k in np.unique(K):
        w_ = np.array([(a, b, k - a - b) for a in range(k + 1) for b in range(k + 1 - a)], 'f8') / k
        out.append(np.einsum('kj,tjd->tkd', w_, p_[K == k]).reshape(-1, 3))
    return np.concatenate(out) if out else np.zeros((0, 3))


def triangles(groups, collide, farcut):
    """the triangles (n, 3, 3) that collide today"""
    out = [g[1][np.asarray(g[4]).reshape(-1, 3)] for g in groups if collide in g[0][1] or collide == 2 and farcut in g[0][1]]
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def points(T):
    """points spread over triangles (n, 3, 3), so thin walls and long triangles are seen"""
    return sample(T) if len(T) else np.zeros((0, 3))


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
    point is its lowest surface, so eaves, carport roofs and the lintel over a doorway float and can be driven under. Each box is trimmed to
    its columns' points, so a coarse grid doesn't push it past the walls"""
    lo = P.min(0); ij = np.floor((P[:, :2] - lo[:2]) / cell).astype(int); nx, ny = ij.max(0) + 1
    zmin = np.full((nx, ny), np.inf); zmax = np.full((nx, ny), -np.inf); at = (ij[:, 0], ij[:, 1])
    np.minimum.at(zmin, at, P[:, 2]); np.maximum.at(zmax, at, P[:, 2])
    xy0 = np.full((nx, ny, 2), np.inf); xy1 = np.full((nx, ny, 2), -np.inf)       # each column's points' extent, which its box is trimmed to
    for k in (0, 1):
        np.minimum.at(xy0[..., k], at, P[:, k]); np.maximum.at(xy1[..., k], at, P[:, k])
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
            a_ = xy0[i:i1 + 1, j:j1 + 1].reshape(-1, 2).min(0); b_ = np.maximum(xy1[i:i1 + 1, j:j1 + 1].reshape(-1, 2).max(0), a_ + BOXMIN)
            out.append((np.r_[a_, z0], np.r_[b_, z1]))
    return out, float(sum((zmax[full] - zmin[full])) * cell * cell)


RAIL_STEP = 1.     # metres between points: corners set a piece's width, and joints bridge its ends
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


LINK = 1.5; LINK_COS = np.cos(np.radians(50))     # segment ends this close and this straight join into one wall


def _segments(tris):
    """centre-line pieces (start, end, half thickness, z bottom, z top) of one cell's barrier triangles (n, 3, 3)"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    _, inv = np.unique(np.rint(tris.reshape(-1, 3) * 100).astype(np.int64), axis=0, return_inverse=True); inv = inv.reshape(-1, 3)
    e_ = np.r_[inv[:, [0, 1]], inv[:, [1, 2]]]; n_ = inv.max() + 1
    _, lab = connected_components(coo_matrix((np.ones(len(e_)), (e_[:, 0], e_[:, 1])), shape=(n_, n_)), directed=False)
    out = []; tl = lab[inv[:, 0]]
    for k in np.unique(tl):
        _rail(sample(tris[tl == k], RAIL_STEP), out)
    return [(c + a * lo[0] + b * (lo[1] + hi[1]) / 2, c + a * hi[0] + b * (lo[1] + hi[1]) / 2, (hi[1] - lo[1]) / 2, lo[2], hi[2]) for c, a, b, lo, hi in out]


def _loop_len(link, e):
    n = 1; f = link[e] ^ 1
    while f in link and link[f] // 2 != e // 2 and n < 4:
        f = link[f] ^ 1; n += 1
    return n + 1


def _chains(S):
    """order segments into chains by joining ends that meet: lists of (segment, reversed), and whether each chain closes"""
    from scipy.spatial import cKDTree
    E = np.array([e for s_ in S for e in s_[:2]]); D = []
    for s_ in S:
        d = s_[1] - s_[0]; d = d / max(np.linalg.norm(d), 1e-9); D += [-d, d]       # each end's outward direction
    D = np.array(D); link = {}
    pairs = sorted(cKDTree(E).query_pairs(LINK), key=lambda q: np.linalg.norm(E[q[0]] - E[q[1]]))
    for i, j in pairs:
        g = E[j] - E[i]       # the other end must lie ahead of this one, not back along its own piece
        if i // 2 != j // 2 and i not in link and j not in link and D[i] @ D[j] < -LINK_COS and g @ D[i] > -.3 and g @ D[j] < .3:
            link[i] = j; link[j] = i
    seen = set(); out = []
    for k in range(len(S)):
        if k in seen:
            continue
        e = 2 * k; start = None      # walk back from segment k's start to the chain's first end
        while e in link and link[e] // 2 != k:
            e = link[e] ^ 1
        closed = e in link and _loop_len(link, e) >= 4      # a shorter loop folds back on itself: sweep it open
        e = 2 * k if closed else e; ch = []
        while True:
            s_ = e // 2; ch.append((s_, e % 2 == 1)); seen.add(s_)
            f = e ^ 1
            if f not in link or link[f] // 2 in seen:
                break
            e = link[f]
        out.append((ch, closed))
    return out


def _sweep(S, ch, closed):
    """one wall along a chain: four corners at each joint, mitred, wound outward"""
    seg = [(S[k][1], S[k][0]) if r else (S[k][0], S[k][1]) for k, r in ch]; W = [S[k][2:] for k, _ in ch]
    Q = []; A = []
    if not closed:
        Q.append(seg[0][0]); A.append(W[0])
    for i in range(len(seg) - (0 if closed else 1)):
        j = (i + 1) % len(seg); Q.append((seg[i][1] + seg[j][0]) / 2)
        A.append((max(W[i][0], W[j][0]), min(W[i][1], W[j][1]), max(W[i][2], W[j][2])))
    if not closed:
        Q.append(seg[-1][1]); A.append(W[-1])
    Q = np.array(Q); m = len(Q); V = []
    for i in range(m):
        p_, n_ = Q[(i - 1) % m] if closed or i else None, Q[(i + 1) % m] if closed or i < m - 1 else None
        ds = [d / max(np.linalg.norm(d), 1e-9) for d in ((Q[i] - p_) if p_ is not None else None, (n_ - Q[i]) if n_ is not None else None) if d is not None]
        t = sum(ds); t = t / max(np.linalg.norm(t), 1e-9); nrm = np.array([-t[1], t[0]])
        w = A[i][0] / max(min(abs(np.array([-d[1], d[0]]) @ nrm) for d in ds), .5)
        for side, z in ((1, A[i][1]), (1, A[i][2]), (-1, A[i][2]), (-1, A[i][1])):
            V.append(np.r_[Q[i] + side * w * nrm, z])
    V = np.array(V); T = []; c_ = V.mean(0)

    def quad(a, b, c, d, out_):
        for tri in ((a, b, c), (a, c, d)):
            P = V[list(tri)]
            T.append(tri if np.cross(P[1] - P[0], P[2] - P[0]) @ out_ > 0 else tri[::-1])
    for i in range(m - (0 if closed else 1)):
        j = (i + 1) % m; a, b = 4 * i, 4 * j; mid = (V[a:a + 4].mean(0) + V[b:b + 4].mean(0)) / 2
        for q in ((0, 1), (1, 2), (2, 3), (3, 0)):         # left side, top, right side, bottom
            f = [a + q[0], a + q[1], b + q[1], b + q[0]]
            quad(*f, V[f].mean(0) - mid)
    if not closed:
        for i, sgn in ((0, -1), (m - 1, 1)):
            a = 4 * i; d = Q[min(i + 1, m - 1)] - Q[max(i - 1, 0)]; d = np.r_[d / max(np.linalg.norm(d), 1e-9), 0]
            quad(a, a + 1, a + 2, a + 3, sgn * d)
    return V, np.array(T).ravel()


POST_L = 1.5; POST_D = .6; WALL_L = 2.      # metres: a piece this short, this close to a wall this long, is one of its posts


def _posts(S, chains):
    """fold armco posts into their rail. Each post is its own piece reaching the ground about 0.5 m below the beam, so a bumper slid
    under the beam's wall and caught on every post. Posts are dropped and their wall goes down to the ground, by their median drop"""
    from scipy.spatial import cKDTree
    L = [np.linalg.norm(s_[1] - s_[0]) for s_ in S]
    wall = [i for i, (c, _) in enumerate(chains) if sum(L[k] for k, _ in c) > WALL_L]
    post = [i for i, (c, _) in enumerate(chains) if len(c) == 1 and L[c[0][0]] < POST_L]
    if not wall or not post:
        return chains
    pts = []; own = []
    for i in wall:
        for k, _ in chains[i][0]:
            pts.append(np.linspace(S[k][0], S[k][1], max(int(L[k] / .25), 2))); own += [(i, k)] * len(pts[-1])
    d, j = cKDTree(np.concatenate(pts)).query(np.array([(S[chains[i][0][0][0]][0] + S[chains[i][0][0][0]][1]) / 2 for i in post]))
    drop = collections.defaultdict(list); gone = set()
    for i, d_, j_ in zip(post, d, j):
        if d_ < POST_D:
            w, k = own[j_]; drop[w].append(S[k][3] - S[chains[i][0][0][0]][3]); gone.add(i)
    for w, dz in drop.items():
        dz = max(float(np.median(dz)), 0.)
        for k, _ in chains[w][0]:
            S[k] = S[k][:3] + (S[k][3] - dz, S[k][4])
    return [c for i, c in enumerate(chains) if i not in gone]


def rails(cells, pool=None):
    """walls along barriers, guardrails and fences. cells: {key: triangles (n, 3, 3) in world space, z up}, to {key: (vertices, triangle
    indices)} and (segments, chains). Each connected piece is halved along its long axis until a piece is at most RAIL_W thick and RAIL_L
    long; pieces whose ends meet, across cells too, become one swept wall, so a car sliding along finds no box ends to catch on.
    pool: an executor to fit the cells' pieces in, which is most of the work"""
    S = []; key = []; ks = [k for k, t in cells.items() if len(t)]
    for k, s_ in zip(ks, (pool.map if pool else map)(_segments, [cells[k] for k in ks])):
        S += s_; key += [k] * len(s_)
    chains = _posts(S, _chains(S)) if S else []; out = {}
    for ch, closed in chains:
        V, T = _sweep(S, ch, closed); k = key[ch[0][0]]
        V0, T0 = out.get(k, (np.zeros((0, 3)), np.zeros(0, np.int64)))
        out[k] = (np.r_[V0, V], np.r_[T0, T + len(V0)])
    return out, (len(S), len(chains))


WALL_SHARE = .12               # a wall direction holding this share of the wall area gets its own frame
VOTE_CELL = 1.; VOTE_R = 4.    # metres: points in a cell take the direction holding most wall area this near


def wall_dirs(T):
    """the main wall directions of triangles (n, 3, 3), as angles in degrees mod 90, most wall area first (at most 3), each triangle's
    index into them (-1 for floors and roofs), and the triangles' areas"""
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]); a = np.linalg.norm(n, axis=1); n = n / np.maximum(a, 1e-12)[:, None]; a = a / 2
    w = (np.abs(n[:, 2]) < .3) & (a > 1e-4)
    ang = np.degrees(np.arctan2(n[:, 1], n[:, 0])) % 90
    h = np.bincount(np.floor(ang[w]).astype(int) % 90, a[w], 90); tot = h.sum(); h = sum(np.roll(h, k) for k in range(-2, 3)); out = []      # 5 degree windows
    while tot > 0 and len(out) < 3:
        k = int(np.argmax(h))
        if out and h[k] < WALL_SHARE * tot:
            break
        d = (ang - k - .5 + 45) % 90 - 45; m = w & (np.abs(d) < 3)       # the peak's mean, to a fraction of a degree
        out.append(float(k + .5 + np.average(d[m], weights=a[m])) % 90 if m.any() else k + .5)
        h[[(k + d_) % 90 for d_ in range(-10, 11)]] = 0
    lab = np.full(len(T), -1)
    if out:
        lab[w] = np.argmin([np.abs((ang[w] - d + 45) % 90 - 45) for d in out], 0)
    return out, lab, a


def _rot(deg):
    c, s_ = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1.]])


def _skyline_fit(P):
    ext = np.ptp(P, 0); cell = float(np.clip(max(ext[:2]) / 24, .5, 2.))
    while True:
        bs, vol = skyline(P, cell)
        if len(bs) <= MAX_BOXES or cell > 8:
            return bs, vol
        cell *= 1.4


def oriented_skyline(P, T):
    """skyline boxes in the frame of the walls. A building turned in its model frame, or with wings at an angle (Clear Springs' diner and
    arcade, one 96 m prop), got a staircase of boxes reaching metres past its walls into the car park. Points go to the wall direction
    holding most wall area near them, and each direction's points get their own skyline -> (vertices, triangle indices), column volume"""
    dirs, lab, area = wall_dirs(T) if len(T) else ([], None, None)
    if not dirs:
        bs, vol = _skyline_fit(P)
        return box_mesh(bs), vol
    lp = np.zeros(len(P), int)
    if len(dirs) > 1:
        from scipy.spatial import cKDTree
        g = np.floor(P[:, :2] / VOTE_CELL).astype(int); cells, inv = np.unique(g, axis=0, return_inverse=True)
        cc = (cells + .5) * VOTE_CELL; c_ = T.mean(1)[:, :2]; score = np.zeros((len(cells), len(dirs)))
        for i in range(len(dirs)):
            wa = area[lab == i]
            for k, nb in enumerate(cKDTree(c_[lab == i]).query_ball_point(cc, VOTE_R)):
                score[k, i] = wa[nb].sum()
        lp = np.argmax(score, 1)[inv.ravel()]
    V = []; I = []; vol = 0.; nv = 0
    for i, d in enumerate(dirs):
        Q = P[lp == i]
        if len(Q) < 4:
            continue
        R = _rot(d); bs, v_ = _skyline_fit(Q @ R); vol += v_       # Q @ R turns by -d, into the walls' frame
        v, t = box_mesh(bs); I.append(t + nv); V.append(v @ R.T); nv += len(v)       # a turn keeps the winding
    return (np.concatenate(V), np.concatenate(I)), vol


def generic(P, T=()):
    """('hull' or 'skyline', (vertices, triangle indices)) for any prop, or None when neither works. T: the triangles P was spread over"""
    if len(P) < 4:
        return None
    ext = np.ptp(P, 0); cm, vol = oriented_skyline(P, np.asarray(T, float).reshape(-1, 3, 3))
    h = hull(P) if max(ext[:2]) < HULL_MAX else None
    if h is not None:
        try:
            hv = ConvexHull(P).volume
        except QhullError:
            hv = 0
        if hv > 0 and vol / hv > CONVEX:
            return 'hull', h
    return 'skyline', cm
