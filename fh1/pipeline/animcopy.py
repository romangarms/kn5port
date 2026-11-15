"""The track's own copies of ANIM models (__R00G000xx.pgeo, named Anim_<model><n> at 0x60): their meshes, the textures each draw uses, the bone each
vertex follows, and those bones' animation, from the Granny file inside (granny2.py). Used for the festival rides, balloons and inflatables, which exist
only here. Layout is in docs/formats.md (track copies)."""
import functools, glob, itertools, os, re, struct
import numpy as np
from scipy.interpolate import BSpline
import gamepath, granny2

COPIES = ('../x/__R00G000[0-9][0-9].pgeo', '../x/__R00G200[0-9][0-9].pgeo')       # the second: base Colorado's, added by merge_base.py
# a draw node ends in two floats, a switch distance and a fraction, such as 10.0 and 0.1
NODE_FAR = (1, 1000); NODE_FRACTION = (.001, 1)
# V from the s16 at its offset, as granny.uvs
V0, VSTEP = .4977, 1.5395e-5


KEYS = {}       # model -> what to add to its texture-list indices: in a merged DLC track, base Colorado's keys are offset (merge.json)


@functools.cache
def find(model):
    """the bytes of the track's copy of one model, e.g. ANIM_FRGND_Ferris. Copies are named Anim_<model><index>"""
    for f in sorted(f for p in COPIES for f in glob.glob(p)):
        b = open(f, 'rb').read()
        if b[:4] == b'OEGP' and re.fullmatch(rb'Anim_' + re.escape(model.encode()) + rb'_?\d+', b[0x60:0xa0].split(b'\0')[0]):
            KEYS[model] = gamepath.MERGE.get('key_offset', 0) if os.path.basename(f) in gamepath.MERGE.get('pgeo', ()) else 0
            return b
    raise FileNotFoundError(model)


def standalone(model):
    """the bytes of an ANIM model that the track has no copy of, from media/animatedobjects.zip, e.g. ANIM_PlaneBanner_002"""
    import gamezip
    return gamezip.read('animatedobjects.zip', model + '.pgeo')


def texture_names(b):
    """a standalone model's textures, in the order draw nodes number them. Its header has no texture-list indices (the word at 0x40 is 0) but
    a run of u32 name lengths, each counting the name's null, then the names back to back. The bytes before may hold a leftover name tail
    ('ZN_Diff.' in ANIM_PlaneBanner_002), so a run is taken only where every length matches its name"""
    for s in range(0xa0, 0x400, 4):
        names = []; o = s
        while True:
            m = re.match(rb'[\x20-\x7e]{4,}\0', b[o:o + 128])
            if not m:
                break
            names.append(m.group()[:-1].decode()); o += len(m.group())
        if names and all(struct.unpack_from('>I', b, s - 4 * (len(names) - i))[0] == len(n) + 1 for i, n in enumerate(names)):
            return names
    raise ValueError('no texture names')


