"""Tree imposters with mips that keep their shape. BeamNG captures a forest shape's imposter into temp/ the first time a level loads, and its
own mips lose thin trees: the lodgepole pines' 64 px to 8 px levels came out empty, and an empty level draws as a dark square at a distance.
This rebuilds each captured imposter's mips from its top level, colour weighted by alpha and alpha scaled so the share passing the alpha
test stays the top level's, into ../impcache/. build3.py ships those beside the shapes, where BeamNG reads them instead of capturing.
Load the level once, then: python impfix.py [fh1_clearsprings|fh1_colorado ...]. The next build carries them"""
import os, sys, glob, shutil, struct
import numpy as np


def decode(b, w, h, o):
    blk = np.frombuffer(b, 'u1', (w // 4) * (h // 4) * 16, o).reshape(h // 4, w // 4, 16).astype(np.uint32)
    a = np.stack([blk[..., :8] & 15, blk[..., :8] >> 4], -1).reshape(h // 4, w // 4, 16) * 17
    c0 = blk[..., 8] | blk[..., 9] << 8; c1 = blk[..., 10] | blk[..., 11] << 8; idx = blk[..., 12] | blk[..., 13] << 8 | blk[..., 14] << 16 | blk[..., 15] << 24
    def rgb(c): return np.stack([(c >> 11) * 255 / 31, ((c >> 5) & 63) * 255 / 63, (c & 31) * 255 / 31], -1)
    P = np.stack([rgb(c0), rgb(c1), (2 * rgb(c0) + rgb(c1)) / 3, (rgb(c0) + 2 * rgb(c1)) / 3], 2)
    k = np.stack([(idx >> (2 * i)) & 3 for i in range(16)], -1)
    px = np.take_along_axis(P, k[..., None].astype(int), 2)
    out = np.zeros((h, w, 4))
    out[..., :3] = px.reshape(h // 4, w // 4, 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(h, w, 3)
    out[..., 3] = a.reshape(h // 4, w // 4, 4, 4).transpose(0, 2, 1, 3).reshape(h, w)
    return out


def encode(img):
    h, w = img.shape[:2]; H, W = max(h, 4), max(w, 4)
    if (H, W) != (h, w):
        img = np.pad(img, ((0, H - h), (0, W - w), (0, 0)), mode='edge')
    B = img.reshape(H // 4, 4, W // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    a = np.clip(np.rint(B[..., 3] / 17), 0, 15).astype(np.uint8)
    ab = (a[:, 0::2] | a[:, 1::2] << 4).astype(np.uint8)
    rgb = B[..., :3]; lum = rgb @ [0.3, 0.59, 0.11]
    hi = rgb[np.arange(len(B)), lum.argmax(1)]; lo = rgb[np.arange(len(B)), lum.argmin(1)]
    def q(c): return (np.clip(np.rint(c[:, 0] * 31 / 255), 0, 31).astype(np.uint32) << 11) | (np.clip(np.rint(c[:, 1] * 63 / 255), 0, 63).astype(np.uint32) << 5) | np.clip(np.rint(c[:, 2] * 31 / 255), 0, 31).astype(np.uint32)
    c0 = q(hi); c1 = q(lo)
    sw = c0 < c1; c0, c1 = np.where(sw, c1, c0), np.where(sw, c0, c1); hi, lo = np.where(sw[:, None], lo, hi), np.where(sw[:, None], hi, lo)
    c0 = np.where(c0 == c1, np.minimum(c0 + 1, 0xffff), c0)       # keep 4-colour mode
    P = np.stack([hi, lo, (2 * hi + lo) / 3, (hi + 2 * lo) / 3], 1)
    k = ((rgb[:, :, None, :] - P[:, None]) ** 2).sum(-1).argmin(-1).astype(np.uint32)
    idx = np.zeros(len(B), np.uint32)
    for i in range(16):
        idx |= k[:, i] << (2 * i)
    out = np.zeros((len(B), 16), np.uint8); out[:, :8] = ab
    out[:, 8] = c0 & 255; out[:, 9] = c0 >> 8; out[:, 10] = c1 & 255; out[:, 11] = c1 >> 8
    for i in range(4):
        out[:, 12 + i] = (idx >> (8 * i)) & 255
    return out.tobytes()


def down(img, cover, ref):
    h, w = img.shape[:2]; q = img.reshape(h // 2, 2, w // 2, 2, 4)
    a = q[..., 3]; wsum = a.sum((1, 3)); n = np.zeros((h // 2, w // 2, 4))
    n[..., :3] = (q[..., :3] * a[..., None]).sum((1, 3)) / np.maximum(wsum, 1e-6)[..., None]
    n[..., 3] = a.mean((1, 3))
    # scale alpha so the share passing the alpha test stays the top level's
    if cover > 0 and (n[..., 3] > 0).any():
        v = np.sort(n[..., 3].ravel())[::-1]; t = v[min(int(cover * len(v)), len(v) - 1)]
        if t > 0:
            n[..., 3] = np.clip(n[..., 3] * (ref / t), 0, 255)
    return n




def fix(b, ref=128):
    """the imposter dds bytes with rebuilt mips"""
    h, w = struct.unpack_from('<II', b, 12); mips = struct.unpack_from('<I', b, 28)[0]
    img = decode(b, w, h, 128); cover = (img[..., 3] >= ref).mean()
    data = [b[128:128 + (w // 4) * (h // 4) * 16]]; cur = img
    for _ in range(mips - 1):
        cur = down(cur, cover, ref) if cur.shape[0] > 1 and cur.shape[1] > 1 else cur
        data.append(encode(cur))
    return b[:128] + b''.join(data)


if __name__ == '__main__':
    out = os.path.join('..', 'impcache'); os.makedirs(out, exist_ok=True); n = 0
    for lvl in sys.argv[1:] or ['fh1_clearsprings', 'fh1_colorado']:
        src = os.path.expandvars(rf'%LOCALAPPDATA%/BeamNG/BeamNG.drive/current/temp/levels/{lvl}/art/forest/models')
        for f in glob.glob(os.path.join(src, '*.dae.imposter.dds')):
            b = open(f, 'rb').read()
            if b[84:88] != b'DXT3':
                continue
            open(os.path.join(out, os.path.basename(f)), 'wb').write(fix(b))
            nf = f.replace('.imposter.dds', '.imposter_normals.dds')
            if os.path.exists(nf):
                shutil.copyfile(nf, os.path.join(out, os.path.basename(nf)))
            n += 1
    print(n, 'imposters into', os.path.abspath(out))
