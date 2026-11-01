"""All static placements from the zone files: align each zone's draw ids with its placement records."""
import struct, pickle, json, collections, re, time, sys
import numpy as np
from numba import njit
mt = np.load('../mt.npy'); T = mt['t'].astype(np.int64); FL = mt['f'].astype(np.int64)
F = pickle.load(open('../cat2.pkl', 'rb')); nm = {}; ext = {}
for c in F:
    no = int(c['f'].split('.')[1]); nm.setdefault(no, c['name'])
    ext[no] = max(ext.get(no, 0), abs(c['mn'][0]), abs(c['mx'][0]), abs(c['mn'][2]), abs(c['mx'][2]))
ISLOCAL = np.zeros(70000, 'i1')
for no, e in ext.items():
    ISLOCAL[no] = 1 if e < 150 else 0
H = np.array([0. if ((u >> 10) & 31) == 0 else (-1. if u & 0x8000 else 1.) * (1 + (u & 1023) / 1024) * 2. ** (((u >> 10) & 31) - 15) for u in range(65536)])
@njit(cache=True)
def scan(a, start, H):
    out = []; n = len(a); i = start
    while i + 30 <= n:
        # floats are big endian; test the rotation first, it is the rarest pattern
        m = np.empty(9)
        for k in range(9):
            m[k] = H[(int(a[i + 12 + 2 * k]) << 8) | int(a[i + 13 + 2 * k])]
        ok = True
        for r in range(3):
            l = m[3 * r] ** 2 + m[3 * r + 1] ** 2 + m[3 * r + 2] ** 2
            c = m[r] ** 2 + m[3 + r] ** 2 + m[6 + r] ** 2
            if l < .8 or l > 1.25 or c < .75 or c > 1.3:
                ok = False
                break
        if ok:
            out.append(i); i += 72
        else:
            i += 1
    return out
@njit(cache=True)
def align(A, B, Aid, Bid):
    """A: entry kind, B: record kind (1 placed, 0 at origin). Aid/Bid: draw id known from the live scan (or -1)."""
    n, m = len(A), len(B); S = np.zeros((n + 1, m + 1), np.int32); P = np.zeros((n + 1, m + 1), np.int8)
    for i in range(1, n + 1):
        S[i, 0] = -i; P[i, 0] = 1
    for j in range(1, m + 1):
        S[0, j] = -j * 2; P[0, j] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = S[i - 1, j - 1] + (3 if A[i - 1] == B[j - 1] else -6)
            if Bid[j - 1] >= 0:
                d += 25 if Bid[j - 1] == Aid[i - 1] else -4
            u = S[i - 1, j] - 1; l = S[i, j - 1] - 2
            if d >= u and d >= l:
                S[i, j] = d; P[i, j] = 0
            elif u >= l:
                S[i, j] = u; P[i, j] = 1
            else:
                S[i, j] = l; P[i, j] = 2
    i, j = n, m; res = np.full(m, -1, np.int64)
    while i > 0 and j > 0:
        p = P[i, j]
        if p == 0:
            res[j - 1] = i - 1; i -= 1; j -= 1
        elif p == 1:
            i -= 1
        else:
            j -= 1
    return res
def zone(n, live, holdout):
    d = open('../x/' + n, 'rb').read()
    c = struct.unpack_from('>I', d, 0)[0]; ID = np.array(struct.unpack_from('>%dI' % c, d, 4), dtype=np.int64) & 0x7fffffff
    o = 4 + 4 * c; k = struct.unpack_from('>I', d, o)[0]; o += 4 + 2 * k
    while o + 16 <= len(d):
        a, b, g, f = struct.unpack_from('>4I', d, o)
        if f > 64 or g >= len(T):
            break
        o += 16
    off = scan(np.frombuffer(d, 'u1'), o, H)
    recs = []
    for i in off:
        x, y, z = struct.unpack_from('>3f', d, i)
        if abs(x) > 26000 or abs(z) > 26000 or not -600 < y < 4500:
            continue
        recs.append((i, x, y, z))
    if not recs:
        return []
    ID = ID[ID < len(T)]; E = ID[(FL[ID] & 0x800) > 0]
    A = ISLOCAL[np.minimum(T[E], 69999)].astype(np.int64)
    B = np.array([0 if abs(r[1]) + abs(r[3]) < 1 else 1 for r in recs], dtype=np.int64)
    Bid = np.full(len(recs), -1, np.int64)
    if live:
        for j, r in enumerate(recs):
            v = live.get((round(r[1], 1), round(r[3], 1)))
            if v is not None and hash((r[0], n)) % 2 != holdout:
                Bid[j] = v
    res = align(A, B, E, Bid); out = []
    for j, r in enumerate(recs):
        if res[j] >= 0 and B[j] == 1:
            u = np.frombuffer(d, '>u2', 9, r[0] + 12); out.append((int(E[res[j]]), r[1], r[2], r[3], H[u].reshape(3, 3).round(4).tolist()))
    return out
if __name__ == '__main__':
    R = pickle.load(open('../memrec.pkl', 'rb')); L = pickle.load(open('../memobj_all.pkl', 'rb'))
    live = {(round(L[k][0], 1), round(L[k][2], 1)): r['id'] for k, r in R.items() if k in L}
    tlive = {(round(L[k][0], 1), round(L[k][2], 1)): k[0] for k in L}
    Z = [n for n, _ in json.load(open('../list.json')) if n.endswith('.pvsz') and not n.endswith('.lc')]
    t0 = time.time(); ALL = {}; good = bad = 0; base = lambda n: re.sub(r'_LOD.*$', '', n)
    for zi, n in enumerate(Z):
        for did, x, y, z, M in zone(n, live, 0):
            t = int(T[did]); key = (t, round(x, 1), round(z, 1))
            ALL.setdefault(key, (x, y, z, M, did))
            tv = tlive.get((round(x, 1), round(z, 1)))
            if tv is not None:
                if base(nm.get(tv, '')) == base(nm.get(t, '')):
                    good += 1
                else:
                    bad += 1
        if zi % 200 == 0:
            print(zi, len(ALL), f'{time.time() - t0:.0f}s', 'match', good, 'miss', bad, flush=True)
    pickle.dump(ALL, open('../zoneobj.pkl', 'wb'))
    print('objects', len(ALL), 'live agreement', good, bad)
    box = [(k, v) for k, v in ALL.items() if -8048 < v[0] < -3952 and 2202 < -v[2] < 6298]
    print('in Red Rock box', len(box)); print(collections.Counter(re.sub(r'\d+', '#', base(nm.get(k[0], '?')))[:30] for k, v in box).most_common(50))
