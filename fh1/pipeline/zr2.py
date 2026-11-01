"""Zone files: first list = draw ids, in the same order as the 90-ish byte placement records in the tail."""
import struct, pickle, json, collections, re, sys
import numpy as np
mt = np.load('../mt.npy'); T = mt['t'].astype(int)
H = np.array([0. if ((u >> 10) & 31) == 0 else (-1. if u & 0x8000 else 1.) * (1 + (u & 1023) / 1024) * 2. ** (((u >> 10) & 31) - 15) for u in range(65536)])
def records(d, start):
    """every (offset, pos, rot) in the tail, including world-space meshes at the origin"""
    a = np.frombuffer(d, 'u1'); out = []; i = start; n = len(d)
    while i + 30 <= n:
        x, y, z = struct.unpack_from('>3f', d, i)
        if (abs(x) < 26000 and abs(z) < 26000 and -600 < y < 4500) and (d[i + 12:i + 30] != b'\0' * 18):
            u = np.frombuffer(d, '>u2', 9, i + 12); M = H[u].reshape(3, 3); nr = np.sqrt((M * M).sum(1))
            if nr.min() > .9 and nr.max() < 1.1 and abs(np.sqrt((M * M).sum(0)) - 1).max() < .15:
                out.append((i, x, y, z, M)); i += 72
                continue
        i += 1
    return out
def zone(n):
    d = open('../x/' + n, 'rb').read()
    c = struct.unpack_from('>I', d, 0)[0]; V = np.array(struct.unpack_from('>%dI' % c, d, 4), dtype=np.int64)
    o = 4 + 4 * c; k = struct.unpack_from('>I', d, o)[0]; o += 4 + 2 * k; ch = 0
    while o + 16 <= len(d):
        a, b, g, f = struct.unpack_from('>4I', d, o)
        if f > 64 or g >= len(T):
            break
        o += 16; ch += 1
    return d, V & 0x7fffffff, V >> 31, ch, records(d, o)
if __name__ == '__main__':
    R = pickle.load(open('../memrec.pkl', 'rb')); L = pickle.load(open('../memobj_all.pkl', 'rb'))
    live = {(round(L[k][0], 1), round(L[k][2], 1)): r['id'] for k, r in R.items() if k in L}
    Z = [n for n, _ in json.load(open('../list.json')) if n.endswith('.pvsz') and not n.endswith('.lc')]
    st = collections.Counter(); ex = []
    for n in Z[:60]:
        d, ID, HI, ch, recs = zone(n)
        st['zones'] += 1; st['ids'] += len(ID); st['recs'] += len(recs)
        ex.append((n[-10:-5], len(ID), len(recs), ch, sum(1 for r in recs if abs(r[1]) + abs(r[3]) < 1)))
        best = collections.Counter()
        for i, r in enumerate(recs):
            e = live.get((round(r[1], 1), round(r[3], 1)))
            if e is None:
                continue
            for s in range(-3, 4):
                if 0 <= i + s < len(ID) and ID[i + s] == e:
                    best[s] += 1
            best['n'] += 1
        for k, v in best.items():
            st[('shift', k)] += v
    print(st); print(ex[:16])
