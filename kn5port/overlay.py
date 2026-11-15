"""Separate coplanar layers (road + patches, lines, dirt/skid decals) so BeamNG doesn't z-fight them.

AC (and the games tracks get ripped from) lay several meshes exactly on top of each other: base asphalt, then
patch, line and grime/decal meshes. AC draws opaque meshes in file order, then transparent ones, with a
less-or-equal depth test, so a later layer at exactly the same depth reliably wins. BeamNG gives no such
guarantee (meshes live in different shapes and draw batches), so the layers flicker against each other.

`OverlayLifter` reproduces AC's draw order in geometry: every drawn mesh is checked against the meshes AC
draws before it. Where one of its triangles lies on an earlier surface (centroid inside an earlier
triangle, within a few cm of its height), the mesh is a layer on top of that surface. Its level is one more
than the highest level under it, and the vertices of those triangles are raised by `level * step`. Meshes that
only touch along a seam (road split into segments, shoulder next to road) never overlap and are left alone.

Hidden physics copies are not part of this: they are invisible, so nothing can fight with them.
"""
import numpy as np

STEP = 0.02          # metres between stacked layers
TOL_BELOW = 0.03     # a layer may sit up to this far below the surface it covers (rip noise)
MIN_UP = 0.5         # only roughly horizontal triangles count (walls don't carry decals that fight)
MAX_PAIRS = 4_000_000


def _key(cx, cy):
    return (cx + (1 << 30)) * (1 << 31) + (cy + (1 << 30))


def _tri_geom(pos, idx):
    t = pos[idx.reshape(-1, 3)].astype(np.float64)
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    ln = np.linalg.norm(n, axis=1)
    up = np.abs(n[:, 2]) / np.maximum(ln, 1e-12)
    return t, up, ln


