"""FH1's developer map art (ui/New_Map_MapGameReady.jpg), cut to a world rectangle for menu and mission previews."""
import json
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
SIZE = (800, 600)                     # stock menu and mission images are 800x600


def to_px(r, x, z):
    """map art pixel of a point in mesh axes"""
    return x / r['sx'] + r['ox'], z / r['sz'] + r['oz']


def art(box, size=SIZE):
    """map art for a world rectangle (x0, z0, x1, z1) in mesh axes"""
    r = json.load(open('../reg.json')); src = Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB')
    x0, z0, x1, z1 = box
    return src.transform(size, Image.EXTENT, to_px(r, x0, z0) + to_px(r, x1, z1), Image.BICUBIC)
