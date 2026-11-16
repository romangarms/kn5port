"""Fill a DLC track's gaps from base Colorado: python merge_base.py <Colorado work> <DLC work> [--off]

The Rally Expansion (ColoradoDirt) is a rebuild of Colorado that only has zones along its rally roads: 614 of Colorado's 1,434 hexes.
Carson's streets, the festival site and the roads between have no DLC zone. This fills every hex the DLC leaves empty with Colorado's
own zone for that hex, as if the game fell back to the base map cell by cell, and adds what those zones and the base map's placed
sets need to the DLC work folder, renumbered so nothing collides:

- meshes: a Colorado mesh the DLC also has (same geometry and box, or the same name re-cut within metres) is drawn as the DLC's copy;
  the rest are added as mesh BASE_MESH + its Colorado number.
- textures: identical files map to the DLC's texture id, the rest are added as TEX_BASE + their Colorado id. Colorado's texture-list
  keys become KEY_BASE + key. Shaders the DLC lacks are appended to its list.
- draws: Colorado's draw table is appended, so Colorado draw d is DLC draw draw_offset + d. LOD links are relative and survive.
- zones: each used Colorado zone is rewritten with the new draw and mesh numbers as zone 614 + i, and merged_00.hex (Colorado's
  91 x 60 grid) gives the DLC's hexes their own zones and the rest Colorado's. gamepath.HEX reads it when present.
- placed sets (.pgeo) whose box centre is in a Colorado hex, and that the DLC has no copy of, are added as __R00G<20000 + n>.
  build3 and grass.py shift their draw ids and texture keys by the offsets in merge.json.

The DLC's own tables are kept in <DLC work>/own/ on the first run and every run starts from them; --off restores them.
"""
import collections, hashlib, json, os, pickle, re, shutil, struct, subprocess, sys
import numpy as np

BASE_MESH = 10000; KEY_BASE = 20000; TEX_BASE = 0x200000; PGEO_BASE = 20000
TABLES = ('cat2.pkl', 'meshtab.pkl', 'texshader.pkl', 'mt.npy', 'zonerec.pkl', 'list.json')
GENERIC = re.compile(r'(Plane|Box|Line|Object|Mesh|Rectangle|cables_|Decals_)\d+', re.I)


def link(src, dst):
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def hexgrid(p):
    b = open(p, 'rb').read(); h = struct.unpack_from('>4sI3fIII', b, 0); W, H = h[6], h[7]
    return h, np.frombuffer(b, '>u4', W * H, 0x20).astype(np.int64).reshape(H, W)


def restore(D):
    own = D + '/own'
    if os.path.exists(D + '/merge.json'):
        for f in json.load(open(D + '/merge.json'))['files']:
            if os.path.exists(D + '/x/' + f):
                os.remove(D + '/x/' + f)
        os.remove(D + '/merge.json')
    if os.path.exists(D + '/merged_00.hex'):
        os.remove(D + '/merged_00.hex')
    if not os.path.exists(own):
        os.makedirs(own); [shutil.copyfile(f'{D}/{t}', f'{own}/{t}') for t in TABLES]
    for t in TABLES:
        shutil.copyfile(f'{own}/{t}', f'{D}/{t}')


def by_mesh(F):
    g = collections.defaultdict(list)
    for c in F:
        g[int(c['f'].split('.')[1])].append(c)
    return g


