"""Draw the kn5port app icon: "kn" in light strokes, "5" in a BeamNG-orange circle, on a dark rounded tile.

The letters are geometric strokes (no font), so the icon renders identically on any machine.

    python packaging/make_icon.py            -> packaging/kn5port.png (1024) + packaging/kn5port.ico
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 4                      # supersampling factor
N = 1024 * S               # working canvas
BG = (21, 24, 29, 255)
INK = (236, 238, 242, 255)
ORANGE = (255, 122, 26, 255)
ORANGE_INK = (26, 13, 0, 255)


def p(v):
    return int(round(v * S))


def stroke(d, pts, w, fill):
    """Polyline with round caps and joins."""
    pts = [(p(x), p(y)) for x, y in pts]
    d.line(pts, fill=fill, width=p(w), joint="curve")
    r = p(w) / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


def arc(d, cx, cy, rad, start, end, w, fill):
    """Arc of a circle (degrees, clockwise from 3 o'clock) with round caps."""
    import math
    box = (p(cx - rad), p(cy - rad), p(cx + rad), p(cy + rad))
    d.arc(box, start, end, fill=fill, width=p(w))
    r = p(w) / 2
    for a in (start, end):
        x = cx + (rad - w / 2) * math.cos(math.radians(a))
        y = cy + (rad - w / 2) * math.sin(math.radians(a))
        d.ellipse((p(x) - r, p(y) - r, p(x) + r, p(y) + r), fill=fill)


def draw():
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # tile
    d.rounded_rectangle((p(40), p(40), p(984), p(984)), radius=p(200), fill=BG)

    w = 84                  # stroke weight
    base, xh, asc = 700, 460, 300
    # k
    kx = 128
    stroke(d, [(kx, asc), (kx, base)], w, INK)
    stroke(d, [(kx + 156, xh), (kx + 14, 600)], w, INK)
    stroke(d, [(kx + 70, 560), (kx + 150, base)], w, INK)
    # n
    nx, nw = 382, 150
    rr = nw / 2
    stroke(d, [(nx, xh), (nx, base)], w, INK)
    arc(d, nx + rr, xh + rr, rr + w / 2, 180, 360, w, INK)
    stroke(d, [(nx + nw, xh + rr), (nx + nw, base)], w, INK)

    # orange disc with the 5
    cx, cy, R = 774, 520, 178
    k_ = R / 205
    d.ellipse((p(cx - R), p(cy - R), p(cx + R), p(cy + R)), fill=ORANGE)
    fw = 62 * k_
    top, left, right = cy - 116 * k_, cx - 60 * k_, cx + 68 * k_
    stroke(d, [(right, top), (left, top), (left - 5 * k_, cy - 22 * k_)], fw, ORANGE_INK)
    bowl_r, bcx, bcy = 90 * k_, cx + 2 * k_, cy + 38 * k_
    arc(d, bcx, bcy, bowl_r + fw / 2, 220, 495, fw, ORANGE_INK)
    return img.resize((1024, 1024), Image.LANCZOS)


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def write_ico(img, path, sizes=ICO_SIZES):
    """A Windows .ico the way Explorer expects it: 32-bit BMP (DIB + AND mask) entries for the small sizes and a
    PNG entry only for 256, with planes = 1. Pillow writes every size as PNG with planes = 0, which Explorer
    doesn't reliably pick up from an exe's icon resource (it falls back to the generic exe icon)."""
    entries = []
    for s in sizes:
        im = img.resize((s, s), Image.LANCZOS).convert("RGBA")
        if s >= 256:
            buf = io.BytesIO()
            im.save(buf, "PNG")
            data = buf.getvalue()
        else:
            r, g, b, a = im.split()
            xor = Image.merge("RGBA", (b, g, r, a)).transpose(Image.FLIP_TOP_BOTTOM).tobytes()   # BGRA, bottom-up
            row = ((s + 31) // 32) * 4
            mask = bytearray(row * s)                     # AND mask: 1 = transparent (alpha 0)
            alpha = a.transpose(Image.FLIP_TOP_BOTTOM).tobytes()
            for y in range(s):
                for x in range(s):
                    if alpha[y * s + x] == 0:
                        mask[y * row + x // 8] |= 0x80 >> (x % 8)
            header = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
            data = header + xor + bytes(mask)
        entries.append((s, data))
    out = struct.pack("<HHH", 0, 1, len(entries))
    off = 6 + 16 * len(entries)
    for s, data in entries:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), off)
        off += len(data)
    Path(path).write_bytes(out + b"".join(d for _, d in entries))


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(out / "kn5port.png")
    write_ico(img, out / "kn5port.ico")
    img.resize((64, 64), Image.LANCZOS).save(out / "favicon.png")
    print(f"wrote {out / 'kn5port.png'}, kn5port.ico, favicon.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
