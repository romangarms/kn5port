"""DXT1 and BC5 .dds writers. A top mip that is already DXT1 or BC5 is kept as FH1's own blocks; everything else is encoded here."""
import struct
import numpy as np
from numba import njit
from PIL import Image


@njit(cache=True)
def _encode(a):
    """a: h, w, 3 uint8. Returns DXT1 blocks as (endpoints, indices) u32 pairs."""
    h, w = a.shape[:2]; bw = (w + 3) // 4; bh = (h + 3) // 4
    out = np.zeros((bh * bw, 2), np.uint32); p = np.zeros((16, 3)); pal = np.zeros((4, 3))
    scale = np.array([31.0, 63.0, 31.0]) / 255; top = np.array([31.0, 63.0, 31.0])
    for by in range(bh):
        for bx in range(bw):
            for k in range(16):
                y = min(by * 4 + k // 4, h - 1); x = min(bx * 4 + k % 4, w - 1)
                for c in range(3):
                    p[k, c] = a[y, x, c]
            m = np.array([p[:, 0].mean(), p[:, 1].mean(), p[:, 2].mean()])
            q = p - m; cov = q.T @ q; v = np.array([1.0, 1.0, 1.0])
            for _ in range(4):           # principal axis by power iteration
                v = cov @ v; n = np.sqrt((v * v).sum())
                if n < 1e-9:
                    v = np.array([1.0, 1.0, 1.0]); break
                v = v / n
            t = q @ v
            e0 = np.minimum(np.maximum(np.rint((m + v * t.max()) * scale), 0.0), top)
            e1 = np.minimum(np.maximum(np.rint((m + v * t.min()) * scale), 0.0), top)
            c0 = (int(e0[0]) << 11) | (int(e0[1]) << 5) | int(e0[2])
            c1 = (int(e1[0]) << 11) | (int(e1[1]) << 5) | int(e1[2])
            if c0 < c1:
                c0, c1 = c1, c0
            pal[0, 0] = (c0 >> 11) * 255 / 31; pal[0, 1] = ((c0 >> 5) & 63) * 255 / 63; pal[0, 2] = (c0 & 31) * 255 / 31
            pal[1, 0] = (c1 >> 11) * 255 / 31; pal[1, 1] = ((c1 >> 5) & 63) * 255 / 63; pal[1, 2] = (c1 & 31) * 255 / 31
            pal[2] = (2 * pal[0] + pal[1]) / 3; pal[3] = (pal[0] + 2 * pal[1]) / 3
            sel = 0
            if c0 != c1:                 # equal endpoints would select three-colour mode, so they stay on index 0
                for k in range(16):
                    best = 1e18; bi = 0
                    for i in range(4):
                        d = ((p[k] - pal[i]) ** 2).sum()
                        if d < best:
                            best = d; bi = i
                    sel |= bi << (2 * k)
            out[by * bw + bx, 0] = c0 | (c1 << 16); out[by * bw + bx, 1] = sel
    return out


def save(path, a, blocks=None, limit=1 << 30, peak=False):
    """a: h, w, 3 (or h, w for a mask) uint8. blocks: FH1's DXT1 data for the top mip, used unchanged.
    peak: smaller mips keep the brightest of each 2x2, so points such as stars survive. Needs even sizes"""
    a = np.dstack([a] * 3) if a.ndim == 2 else a[..., :3]
    h, w = a.shape[:2]
    while max(h, w) > limit:
        h = max(h // 2, 1); w = max(w // 2, 1); a = np.asarray(Image.fromarray(a).resize((w, h), Image.BOX)); blocks = None
    H, W = h, w; mips = []
    while True:
        mips.append(blocks if blocks is not None else _encode(a).astype('<u4').tobytes()); blocks = None
        if w == 1 and h == 1:
            break
        w = max(w // 2, 1); h = max(h // 2, 1)
        a = a.reshape(h, 2, w, 2, 3).max((1, 3)) if peak else np.asarray(Image.fromarray(a).resize((w, h), Image.BOX))
    hdr = b'DDS ' + struct.pack('<7I', 124, 0xA1007, H, W, len(mips[0]), 0, len(mips)) + bytes(44)
    hdr += struct.pack('<2I4s5I', 32, 4, b'DXT1', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr); f.write(b''.join(mips))


@njit(cache=True)
def _encode_bc4(a):
    """a: h, w uint8. Returns BC4 blocks, 8 bytes each, in the eight-value mode."""
    h, w = a.shape; bw = (w + 3) // 4; bh = (h + 3) // 4
    out = np.zeros((bh * bw, 8), np.uint8); v = np.zeros(16); pal = np.zeros(8)
    for by in range(bh):
        for bx in range(bw):
            for k in range(16):
                v[k] = a[min(by * 4 + k // 4, h - 1), min(bx * 4 + k % 4, w - 1)]
            a0 = int(v.max()); a1 = int(v.min())
            o = by * bw + bx; out[o, 0] = a0; out[o, 1] = a1
            if a0 == a1:
                continue
            pal[0] = a0; pal[1] = a1
            for k in range(2, 8):
                pal[k] = ((8 - k) * a0 + (k - 1) * a1) // 7
            bits = 0
            for k in range(16):
                best = 1e9; bi = 0
                for i in range(8):
                    d = abs(v[k] - pal[i])
                    if d < best:
                        best = d; bi = i
                bits |= bi << (3 * k)
            for k in range(6):
                out[o, 2 + k] = (bits >> (8 * k)) & 255
    return out


def save_bc5(path, rg, blocks=None, limit=1 << 30):
    """rg: h, w, 2 uint8 tangent-space x and y, as a BC5U normal map. blocks: FH1's BC5 data for the top mip, used unchanged."""
    h, w = rg.shape[:2]
    while max(h, w) > limit:
        h = max(h // 2, 1); w = max(w // 2, 1); blocks = None
        rg = np.dstack([np.asarray(Image.fromarray(np.ascontiguousarray(rg[..., c])).resize((w, h), Image.BOX)) for c in range(2)])
    H, W = h, w; mips = []
    while True:
        mips.append(blocks if blocks is not None else np.concatenate([_encode_bc4(np.ascontiguousarray(rg[..., c])) for c in range(2)], 1).tobytes()); blocks = None
        if w == 1 and h == 1:
            break
        w = max(w // 2, 1); h = max(h // 2, 1)
        rg = np.dstack([np.asarray(Image.fromarray(np.ascontiguousarray(rg[..., c])).resize((w, h), Image.BOX)) for c in range(2)])
    hdr = b'DDS ' + struct.pack('<7I', 124, 0xA1007, H, W, len(mips[0]), 0, len(mips)) + bytes(44)
    hdr += struct.pack('<2I4s5I', 32, 4, b'BC5U', 0, 0, 0, 0, 0) + struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(hdr); f.write(b''.join(mips))