def main(B, D):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    track = json.load(open(D + '/track.json'))['track']; NAME = {'coloradodirt': 'ColoradoDirt'}[track]; PRE = track + 'out'
    restore(D); added = []; st = collections.Counter()
    F1 = pickle.load(open(B + '/cat2.pkl', 'rb')); F2 = pickle.load(open(D + '/cat2.pkl', 'rb'))
    M1 = pickle.load(open(B + '/meshtab.pkl', 'rb')); M2 = pickle.load(open(D + '/meshtab.pkl', 'rb'))
    I1, S1 = pickle.load(open(B + '/texshader.pkl', 'rb')); I2, S2 = pickle.load(open(D + '/texshader.pkl', 'rb'))
    D1 = np.load(B + '/mt.npy'); D2 = np.load(D + '/mt.npy'); DB = len(D2)
    L2 = json.load(open(D + '/list.json')); have2 = {n for n, _ in L2}
    assert len(M2) <= BASE_MESH and max(I2) < KEY_BASE and len(I1) <= 65535 - KEY_BASE

    # hexes: the DLC's zones where it has them, else Colorado's
    dump = open(B + '/dump_path.txt').read().strip() if os.path.exists(B + '/dump_path.txt') else None
    import gamepath
    h1, g1 = hexgrid(gamepath.MEDIA + 'tracks/colorado/Ribbon_00/Colorado_00.hex')
    h2, g2 = hexgrid(f'{D}/track/Ribbon_00/{NAME}_00.hex')
    assert h1[2:5] == h2[2:5], 'the two hex grids differ'
    n2 = h2[5]; G = np.full(g1.shape, 0xFFFFFFFF, np.int64); used = {}
    for r in range(g1.shape[0]):
        for c in range(g1.shape[1]):
            if r < g2.shape[0] and c < g2.shape[1] and g2[r, c] < n2:
                G[r, c] = g2[r, c]
            elif g1[r, c] < h1[5]:
                G[r, c] = used.setdefault(int(g1[r, c]), n2 + len(used))
    hdr = struct.pack('>4sI3fIII', h2[0], h2[1], *h2[2:5], n2 + len(used), g1.shape[1], g1.shape[0])
    open(D + '/merged_00.hex', 'wb').write(hdr + G.astype('>u4').tobytes())
    st['dlc hexes'] = int((G < n2).sum()); st['colorado hexes'] = int(((G >= n2) & (G < 0xFFFFFFFF)).sum())

    # meshes
    g_1 = by_mesh(F1); g_2 = by_mesh(F2)
    sig = lambda cs: tuple(sorted((c['nv'], c['nt'], tuple(np.round(c['mn'], 2)), tuple(np.round(c['mx'], 2))) for c in cs))
    bysig = collections.defaultdict(list); byname = collections.defaultdict(list)
    for no, cs in g_2.items():
        bysig[(cs[0]['name'], sig(cs))].append(no); byname[cs[0]['name']].append(no)
    obb = lambda M, no: np.array(M[no][2]) if no < len(M) else None
    MAP = {}
    for no, cs in g_1.items():
        o1 = obb(M1, no); name = cs[0]['name']
        cand = [d for d in bysig.get((name, sig(cs)), ()) if np.abs(obb(M2, d) - o1).max() < .01]
        if cand:
            MAP[no] = cand[0]; st['meshes identical'] += 1
            continue
        near = sorted((float(np.linalg.norm(obb(M2, d)[9:12] - o1[9:12])), d) for d in byname.get(name, ()))
        if near and near[0][0] < (5.0 if GENERIC.match(name) else 30.0):
            MAP[no] = near[0][1]; st['meshes re-cut by the DLC'] += 1
            continue
        st['meshes added'] += 1
    newno = lambda no: MAP.get(no, BASE_MESH + no)

    # shaders and textures
    S = list(S2) + [s for s in S1 if s not in S2]; SMAP = [S.index(s) for s in S1]
    for s in S1:
        if s not in S2:
            for f in os.listdir(B + '/x'):
                if f.lower() == f'shaders_track_{s}.fxobj'.lower() and f not in have2:
                    link(f'{B}/x/{f}', f'{D}/x/{f}'); added.append(f)

    def tfiles(w, tid):
        n = f'_0x{tid:08X}'
        return [f for f in (n + '.bix', n + '_B.bix', n + '.bin') if os.path.exists(f'{w}/x/{f}')]

    def thash(w, fs):
        h = hashlib.md5()
        for f in fs:
            h.update(f[-6:].encode()); h.update(open(f'{w}/x/{f}', 'rb').read())
        return h.hexdigest()
    size2 = collections.defaultdict(list)
    for tid, _ in set(I2.values()):
        fs = tfiles(D, tid)
        if fs:
            size2[tuple(os.path.getsize(f'{D}/x/{f}') for f in fs)].append((tid, fs))
    h2c = {}
    TMAP = {}
    for tid, _ in set(I1.values()):
        if tid >= 0xFFFF0000:
            TMAP[tid] = tid
            continue
        fs = tfiles(B, tid)
        if not fs:
            TMAP[tid] = TEX_BASE + tid
            continue
        key = tuple(os.path.getsize(f'{B}/x/{f}') for f in fs); h = None
        for t2, fs2 in size2.get(key, ()):
            h = h or thash(B, fs)
            if t2 not in h2c:
                h2c[t2] = thash(D, fs2)
            if h2c[t2] == h:
                TMAP[tid] = t2; break
        if tid in TMAP:
            st['textures shared'] += 1
            continue
        TMAP[tid] = TEX_BASE + tid; st['textures added'] += 1
        for f in fs:
            g = f.replace(f'_0x{tid:08X}', f'_0x{TEX_BASE + tid:08X}'); link(f'{B}/x/{f}', f'{D}/x/{g}'); added.append(g)
    I = dict(I2)
    for k, (tid, ty) in I1.items():
        I[KEY_BASE + k] = (TMAP[tid], ty)
    pickle.dump((I, S), open(D + '/texshader.pkl', 'wb'))

    # per-mesh table, catalogue, mesh files
    M = list(M2) + [((), (), (0.0,) * 15)] * (BASE_MESH - len(M2))
    for no, (keys, shs, ob) in enumerate(M1):
        M.append((tuple(KEY_BASE + k for k in keys), tuple(SMAP[s] if s < len(SMAP) else s for s in shs), ob))
    pickle.dump(M, open(D + '/meshtab.pkl', 'wb'))
    F = list(F2)
    for no, cs in g_1.items():
        if no in MAP:
            continue
        f = f'{PRE}.{BASE_MESH + no:05d}.rmb.bin'
        link(f'{B}/x/{cs[0]["f"]}', f'{D}/x/{f}'); added.append(f)
        F += [dict(c, f=f) for c in cs]
    pickle.dump(F, open(D + '/cat2.pkl', 'wb'))

    # draws
    A = D1.copy(); A['t'] = [newno(int(t)) for t in D1['t']]
    s1 = A['s'][:, 1].astype(np.int64); A['s'][:, 1] = np.where(s1 > 0, s1 + KEY_BASE, 0)
    np.save(D + '/mt.npy', np.concatenate([D2, A]))

    # zones
    for z, nz in used.items():
        b = bytearray(open(f'{B}/x/__R00Z{z:05d}.pvsz', 'rb').read())
        n = struct.unpack_from('>I', b, 0)[0]
        for i in range(n):
            v = struct.unpack_from('>I', b, 4 + 4 * i)[0]; struct.pack_into('>I', b, 4 + 4 * i, (v & 0x80000000) | ((v & 0x7fffffff) + DB))
        o = 4 + 4 * n; k = struct.unpack_from('>I', b, o)[0]
        for i in range(k):
            struct.pack_into('>H', b, o + 4 + 2 * i, newno(struct.unpack_from('>H', b, o + 4 + 2 * i)[0]))
        f = f'__R00Z{nz:05d}.pvsz'; open(f'{D}/x/{f}', 'wb').write(b); added.append(f)

    # placed sets in Colorado's hexes that the DLC has no copy of
    R = h1[2]; X0, Z0 = h1[3], h1[4]
    from scipy.spatial import cKDTree
    rc = [(r, c) for r in range(G.shape[0]) for c in range(G.shape[1]) if G[r, c] < 0xFFFFFFFF]
    tree = cKDTree([(X0 + (c + .5) * 1.5 * R, Z0 + (r + .5 + .5 * (c % 2)) * np.sqrt(3) * R) for r, c in rc])
    def head(w, n):
        b = open(f'{w}/x/{n}', 'rb').read(0x100)
        """(the header's strings, such as the set name and layer tags, which sit at no fixed offset; box) of a set"""
        return (b'|'.join(re.findall(rb'[A-Za-z0-9_\[\]]{4,}', b[0x50:0x100])), struct.unpack('>8f', b[0x10:0x30])) if b[:4] == b'OEGP' else None
    core = lambda h: max(h[0].split(b'|'), key=len)      # the set's name: the longest string; tags and leftovers follow it
    dl = [(core(h), np.array(h[1])) for h in (head(D, n) for n in have2 if n.endswith('.pgeo')) if h]
    DB_ = np.array([b_ for _, b_ in dl])

    def has(h):
        """the DLC has this set: a set within a metre of its box, or within 20 m and with its name"""
        d_ = np.abs(DB_ - np.array(h[1])).max(1)
        return d_.min() < 1 or any(dl[i][0] == core(h) for i in np.where(d_ < 20)[0])
    PG = {}
    def model(h):
        m_ = re.search(rb'Anim_(\w+?)_?\d+(\||$)', h[0]) if h else None
        return m_.group(1) if m_ else None
    anims = {model(head(D, n)) for n in have2 if n.endswith('.pgeo')} - {None}
    for n, _ in json.load(open(B + '/list.json')):
        if not n.endswith('.pgeo'):
            continue
        h = head(B, n)
        if h is None:
            continue
        if model(h):            # the track's copies of animated models (animcopy.py): add the ones the DLC has none of, such as the festival rides
            if model(h) not in anims:
                anims.add(model(h)); g = f'__R00G{PGEO_BASE + int(n[6:11]):05d}.pgeo'; link(f'{B}/x/{n}', f'{D}/x/{g}'); added.append(g); PG[g] = n
                st['animated models added'] += 1
            continue
        bb = h[1]; _, i = tree.query(((bb[0] + bb[4]) / 2, (bb[2] + bb[6]) / 2)); r, c = rc[i]
        if G[r, c] < n2:
            continue
        if has(h):
            st['sets the DLC has'] += 1
            continue
        g = f'__R00G{PGEO_BASE + int(n[6:11]):05d}.pgeo'; link(f'{B}/x/{n}', f'{D}/x/{g}'); added.append(g); PG[g] = n; st['sets added'] += 1

    L = L2 + [(f, os.path.getsize(f'{D}/x/{f}')) for f in added]
    json.dump(L, open(D + '/list.json', 'w'))
    json.dump(dict(base_mesh=BASE_MESH, draw_offset=DB, key_offset=KEY_BASE, base_zones=n2, pgeo=PG, files=added), open(D + '/merge.json', 'w'))

    # placements: zones.py over the merged zones, then drop Colorado records of a DLC mesh the DLC already places there
    snap = D + '/prep_snapshot'
    subprocess.check_call([sys.executable, 'zones.py'], cwd=snap)
    Z = pickle.load(open(D + '/zonerec.pkl', 'rb')); T = np.concatenate([D2['t'], A['t']]).astype(np.int64)
    mine = {(int(T[d]), p) for d, p in Z if d < DB}; out = {}
    for (d, p), r in Z.items():
        if d >= DB and (int(T[d]), p) in mine:
            st['placements the DLC has'] += 1
            continue
        out[(d, p)] = r
    pickle.dump(out, open(D + '/zonerec.pkl', 'wb'))
    st['colorado zones'] = len(used); st['files added'] = len(added)
    print(dict(st))


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    if len(a) != 2:
        sys.exit(__doc__)
    B, D = (os.path.abspath(x).replace('\\', '/') for x in a)
    if '--off' in sys.argv:
        restore(D); print('restored the DLC tables')
    else:
        main(B, D)