def xds(name):
    """a texture from animatedobjects.zip/Textures, as RGBA. These hold their top mip linear, not tiled as effects.zip's are: format byte 0x73
    is DXT1 and 0x75 DXT5 (size in the fetch constant at 0x24, data from 52)"""
    import gamezip, tex
    b = gamezip.read('animatedobjects.zip', f'Textures/{name}.xds'); size = struct.unpack_from('>I', b, 0x24)[0]
    w = (size & 0x1fff) + 1; h = ((size >> 13) & 0x1fff) + 1; f = {0x73: (0x52, 8), 0x75: (0x54, 16)}[b[0x23]]
    return tex.decode(b[52:52 + ((w + 3) // 4) * ((h + 3) // 4) * f[1]], w, h, f[0])


def signed(w):
    return w - (1 << 32) if w >= 1 << 31 else w


def as_float(w):
    return struct.unpack('>f', struct.pack('>I', w))[0]


def textures(b):
    """texture-list indices this copy uses, in the order draw nodes number them: after the name, a u16 at the end of each 8-byte entry.
    The count is the word at 0x40"""
    n = struct.unpack_from('>I', b, 0x40)[0]; end = b.index(b'\0', 0x60)
    first = (end + 1 + 3) // 4 * 4 + 2
    return [struct.unpack_from('>H', b, first + 8 * j)[0] for j in range(n)]


def draws(W, flags=False):
    """Draw nodes: (texture slots, first triangle, triangle count) of 11 words each, the slots numbering the texture list (-1 for none): colour, unused,
    glow, specular. Consecutive nodes form one mesh's tree. A tree restarts at triangle 0. The three words before the switch distance are 0, except in the
    people models (Fest_*, Street_*), where they hold small flags such as (0, 0, 2) and (1, 0, 0), and a fraction may be 0: flags=True allows those. It's off for the rides, as
    on some stage and light copies it finds extra nodes"""
    k_ = [k for k in range(9, len(W) - 1) if (max(W[k - 3:k]) < 16 if flags else W[k - 3:k] == (0, 0, 0)) and NODE_FAR[0] <= as_float(W[k]) <= NODE_FAR[1] and
          (0 if flags else NODE_FRACTION[0]) <= as_float(W[k + 1]) <= NODE_FRACTION[1] and 0 < W[k - 4] < 1 << 20]
    trees = []
    for k in k_:
        n = [signed(x) for x in W[k - 9:k - 3]]
        if as_float(W[k + 1]) < NODE_FRACTION[0] and not (trees and k - trees[-1][-1][0] == 11 and n[4] == sum(trees[-1][-1][1][1:]) and n[4]):
            continue                # a fraction of 0 only on a node that carries on the one before it
        if not trees or n[4] == 0 or k - trees[-1][-1][0] != 11:
            trees.append([])
        trees[-1].append((k, (n[:4], n[4], n[5])))
    return trees


def vertex_sizes(b, ms, o, rest):
    """each mesh's vertex size where a model mixes them, as the people's do (a 24-byte body, a 20-byte hair mesh, 16-byte rigid props): the sizes
    that fill rest bytes, skinned 16, 20 or 24 and rigid 12 or 16, and of those the ones whose skinned vertices read as bones or weights summing to 255"""
    fits = []
    for sizes in itertools.product(*[(16, 20, 24) if m['skin'] else (12, 16) for m in ms]):
        if sum(m['V'] * s for m, s in zip(ms, sizes)) != rest:
            continue
        at = o; good = True
        for m, s in zip(ms, sizes):
            raw = np.frombuffer(b, 'u1', m['V'] * s, at).reshape(-1, s); at += m['V'] * s
            good = good and (not m['skin'] or s == 16 and (raw[:, 8:12] == raw[:, 8:9]).all() or s > 16 and (raw[:, 8:12].astype(int).sum(1) == 255).all())
        if good:
            fits.append(sizes)
    if len(fits) != 1:
        raise ValueError(f'{len(fits)} ways to size the skinned vertices in {rest} bytes')
    return fits[0]


def rigid_sizes(b, ms, o, rest):
    """rigid vertex sizes where 12 bytes don't fill the tail, as in the main stage's light rig: its lamps' 16-byte vertices are the 12-byte layout and 4
    zero bytes, its glow planes 12. Meshes of the same vertex and triangle counts share a size; the sizes that fill rest bytes and leave every 16-byte
    vertex's last 4 bytes zero"""
    kinds = sorted({(m['V'], m['T']) for m in ms}); fits = []
    for pick in itertools.product((12, 16), repeat=len(kinds)):
        size = dict(zip(kinds, pick)); sizes = [size[(m['V'], m['T'])] for m in ms]
        if sum(m['V'] * z for m, z in zip(ms, sizes)) != rest:
            continue
        at = o; good = True
        for m, z in zip(ms, sizes):
            raw = np.frombuffer(b, 'u1', m['V'] * z, at).reshape(-1, z); at += m['V'] * z
            good = good and (z == 12 or not raw[:, 12:].any())
        if good:
            fits.append(sizes)
    if len(fits) != 1:
        raise ValueError(f'{len(fits)} ways to size the rigid vertices in {rest} bytes')
    return fits[0]


def meshes(b, flags=False):
    """Each mesh: vertex count, triangles, the bone it follows in its model's skeleton (rigid) or None (skinned), its draws, and its vertices:
    positions in Granny axes, UVs, and for skinned meshes each vertex's bone. Header records of 17 words hold, at the vertex count, the bone (-1 when
    skinned) before it and the index count and draw count after. The geometry after the Granny file is every mesh's u16 indices, each padded to 4
    bytes, then every mesh's vertices: 12 bytes when rigid (3 half-floats, U half, V s16, normal) and 16 or 20 when skinned, with V 4 bytes from
    the end. 16-byte vertices have bone numbers at 8-11, all four equal, counting in the model's own skeleton; 20-byte ones weights and then bones.
    24-byte ones (the people's high-detail models) are 20-byte ones and 4 zero bytes, V at 16"""
    g = granny2.File(b); W = struct.unpack(f'>{g.at // 4}I', b[:g.at // 4 * 4]); tail = struct.unpack_from('>I', b, 0x50)[0]
    out = []; at = 1
    for tree in draws(W, flags):
        segs = [n for _, n in tree]; T = max(f + c for _, f, c in segs); first_node = tree[0][0] - 9
        # the draw count follows the index count, or in the stage and autoshow light rigs (flags) may come one word later, after a 0
        n_ = (len(segs), len(segs) - 1)
        k = next((k for k in range(at, first_node) if W[k] == 3 * T and (W[k + 1] in n_ or flags and W[k + 1] == 0 and W[k + 2] in n_) and 0 < W[k - 1] < 1 << 16), None)
        if k is None:
            raise ValueError(f'no mesh record for a tree of {T} triangles')
        out.append(dict(V=W[k - 1], T=T, bone=signed(W[k - 2]), segs=segs)); at = k + 2
    for m in out:
        m['skin'] = m['bone'] == -1
    idx_bytes = sum((6 * m['T'] + 3) // 4 * 4 for m in out); rigid = sum(m['V'] for m in out if not m['skin']); skinned = sum(m['V'] for m in out if m['skin'])
    rest = tail - idx_bytes - 12 * rigid
    size = rest // skinned if skinned else 0
    sizes = [size if m['skin'] else 12 for m in out]
    if flags and skinned and (rest != size * skinned or size not in (16, 20, 24)):
        sizes = vertex_sizes(b, out, len(b) - tail + idx_bytes, tail - idx_bytes)
    elif flags and not skinned and rest:
        sizes = rigid_sizes(b, out, len(b) - tail + idx_bytes, tail - idx_bytes)
    elif (rest != size * skinned) or (skinned and size not in (16, 20, 24)):
        raise ValueError(f'meshes fill {idx_bytes + 12 * rigid} of {tail} bytes, {skinned} skinned vertices')
    o = len(b) - tail
    for m in out:
        m['idx'] = np.frombuffer(b, '>u2', 3 * m['T'], o).astype('i8').reshape(-1, 3); o += (6 * m['T'] + 3) // 4 * 4
    for m, sz in zip(out, sizes):
        raw = np.frombuffer(b, 'u1', m['V'] * sz, o).reshape(-1, sz); o += m['V'] * sz
        m['pos'] = raw[:, :6].copy().view('>f2').astype('f8')
        v_at = (16 if sz == 24 else sz - 4) if m['skin'] else 8
        m['uv'] = np.c_[raw[:, 6:8].copy().view('>f2')[:, 0].astype('f8'), V0 + raw[:, v_at:v_at + 2].copy().view('>i2')[:, 0].astype('f8') * VSTEP]
        if m['skin'] and sz == 16:
            m['bones'] = raw[:, 8].astype('i8'); m['b4'] = raw[:, 8:12].astype('i8'); m['w4'] = np.tile([1., 0, 0, 0], (m['V'], 1))
        elif m['skin']:             # weights at 8-11, bones at 12-15: the inflatables blend, and each vertex follows its heaviest bone
            m['bones'] = raw[:, 12:16][np.arange(m['V']), raw[:, 8:12].argmax(1)].astype('i8')
            m['b4'] = raw[:, 12:16].astype('i8'); m['w4'] = raw[:, 8:12] / 255.     # all four, for posing a frame (people.py)
        if m['idx'].max() >= m['V']:
            raise ValueError('indices run past their vertices')
    return out


def bind(b, ms):
    """Which model each mesh belongs to: meshes are stored model by model, in the order of the Granny file's skeletons, and a rigid mesh's bone counts
    within its model's skeleton. The split that puts the most meshes on bones named for a LOD is kept. Adds model, and for rigid meshes the bone's name"""
    sk = [[x['Name'] for x in s['Bones']] for s in granny2.File(b).tree()['Skeletons']]
    need = [int(m['bones'].max()) if m['skin'] else m['bone'] for m in ms]

    @functools.lru_cache(None)
    def best(j, s, used):
        if j == len(ms):
            return 0, ()
        if s >= len(sk):
            return -1e9, ()
        out = best(j, s + 1, frozenset())
        if need[j] < len(sk[s]) and (ms[j]['skin'] or need[j] not in used):
            name = None if ms[j]['skin'] else sk[s][need[j]]
            r = best(j + 1, s, used if ms[j]['skin'] else used | {need[j]})
            out = max(out, (r[0] + (ms[j]['skin'] or 'LOD' in name), ((s, name),) + r[1]))
        return out
    score, a = best(0, 0, frozenset())
    if score < 0:
        raise ValueError('meshes fit no split into the skeletons')
    for m, (s, name) in zip(ms, a):
        m['model'] = s; m['name'] = name
    return sk


def lod(m, ms):
    """detail level of a mesh: from its bone's name (LOD00, LOD01_...), or for skinned meshes its rank by size among its model's skinned meshes"""
    if not m['skin']:
        r = re.search(r'LOD0*(\d+)_?$', m['name'])
        return int(r.group(1)) if r else 0
    return sorted((x['T'] for x in ms if x['skin'] and x['model'] == m['model']), reverse=True).index(m['T'])


# Granny's quantised quaternion tables: each nibble of ScaleOffsetTableEntries picks a component's scale and offset
QSCALE = np.array([1.4142135, .70710677, .35355338, .35355338, .35355338, .17677669, .17677669, .17677669,
                   -1.4142135, -.70710677, -.35355338, -.35355338, -.35355338, -.17677669, -.17677669, -.17677669])
QOFFSET = np.array([-.70710677, -.35355338, -.53033006, -.17677669, .17677669, -.17677669, -.088388346, 0,
                    .70710677, .35355338, .53033006, .17677669, -.17677669, .17677669, .088388346, 0])


def values(a):
    """a Granny array read by granny2 as a numpy record array of one member, as plain floats"""
    return np.asarray(a[a.dtype.names[0]] if a.dtype.names else a, 'f8')


def curve(g, cd, dim):
    """(knots, controls, degree) of a curve: controls n x dim, knots None for a constant. Quaternions come back as x, y, z, w; scale-shear as 9 values"""
    d = g.read(*cd); head = next(k for k in d if k.startswith('CurveDataHeader_')); degree = int(d[head]['Degree'])
    return knots_controls(d, head[16:], dim) + (degree,)


def knots_controls(d, kind, dim):
    """(knots, controls) of a curve read by granny2, by its kind, such as D4nK16uC15u"""
    if kind == 'DaIdentity':
        return None, (np.eye(3).ravel() if dim == 9 else np.r_[0, 0, 0, 1.] if dim == 4 else np.zeros(3))[None]
    if kind.endswith('Constant32f'):
        return None, values(d['Controls'])[None, :dim]
    if kind == 'DaK32fC32f':
        k = values(d['Knots']); c = values(d['Controls']).reshape(len(k), -1)
        return k, c
    kc = values(d['KnotsControls'])
    if kind.startswith('D4n'):
        bits = 15 if 'C15u' in kind else 7; n = len(kc) // 4; sel = int(d['ScaleOffsetTableEntries'])
        scale = QSCALE[[(sel >> s) & 15 for s in (0, 4, 8, 12)]] / (2 ** bits - 1); offset = QOFFSET[[(sel >> s) & 15 for s in (0, 4, 8, 12)]]
        c = kc[n:].astype('i8').reshape(-1, 3); top = 1 << bits; mask = top - 1
        s1 = ((c[:, 1] & top) >> (bits - 1)) | ((c[:, 2] & top) >> bits)
        q = np.zeros((n, 4))
        rows = np.arange(n); sq = np.zeros(n)
        for j in range(3):
            s = (s1 + 1 + j) & 3; v = (c[:, j] & mask) * scale[s] + offset[s]; q[rows, s] = v; sq += v * v
        w = np.sqrt(np.maximum(1 - sq, 0)); q[rows, s1] = np.where(c[:, 0] & top, -w, w)
        return kc[:n] / float(d['OneOverKnotScale']), q
    one = as_float(int(d['OneOverKnotScaleTrunc']) << 16)
    if kind.startswith('D3') and 'I1' not in kind:
        n = len(kc) // 4; c = kc[n:].reshape(-1, 3) * values(d['ControlScales']) + values(d['ControlOffsets'])
        return kc[:n] / one, c
    if kind.startswith('D3I1'):
        n = len(kc) // 2; c = kc[n:, None] * values(d['ControlScales']) + values(d['ControlOffsets'])
        return kc[:n] / one, c
    if kind.startswith('D9I1'):
        n = len(kc) // 2; c = kc[n:] * float(d['ControlScale']) + float(d['ControlOffset'])
        return kc[:n] / one, (np.eye(3).ravel() * c[:, None])
    if kind.startswith('D9I3'):
        n = len(kc) // 4; c = kc[n:].reshape(-1, 3) * values(d['ControlScales']) + values(d['ControlOffsets'])
        return kc[:n] / one, np.array([np.diag(x).ravel() for x in c])
    if kind in ('DaK16uC16u', 'DaK8uC8u'):
        # any dimension: the scales, then the offsets, one per component
        so = values(d['ControlScaleOffsets']); n_ = len(so) // 2; n = len(kc) // (n_ + 1)
        return kc[:n] / one, kc[n:].reshape(n, n_) * so[:n_] + so[n_:]
    raise ValueError(f'curve {kind} not read')


def sample(k, v, degree, times):
    """a curve at each time, held past its ends. Granny's curves are clamped B-splines: each control has a knot, and the first and last knots repeat
    to the curve's degree, so it starts and ends on its end controls"""
    if k is None or len(k) == 1 or k[-1] <= k[0]:
        return np.repeat(v[:1], len(times), 0)
    t = np.clip(times, k[0], k[-1])
    if degree < 1:
        return v[np.searchsorted(k, t, 'right') - 1]
    if len(k) <= degree + 1:
        return np.c_[[np.interp(t, k, v[:, j]) for j in range(v.shape[1])]].T
    return BSpline(np.r_[[k[0]] * degree, k, [k[-1]]], v, degree)(t)


def quat_matrix(q):
    """rotation matrices of x, y, z, w quaternions, for column vectors"""
    q = q / np.linalg.norm(q, axis=-1, keepdims=True); x, y, z, w = q.T
    return np.stack([np.c_[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     np.c_[2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     np.c_[2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], 1)


def local(p, q, ss):
    """4x4 bone transforms for column vectors: position, then orientation, then scale-shear"""
    M = np.zeros((len(p), 4, 4)); M[:, :3, :3] = quat_matrix(q) @ ss.reshape(-1, 3, 3); M[:, :3, 3] = p; M[:, 3, 3] = 1
    return M


def duration(b):
    """seconds of the copy's animation, which loops"""
    return float(granny2.File(b).tree()['Animations'][0]['Duration'])


def poses(b, times):
    """World transforms of every bone at each time, [skeleton][bone] -> (n, 4, 4), for column vectors in Granny axes, and the bind pose of each, from
    InverseWorldTransform. Bones without a track keep their rest transform. A track group's InitialPlacement places its skeleton's root: NoFear's arm
    pivots 16 m up and turned, and the Zipper's cars hang from the boom, only through it"""
    g = granny2.File(b); t = g.tree(); groups = {tg['Name']: tg for a in t['Animations'][:1] for tg in a['TrackGroups']}
    out = []; rest = []
    for s in t['Skeletons']:
        tg = groups.get(s['Name'], {}); tracks = {tr['Name']: tr for tr in tg.get('TransformTracks', [])}
        x = tg.get('InitialPlacement'); P = np.eye(4) if x is None else local(x['position'][None].astype('f8'), x['orientation'][None].astype('f8'), x['scale_shear'][None].astype('f8'))[0]
        W = []; B = []
        for bone in s['Bones']:
            tr = tracks.get(bone['Name'])
            if tr is None:
                x = bone['Transform']; one = local(x['position'][None].astype('f8'), x['orientation'][None].astype('f8'), x['scale_shear'][None].astype('f8'))
                L = np.repeat(one, len(times), 0)
            else:
                k, q, degree = curve(g, tr['OrientationCurve']['CurveData'], 4)
                for i in range(1, len(q)):          # q and -q are the same turn: keep neighbours on one side, so interpolating doesn't swing the long way
                    q[i] *= np.sign(q[i] @ q[i - 1]) or 1
                q = sample(k, q, degree, times)
                L = local(sample(*curve(g, tr['PositionCurve']['CurveData'], 3), times), q, sample(*curve(g, tr['ScaleShearCurve']['CurveData'], 9), times))
            parent = int(bone['ParentIndex'])
            W.append(P @ L if parent < 0 else W[parent] @ L)
            B.append(np.linalg.inv(np.asarray(bone['InverseWorldTransform'], 'f8').reshape(4, 4)).T)
        out.append(W); rest.append(B)
    return out, rest, float(t['Animations'][0]['Duration'])
