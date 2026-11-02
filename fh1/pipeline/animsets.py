"""FH1's anim_proc sets: where each animated model stands, and which model it is. Layout is in docs/formats.md."""
import glob, itertools, re
import numpy as np

SETS = '../x/__R00G*.pgeo'
# metres a record may stand outside its set's box. A set also holds stray rows that read as a record at the origin
NEAR = 500


def rows(b):
    """(16 floats: rotation rows, position and 1, model index in the set) of each record. Counts in the header don't always cover every record, so records
    are found by shape: rotation rows with a zero fourth value, then position and 1, near the set's bounding box. The model index is the word after them."""
    lo, hi = box(b); F = np.frombuffer(b[:len(b) // 4 * 4], '>f4').astype('f8'); W = np.frombuffer(b[:len(b) // 4 * 4], '>u4'); out = {}
    for i in range(0x80 // 4, len(F) - 16):
        r = F[i:i + 16]
        if r[3] != 0 or r[7] != 0 or r[11] != 0 or r[15] != 1 or not np.isfinite(r).all() or (r[12:15] < lo - NEAR).any() or (r[12:15] > hi + NEAR).any():
            continue
        R = np.array([r[0:3], r[4:7], r[8:11]]); n = np.linalg.norm(R, axis=1)
        if ((n > .05) & (n < 50)).all() and np.abs((R / n[:, None]) @ (R / n[:, None]).T - np.eye(3)).max() < .02:
            out[tuple(np.round(r[12:15], 1))] = (r, int(W[i + 16]))
    return list(out.values())


def anim_sets():
    """(set name, bytes) of each anim_proc set"""
    for f in sorted(glob.glob(SETS)):
        b = open(f, 'rb').read()
        m = re.search(rb'anim_proc_(\w+?)\0', b[:0x200])
        if b[:4] == b'OEGP' and m:
            yield m.group(1).decode(), b


def records(group):
    """Records of the sets whose name starts with group, as 16 floats"""
    return [r for name, b in anim_sets() if re.match(re.escape(group) + r'_\d', name) for r, _ in rows(b)]


def box(b):
    f = np.frombuffer(b[0x10:0x2c], '>f4').astype('f8')
    return f[0:3], f[4:7]


def catalog():
    """(name, min, max) of the track's own copies of the ANIM models. Each set's name is followed by an index, as ANIM_WaterSprinkler17"""
    out = []
    for f in sorted(glob.glob(SETS)):
        b = open(f, 'rb').read(0x200)
        m = re.search(rb'Anim_(ANIM_\w+?)\0', b)
        if b[:4] == b'OEGP' and m:
            out.append((m.group(1).decode(),) + box(b))
    return out


def placed(model):
    """Records of one ANIM model, e.g. ANIM_WaterSprinkler. The sets don't name their models, but each set's bounding box is exactly the union of its
    records' model boxes, so models are found by fitting those."""
    cat = catalog(); out = []
    for _, b in anim_sets():
        rs = rows(b); lo, hi = box(b); ks = sorted({k for _, k in rs})
        if not rs:
            continue
        corners = [[np.array(list(itertools.product(*zip(c[1], c[2])))) @ np.array([r[0:3], r[4:7], r[8:11]]) + r[12:15] for c in cat] for r, _ in rs]
        # Only models that fit inside the set's box are tried for each index
        fits = [[m for m in range(len(cat)) if all((corners[j][m].min(0) >= lo - .05).all() and (corners[j][m].max(0) <= hi + .05).all() for j, (_, k_) in enumerate(rs) if k_ == k)] for k in ks]
        best = None
        for combo in itertools.product(*fits):
            P = np.concatenate([corners[j][combo[ks.index(k)]] for j, (_, k) in enumerate(rs)])
            e = np.abs(np.r_[P.min(0) - lo, P.max(0) - hi]).max()
            if best is None or e < best[0]:
                best = (e, combo)
        if best is None or best[0] > .05:
            continue
        out += [r for r, k in rs if re.fullmatch(re.escape(model) + r'\d+', cat[best[1][ks.index(k)]][0])]
    return out
