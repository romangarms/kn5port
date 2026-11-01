"""Draw the kn5port app icon: "kn" in light strokes, "5" in a BeamNG-orange circle, on a dark rounded tile.

The letters are geometric strokes (no font), so the icon renders identically on any machine.

    python packaging/make_icon.py            -> packaging/kn5port.png (1024) + packaging/kn5port.ico
"""
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


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    img = draw()
    img.save(out / "kn5port.png")
    img.save(out / "kn5port.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.resize((64, 64), Image.LANCZOS).save(out / "favicon.png")
    print(f"wrote {out / 'kn5port.png'}, kn5port.ico, favicon.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
