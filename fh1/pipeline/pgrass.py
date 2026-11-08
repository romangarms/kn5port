"""Reader for FH1's Grass_Ungrouped .pgeo sets: a patch of ground triangles and the sprite layers FH1 scatters over it at runtime. See docs/formats.md."""
import struct
import numpy as np

X = '../x/'


def parse(n):
    """-> dict, or None if not a grass set. P: vertices in .pgeo axes (z opposite to mesh z). T: triangles. wt: density weight per vertex.
    gid: group per vertex. tex: PVS texture indices (IDX[i][0] is the texture id). tail: offset of the tables after the triangles"""
    b = open(X + n, 'rb').read()
    if b[:4] != b'OEGP' or b'Grass_Ungrouped' not in b[0x50:0x90]:
        return None
    name = b[0x60:0xa0].split(b'\0')[0].decode(); nt_ = struct.unpack_from('>I', b, 0x40)[0]
    o = (0x60 + len(name) + 1 + 3) & ~3
    tex = [struct.unpack_from('>I', b, o + 8 * i)[0] for i in range(nt_)]; o += 8 * nt_
    B0 = np.array(struct.unpack_from('>8f', b, 0x10))
    for o2 in range(o, o + 80, 4):        # the patch's own box follows, within metres of the set's box. Some files keep 8 more bytes before it
        bb = np.array(struct.unpack_from('>8f', b, o2))
        if np.all(np.abs(bb - B0) < 20):
            break
    else:
        return None
    o = o2 + 32; nv, ni = struct.unpack_from('>II', b, o); o += 32
    v = np.frombuffer(b, '>u2', nv * 6, o).reshape(nv, 6).astype(np.int64); o += nv * 12      # 3 u16 position in the box, weight, group, 0xffff
    T = np.frombuffer(b, '>u2', ni, o).astype(np.int64).reshape(-1, 3); o += ni * 2
    o += 2 * len(T); o = (o + 3) & ~3                                                      # a u16 per triangle, 1 to n
    pairs = np.frombuffer(b, '>u4', 2 * len(T), o).reshape(-1, 2) / 65536.0; o += 8 * len(T)  # 16.16 area (capped at 200 m2) and slope in degrees
    P = bb[:3] + v[:, :3] / 65535.0 * (bb[4:7] - bb[:3])
    return dict(name=name, b=b, tex=tex, P=P, T=T, wt=v[:, 3] / 65535.0, gid=v[:, 4], area_slope=pairs, tail=o)


def tables(g):
    """sprites, layers and groups. sprite: (w0, w1, h0, h1, u0, u1, v_bottom, v_top, ?, ?, texture slot). layer: (density per m2, 40, 50 or 60,
    four unknown floats, sprite count, [sprite indices]). groups: layer indices per vertex group. glob: four distances, see formats.md"""
    b = g['b']; o = g['tail']
    w = struct.unpack_from('>11I', b, o); nsp, nly, ngr = w[1], w[3], w[5]
    glob = struct.unpack_from('>4f', b, o + 44); o += 60
    grp = [struct.unpack_from('>I', b, o + 8 * i + 4)[0] for i in range(ngr)]; o += 8 * ngr
    ids = struct.unpack_from('>%dI' % sum(grp), b, o); o += 4 * sum(grp)
    groups = []; k = 0
    for n in grp:
        groups.append(ids[k:k + n]); k += n
    sprites = [struct.unpack_from('>8f3I', b, o + 44 * i) for i in range(nsp)]; o += 44 * nsp
    layers = [list(struct.unpack_from('>7fI', b, o + 64 * i + 4)) for i in range(nly)]; o += 64 * nly
    for L in layers:                       # then each layer's sprite indices, in layer order
        L.append(struct.unpack_from('>%dI' % L[7], b, o)); o += 4 * L[7]
    if any(s_ >= nsp for L in layers for s_ in L[8]) or any(l_ >= nly for G in groups for l_ in G):
        raise ValueError('grass tables')
    return dict(glob=glob, groups=groups, sprites=sprites, layers=layers)