class OverlayLifter:
    def __init__(self, step=STEP, tol_below=TOL_BELOW):
        self.step, self.tol_below = step, tol_below
        self.records = []                     # (pos, idx, transparent) of the kn5 being converted
        self.prior_t = np.zeros((0, 3, 3), np.float32)   # surfaces of kn5s already written (unlifted heights)
        self.prior_level = np.zeros(0, np.int16)
        self.stats = {"meshes": 0, "triangles": 0, "max_level": 0}

    def add(self, pos, idx, transparent=False):
        """Register a drawn mesh (in AC file order). `pos` is modified in place by `resolve`."""
        if self.step > 0 and len(idx):
            self.records.append((pos, idx, bool(transparent)))

    def resolve(self):
        """Lift the layers of the meshes added since the last call, then index them for the next kn5."""
        recs = self.records
        self.records = []
        if not recs:
            return
        # AC draws opaque meshes first, then transparent ones, each in file order
        order = sorted(range(len(recs)), key=lambda i: (recs[i][2], i))
        recs = [recs[i] for i in order]
        R = len(recs)

        # triangles of every record: geometry, owner (rank), local index
        T, own, loc = [], [], []
        for r, (pos, idx, _) in enumerate(recs):
            t, up, ln = _tri_geom(pos, idx)
            keep = (up > MIN_UP) & (ln > 1e-8)
            T.append(t[keep]); own.append(np.full(keep.sum(), r, np.int32)); loc.append(np.nonzero(keep)[0])
        T = np.concatenate(T); own = np.concatenate(own); loc = np.concatenate(loc)
        if not len(T):
            return
        n_prior = len(self.prior_t)
        allT = np.concatenate([self.prior_t.astype(np.float64), T]) if n_prior else T
        all_own = np.concatenate([np.full(n_prior, -1, np.int32), own])

        # Candidate pairs (centroid of one of this kn5's triangles, earlier triangle) from a multi-resolution
        # spatial hash: each triangle goes into the finest grid where its bbox covers at most a few cells, so dense
        # road meshes give few candidates per query and big parking-lot triangles are still indexed.
        C = T.mean(1)
        tlo, thi = allT[:, :, :2].min(1), allT[:, :, :2].max(1)
        tzlo, tzhi = allT[:, :, 2].min(1), allT[:, :, 2].max(1)
        size = (thi - tlo).max(1)
        cell0 = float(np.clip(np.median(size), 0.25, 4.0))
        grid = np.clip(np.ceil(np.log2(np.maximum(size / cell0, 1.0))), 0, 12).astype(np.int64)
        hit_q, hit_t, hit_dz = [], [], []
        for g in np.unique(grid):
            cell = cell0 * 2.0 ** g
            sel = np.nonzero(grid == g)[0]
            lo = np.floor(tlo[sel] / cell).astype(np.int64)
            hi = np.floor(thi[sel] / cell).astype(np.int64)
            nx, ny = hi[:, 0] - lo[:, 0] + 1, hi[:, 1] - lo[:, 1] + 1
            nc = nx * ny                                     # at most 4 (2 x 2) by construction
            j = np.repeat(np.arange(len(sel)), nc)
            within = np.arange(len(j)) - np.repeat(np.cumsum(nc) - nc, nc)
            key = _key(lo[j, 0] + within % nx[j], lo[j, 1] + within // nx[j])
            o = np.argsort(key, kind="stable")
            skey, stid = key[o], sel[j[o]]
            qkey = _key(np.floor(C[:, 0] / cell).astype(np.int64), np.floor(C[:, 1] / cell).astype(np.int64))
            a = np.searchsorted(skey, qkey, "left")
            cnt = np.searchsorted(skey, qkey, "right") - a
            qs_all = np.nonzero(cnt)[0]
            csum = np.cumsum(cnt[qs_all])
            start = 0
            while start < len(qs_all):
                base = csum[start - 1] if start else 0
                end = max(int(np.searchsorted(csum, base + MAX_PAIRS, "right")), start + 1)
                qs = qs_all[start:end]
                start = end
                c = cnt[qs]
                q = np.repeat(qs, c)
                t = stid[np.repeat(a[qs], c) + (np.arange(len(q)) - np.repeat(np.cumsum(c) - c, c))]
                Pz = C[q, 2]
                m = (all_own[t] < own[q]) & (Pz > tzlo[t] - self.tol_below) & (Pz < tzhi[t] + self.step)
                q, t = q[m], t[m]                   # only surfaces AC draws earlier (prior kn5s: -1), near in z
                P = C[q]; tri = allT[t]
                v0 = tri[:, 1, :2] - tri[:, 0, :2]; v1 = tri[:, 2, :2] - tri[:, 0, :2]; v2 = P[:, :2] - tri[:, 0, :2]
                den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
                ok = np.abs(den) > 1e-10
                den = np.where(ok, den, 1.0)
                b1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
                b2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
                b0 = 1 - b1 - b2
                # inside (or on an edge of) the earlier triangle. A centroid is always well inside its own
                # triangle, so meshes that only meet at a seam never qualify
                eps = -1e-5
                ok &= (b0 > eps) & (b1 > eps) & (b2 > eps)
                dz = P[:, 2] - (b0 * tri[:, 0, 2] + b1 * tri[:, 1, 2] + b2 * tri[:, 2, 2])
                ok &= (dz > -self.tol_below) & (dz < self.step)
                hit_q.append(q[ok]); hit_t.append(t[ok]); hit_dz.append(dz[ok])
        hq = np.concatenate(hit_q) if hit_q else np.zeros(0, np.int64)
        ht = np.concatenate(hit_t) if hit_t else np.zeros(0, np.int64)
        hdz = np.concatenate(hit_dz) if hit_dz else np.zeros(0)

        # Levels are per triangle: one more than the highest triangle directly under it. A per-mesh level would
        # carry the deepest stack anywhere under a mesh to all of it (a line running the whole straight that
        # crosses the grid boxes once floated 25 cm along its full length).
        tri_level = np.zeros(len(allT), np.int32)
        tri_level[:n_prior] = self.prior_level
        lifted_meshes = lifted_tris = 0
        if len(hq):
            qo = own[hq]
            srt = np.argsort(qo, kind="stable")
            hq, ht, hdz, qo = hq[srt], ht[srt], hdz[srt], qo[srt]
            bounds = np.searchsorted(qo, np.arange(R + 1))
            for r in range(R):
                s, e = bounds[r], bounds[r + 1]
                if s == e:
                    continue
                q, under, dz = hq[s:e], tri_level[ht[s:e]], hdz[s:e]
                tris, inv = np.unique(q, return_inverse=True)
                lvl = np.zeros(len(tris), np.int32)
                np.maximum.at(lvl, inv, under + 1)
                sink = np.zeros(len(tris))                  # how far the triangle dips below what it covers
                np.maximum.at(sink, inv, -dz)
                tri_level[n_prior + tris] = lvl
                pos, idx, _ = recs[r]
                tv = idx.reshape(-1, 3)[loc[tris]]
                lift = np.zeros(len(pos))
                np.maximum.at(lift, tv.ravel(), np.repeat(lvl * self.step + np.maximum(sink, 0.0), 3))
                pos[:, 2] += lift.astype(pos.dtype)
                lifted_meshes += 1
                lifted_tris += len(tris)
            self.stats["meshes"] += lifted_meshes
            self.stats["triangles"] += lifted_tris
            self.stats["max_level"] = max(self.stats["max_level"], int(tri_level[n_prior:].max()))

        # index this kn5's surfaces for the kn5s that follow, at their original heights plus their level, which is
        # the same frame the triangles of the current kn5 are compared in
        self.prior_t = np.concatenate([self.prior_t, T.astype(np.float32)])
        self.prior_level = np.concatenate([self.prior_level, tri_level[n_prior:].astype(np.int16)])
