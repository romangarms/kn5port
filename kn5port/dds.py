"""DDS writers with full mip chains, so BeamNG loads textures as shipped instead of cooking them.

BeamNG cooks every PNG a level uses into temp/ on first load (California Highway: 440 textures, 376 MB) and keeps
the cooked copy until the PNG changes. A .dds that is already block-compressed, power-of-two and mipped loads as
it is: FH1's whole map ships that way (fh1/docs/beamng-output.md, Textures).

Formats, by BeamNG's colour-space suffix:
    .color.  DXT1, or DXT5 where the image has real alpha
    .data.   DXT1 (greyscale masks and detail maps); opacity maps BC5U, whose red keeps 8 bits
    .normal. BC5U, tangent-space x and y, as stock normal maps
BeamNG's own cook makes BC7 of colour and data maps; Pillow can't encode BC7, and DXT1 is half its size.

Blocks are encoded by Pillow (11.2 and up). `available()` says whether it can, so callers fall back to PNG.
"""
import io
import os
import struct

import numpy as np
from PIL import Image

BLOCK = {b"DXT1": 8, b"DXT5": 16, b"BC5U": 16}
PILLOW_FMT = {b"DXT1": "DXT1", b"DXT5": "DXT5", b"BC5U": "BC5"}


def write(path, data):
    """Whole or not at all, through a temporary file, as two builds can write one cache file at once and the other
    may be copying it. If another writer's copy got there first and is open, keep it: both hold the same texture"""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, path)
    except PermissionError:
        os.remove(tmp)
        if not os.path.exists(path):
            raise


