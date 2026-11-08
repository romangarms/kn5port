"""Exact reader for .pvsz zone files. Run from fh1/pipeline: python zones.py

A zone is: u32 n; n u32 ids (bit 31 load/unload, low bits draw id); u32 k; k u16 mesh numbers; a 16-byte typed chain; gameplay nodes;
then one placement record for every id after the first, in the same order, up to the end of the file:
  3 half floats  LOD distances
  3 floats       position (game space, z flipped against meshes)
  9 half floats  rotation, may carry scale
  float          scale (0 means 1)
  12 bytes       zero
  u8             1 if an owner block follows, 0 ends the record (53 bytes)
  u32            reference (ffffffff when none)
  u8 n, n bytes  list
  32 bytes       owner name (gameplay id such as FR22_03)
"""
import struct, collections, json, re, pickle, sys
import numpy as np
mt = np.load('../mt.npy'); T = mt['t'].astype(np.int64); FL = mt['f'].astype(np.int64)
H = np.array([0. if ((u >> 10) & 31) == 0 else (-1. if u & 0x8000 else 1.) * (1 + (u & 1023) / 1024) * 2. ** (((u >> 10) & 31) - 15) for u in range(65536)])


def walk(d, s, want):
    """exactly `want` records from offset s, or None"""
    out = []; o = s
    for _ in range(want):
        if o + 53 > len(d) or any(d[o + 40:o + 52]) or d[o + 52] > 1:
            return None
        size = 53; ref = None; lst = b''; name = ''
        if d[o + 52]:
            if o + 90 > len(d):
                return None
            n = d[o + 57]; size = 90 + n
            if o + size > len(d):
                return None
            raw = d[o + 58 + n:o + 90 + n].split(b'\0')[0]
            if not all(32 <= c < 127 for c in raw):
                return None
            ref = struct.unpack_from('>I', d, o + 53)[0]; lst = bytes(d[o + 58:o + 58 + n]); name = raw.decode()
        out.append((o, ref, lst, name)); o += size
    return out if o == len(d) else None


def zone(n):
    """-> list of records: id, pos, rotation (3x3), scale, lod distances, ref, list, owner name"""
    d = open('../x/' + n, 'rb').read()
    c = struct.unpack_from('>I', d, 0)[0]; V = struct.unpack_from('>%dI' % c, d, 4); a = np.frombuffer(d, 'u1')
    z = np.where(np.convolve((a != 0).astype('i4'), np.ones(12, 'i4'), 'valid') == 0)[0]
    z = z[(z >= 4 + 4 * c + 40) & (z + 12 < len(d))]; z = z[a[z + 12] <= 1]
    for s in z.tolist():
        r = walk(d, s - 40, c - 1)
        if r is None:
            continue
        out = []
        for i, (o, ref, lst, name) in enumerate(r):
            out.append(dict(id=V[i + 1] & 0x7fffffff, load=V[i + 1] >> 31, lod=[float(H[v]) for v in struct.unpack_from('>3H', d, o)], pos=struct.unpack_from('>3f', d, o + 6),
                            rot=H[np.frombuffer(d, '>u2', 9, o + 18)].reshape(3, 3), scale=struct.unpack_from('>f', d, o + 36)[0], ref=ref, lst=lst, name=name))
        return out
    return None


def all_zones():
    return [n for n, _ in json.load(open('../list.json')) if n.endswith('.pvsz') and not n.endswith('.lc')]


if __name__ == '__main__':
    F = pickle.load(open('../cat2.pkl', 'rb')); nm = {}
    for c in F:
        nm.setdefault(int(c['f'].split('.')[1]), c['name'])
    base = lambda n: re.sub(r'\d+$', '', re.sub(r'_LOD.*$', '', n)).rstrip('_')
    R = pickle.load(open('../memrec.pkl', 'rb')); L = pickle.load(open('../memobj_all.pkl', 'rb'))
    live = collections.defaultdict(set); liveid = collections.defaultdict(set)
    for k, v in L.items():
        p = (round(v[0], 1), round(v[2], 1)); live[p].add(base(nm.get(k[0], '')))
        if k in R:
            liveid[p].add(R[k]['id'])
    st = collections.Counter(); ALL = {}; seen = set(); wrong = []
    for n in all_zones():
        r = zone(n)
        st['zones'] += 1
        if r is None:
            st['failed'] += 1
            continue
        st['records'] += len(r)
        for x in r:
            if x['id'] >= len(T):
                st['id_over'] += 1
                continue
            p = (round(x['pos'][0], 1), round(x['pos'][2], 1)); t = int(T[x['id']])
            ALL.setdefault((x['id'], p), x)
            if p in live and (x['id'], p) not in seen:
                seen.add((x['id'], p))
                ok = base(nm.get(t, '')) in live[p]; st['live_ok' if ok else 'live_bad'] += 1
                st['id_exact'] += x['id'] in liveid[p]
                if not ok and len(wrong) < 8:
                    wrong.append((p, live[p], nm.get(t, '')))
    print(st); print(wrong)
    pickle.dump(ALL, open('../zonerec.pkl', 'wb'))
    print('unique placements', len(ALL), 'positions', len({k[1] for k in ALL}))
    names = collections.Counter(re.sub(r'\d+', '#', x['name']) for x in ALL.values()); print(names.most_common(30))
