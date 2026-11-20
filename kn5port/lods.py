"""AC LODs and cull distances as Torque detail levels.

Every AC mesh draws while the camera is between its lodIn and lodOut metres (lodOut 0 = no limit). Most track
geometry has a lodOut, often 80 to 500 m, and an object's lower LODs are separate meshes whose band starts where
the one before ends. Kunos overlaps the bands a little (HI to 75 m, LOW from 70 m), so a chain is linked by
overlapping bands on meshes of about the same size and place, not by equal numbers.

Torque picks a detail per shape, from the shape's pixel size: radius x PX / distance to the shape's centre. A
detail draws while the shape covers at least its size in pixels, and below its smallest size the shape draws
nothing, which is the cull (what a "nulldetail" node does in hand-made shapes). So:
  * a chain's meshes go in one shape, so its levels switch together, never both or neither;
  * objects with the same switch distances in one cell share a shape;
  * that cell shrinks with the first switch distance, as the engine measures from the shape's centre where AC
    measures from each mesh's own.
PX = 1250 is FH1's (fh1/docs/beamng-output.md): about 1440p at 60 degrees vertical. At lower resolutions, or with
BeamNG's mesh detail setting lowered, everything switches and vanishes nearer.
"""
import math
from collections import defaultdict

import numpy as np

PX = 1250.0
INF = math.inf
MIN_CELL = 8.0
STEP = 1.1          # switch distances are rounded to steps of 10% so near-equal chains share shapes


def quantize(d):
    return d if d == INF else float(round(STEP ** round(math.log(max(d, 1.0)) / math.log(STEP))))


def chains(bands, centres, radii):
    """bands: [(lod_in, lod_out)] with lod_out INF for none; centres (N,3), radii (N,). Links each object's LOD
    meshes. Returns ([(edges, [(mesh index, levels)])] per object, [indices of orphans]). edges run from 0 to the
    last distance anything draws to, INF when something always draws; level j draws from edges[j] to edges[j+1].
    An orphan is a lower LOD whose higher LOD wasn't found: it isn't kept, as before LODs were read."""
    n = len(bands)
    par = list(range(n))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i

    centres = np.asarray(centres, np.float64).reshape(-1, 3)
    radii = np.asarray(radii, np.float64)
    ends = [i for i, b in enumerate(bands) if b[1] < INF]
    sw = []                                         # (higher LOD, lower LOD, switch distance)
    if ends:
        eo = np.array([bands[i][1] for i in ends]); ei = np.array([bands[i][0] for i in ends])
        ce, re_ = centres[ends], radii[ends]
        for j, (a, _) in enumerate(bands):
            if a <= 0:
                continue
            d = np.linalg.norm(ce - centres[j], axis=1)
            ok = ((eo >= a) & (eo <= a * 1.25 + 10) & (ei < a) & (d <= 0.25 * np.maximum(re_, radii[j]) + 0.5) &
                  (re_ <= 2 * radii[j] + 1) & (radii[j] <= 2 * re_ + 1))
            for ii in np.nonzero(ok)[0]:
                par[find(ends[ii])] = find(j)
                sw.append((ends[ii], j, (eo[ii] + a) / 2))
    nb = [list(b) for b in bands]
    for a, b, v in sw:                              # both switch halfway through AC's overlap
        nb[a][1] = v; nb[b][0] = v
    comps = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)
    objects, orphans = [], []
    for c in comps.values():
        if min(nb[i][0] for i in c) > 0:
            orphans += c
            continue
        q = {i: (quantize(nb[i][0]) if nb[i][0] > 0 else 0.0, quantize(nb[i][1])) for i in c}
        edges = sorted({e for b in q.values() for e in b} | {0.0})
        members = []
        for i in c:
            a, b = q[i]
            lv = tuple(j for j in range(len(edges) - 1) if edges[j] >= a and edges[j + 1] <= b)
            if not lv:                              # narrower than the rounding: the level it overlaps most
                lv = (max(range(len(edges) - 1), key=lambda j: min(b, edges[j + 1]) - max(a, edges[j])),)
            members.append((i, lv))
        objects.append((tuple(edges), members))
    return objects, orphans


def cell_size(edges, base):
    """the grid cell an object with these edges goes in: base, halved until under half the first switch"""
    cs = base
    first = edges[1] if len(edges) > 1 else INF
    while cs > first / 2 and cs > MIN_CELL:
        cs /= 2
    return cs


def detail_sizes(radius, edges):
    """Torque detail sizes, largest first, one per level: each draws until the shape's centre is edges[j+1]
    away. The last is 1 (under a pixel) when edges end at INF. Sizes are whole pixels and kept strictly falling."""
    s = [1 if e == INF else max(1, int(round(PX * radius / e))) for e in edges[1:]]
    for j in range(len(s) - 2, -1, -1):
        s[j] = max(s[j], s[j + 1] + 1)
    return tuple(s)