def bc4(a, chunk=1 << 16):
    """a: h, w uint8 -> BC4 blocks (n, 8) uint8 in the eight-value mode: endpoints the block's max and min, each
    texel the nearest of the eight levels between. The same encoder as FH1's, in numpy. Pillow's own BC5 lost
    detail on sharp masks (a fence's opacity: 21 dB against DXT1's 26)"""
    h, w = a.shape
    H, W = -(-h // 4) * 4, -(-w // 4) * 4
    if (H, W) != (h, w):
        a = np.pad(a, ((0, H - h), (0, W - w)), mode="edge")
    blk = a.reshape(H // 4, 4, W // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.int32)
    out = np.empty((len(blk), 8), np.uint8)
    k = np.arange(8)
    for s in range(0, len(blk), chunk):
        b = blk[s:s + chunk]
        a0, a1 = b.max(1), b.min(1)
        pal = np.empty((len(b), 8), np.int32)
        pal[:, 0], pal[:, 1] = a0, a1
        pal[:, 2:] = ((8 - k[2:]) * a0[:, None] + (k[2:] - 1) * a1[:, None]) // 7
        idx = np.abs(b[:, :, None] - pal[:, None, :]).argmin(2).astype(np.uint64)
        idx[a0 == a1] = 0
        bits = (idx << (3 * np.arange(16, dtype=np.uint64))).sum(1, dtype=np.uint64)
        out[s:s + chunk, 0], out[s:s + chunk, 1] = a0, a1
        out[s:s + chunk, 2:] = ((bits[:, None] >> (8 * np.arange(6, dtype=np.uint64))) & 255).astype(np.uint8)
    return out


def _blocks(im, fourcc):
    """one mip level's compressed blocks: BC5 from bc4(), the rest by Pillow, without its header"""
    if fourcc == b"BC5U":
        a = np.asarray(im)
        return np.concatenate([bc4(np.ascontiguousarray(a[..., 0])), bc4(np.ascontiguousarray(a[..., 1]))], 1).tobytes()
    w, h = im.size
    n = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[fourcc]
    b = io.BytesIO()
    im.save(b, "DDS", pixel_format=PILLOW_FMT[fourcc])
    data = b.getvalue()
    if len(data) < n + 128:
        raise ValueError(f"Pillow wrote {len(data)} bytes for a {w}x{h} {fourcc.decode()} mip")
    return data[-n:]


def header(w, h, fourcc, mips, top_size):
    """a legacy DDS header with a FourCC: BeamNG reads DXT1, DXT5 and BC5U this way (FH1 ships them)"""
    hdr = b"DDS " + struct.pack("<7I", 124, 0xA1007, h, w, top_size, 0, mips) + bytes(44)
    hdr += struct.pack("<2I4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0)
    return hdr + struct.pack("<5I", 0x401008, 0, 0, 0, 0)


def encode(im, fourcc, top=None, peak=False):
    """DDS bytes of a PIL image with every mip down to 1x1. top: compressed blocks to use unchanged for the top mip.
    peak: smaller mips keep the brightest texel of each 2x2, so points such as stars survive (needs even sizes)."""
    mode = "RGBA" if fourcc == b"DXT5" else "RGB"
    im = im.convert(mode)
    W, H = w, h = im.size
    mips = []
    while True:
        mips.append(top if top is not None else _blocks(im, fourcc))
        top = None
        if w == 1 and h == 1:
            break
        w, h = max(w // 2, 1), max(h // 2, 1)
        if peak:
            a = np.asarray(im)
            im = Image.fromarray(np.ascontiguousarray(a[: h * 2, : w * 2].reshape(h, 2, w, 2, -1).max((1, 3))), mode)
        else:
            im = im.resize((w, h), Image.BOX)
    return header(W, H, fourcc, len(mips), len(mips[0])) + b"".join(mips)


def _fit(im, limit):
    while max(im.size) > limit:
        im = im.resize((max(im.size[0] // 2, 1), max(im.size[1] // 2, 1)), Image.BOX)
    return im


def save(path, a, blocks=None, limit=1 << 30, peak=False):
    """a: h, w, 3 (or h, w for a mask) uint8 array or a PIL image, as DXT1. blocks: DXT1 data for the top mip, used
    unchanged (dropped if the image has to shrink to `limit`)."""
    im = a if isinstance(a, Image.Image) else Image.fromarray(np.dstack([a] * 3) if a.ndim == 2 else np.ascontiguousarray(a[..., :3]))
    if max(im.size) > limit:
        im, blocks = _fit(im, limit), None
    write(path, encode(im, b"DXT1", blocks, peak))


def save_bc5(path, rg, blocks=None, limit=1 << 30):
    """rg: h, w, 2 uint8 tangent-space x and y (or an RGB array / PIL image, whose R and G are used) as a BC5U
    normal map. blocks: BC5 data for the top mip, used unchanged."""
    if isinstance(rg, Image.Image):
        rg = np.asarray(rg.convert("RGB"))
    im = Image.fromarray(np.dstack([rg[..., 0], rg[..., 1], np.zeros_like(rg[..., 0])]))
    if max(im.size) > limit:
        im, blocks = _fit(im, limit), None
    write(path, encode(im, b"BC5U", blocks))


def fourcc_for(im, kind):
    """the format for a texture of BeamNG kind 'color', 'data' or 'normal'"""
    if kind == "normal":
        return b"BC5U"
    if kind == "color" and im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 255:
        return b"DXT5"
    return b"DXT1"


def save_texture(path, im, kind, fourcc=None):
    """a PIL image as `path` in the format its kind wants (see the module notes), or as `fourcc`. Opacity maps
    want BC5U: BeamNG reads opacity from red, which BC5 keeps at 8 bits where DXT1 has 5 (FH1 ships them so),
    and DXT1 blurred thin cut-outs. California Highway's 66 opacity maps against their PNGs: median 41.1 dB as
    DXT1, 50.6 as BC5; its fence 25.7 and 36.5"""
    fc = fourcc or fourcc_for(im, kind)
    if fc == b"BC5U":
        save_bc5(path, im)
    else:
        write(path, encode(im, fc))


_OK = None


def available():
    """whether this Pillow can encode DDS blocks (11.2 and up)"""
    global _OK
    if _OK is None:
        try:
            _blocks(Image.new("RGB", (4, 4)), b"DXT1")
            _OK = True
        except Exception:
            _OK = False
    return _OK
