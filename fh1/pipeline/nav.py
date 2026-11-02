"""colorado.nav: FH1's road network. Layout is in docs/formats.md."""
import collections, struct
import numpy as np
import gamezip


def read():
    """Returns node positions as (x, north, height) and {road id: {'nodes': ordered node ids, 'tags': {key: value}}}."""
    b = open(gamezip.MEDIA + 'tracks/colorado/colorado.nav', 'rb').read()
    n, roads_, tags_, slots, _, nkeys, nvals = struct.unpack('>7I', b[4:32])
    r = np.frombuffer(b, '>u4', count=n * 8, offset=44).reshape(n, 8).astype('i8')
    p = np.frombuffer(b, '>f4', count=n * 8, offset=44).reshape(n, 8)[:, :3].astype('f8')
    o = 44 + n * 32; table = np.frombuffer(b, '>u4', count=roads_ * 4 - 1, offset=o).astype('i8')    # the last record is a word short
    o += (roads_ * 4 - 1) * 4 + slots * 4
    pairs = np.frombuffer(b, '>u4', count=slots * 2, offset=o).reshape(slots, 2).astype('i8'); o += slots * 8
    tags = np.frombuffer(b, '>u4', count=tags_ * 2, offset=o).reshape(tags_, 2).astype('i8'); o += tags_ * 8
    offs = np.frombuffer(b, '>u4', count=nkeys + nvals, offset=o).astype('i8'); o += (nkeys + nvals) * 4
    def text(base, i):
        return b[base + i:b.index(b'\0', base + i)].decode('latin1')
    keys = [text(o, i) for i in offs[:nkeys]]
    base = b.index(b'\0', o + offs[nkeys - 1]) + 1; vals = [text(base, i) for i in offs[nkeys:]]
    owner = np.full(slots, -1)
    for i in range(n):
        owner[r[i, 4]:r[i, 4] + r[i, 3]] = i
    out = collections.defaultdict(lambda: {'nodes': [], 'tags': {}})
    for s in np.lexsort((pairs[:, 1], pairs[:, 0])):
        out[int(pairs[s, 0])]['nodes'].append(int(owner[s]))
    for i in out:
        rec = table[i * 4:i * 4 + 4]
        if len(rec) > 2:
            out[i]['tags'] = {keys[k]: vals[v] for k, v in tags[rec[2]:rec[2] + rec[1]].tolist()}
    return np.stack([p[:, 0], p[:, 2], p[:, 1]], 1), dict(out)


def is_road(road):
    """Map labels and region outlines share the table with real roads."""
    return not {'text', 'area_type'} & set(road['tags'])
