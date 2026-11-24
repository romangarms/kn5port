"""FM4's menu art and text, for the level selector: the track's photo, each layout's map, and layout names. See docs/fm4.md, Menu art.
- Media/UI/Textures.zip (bin.zip's format, fm4zip) holds .xds textures: a 52-byte D3DBaseTexture header whose last six words (0x1C) are
  the Xenos fetch constant, then the tiled top mip. The menu images are 0x32 (8888) in ARGB order.
- Media/StringTables/<lang>.zip holds .str tables (LSB2): at 0x28 a big-endian count n, then n (u16 key, u32 offset) pairs sorted by key, a
  (0xFFFF, 0, u16) sentinel, then UTF-16BE strings, zero-terminated; an offset counts UTF-16 units from the strings' start. A gamedb
  reference '_&<id>' is the entry keyed by id's low 16 bits.
python fm4ui.py Tsukuba                 prints the layouts and saves the images to fm4/ui/
python fm4ui.py --list <out folder> [Track ...]    every track the app can build: tracks.json and a photo per track (kn5port's
                                                  FM4 tab). Tracks named are on the disc but not copied out yet"""
import json
import os
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(1, str(HERE.parent / 'pipeline'))
import fm4zip
import lzxdec
import tex

FM4 = Path(os.environ.get('FM4_DUMP', r'D:\Roms\Xbox 360\Xenia\FM4XeniaFix'))
MEDIA = FM4 / 'Media'
_INDEX = {}


def read(zp, name):
    """one entry of an FM4 zip (fm4zip's layout), by its stored name"""
    zp = str(zp)
    if zp not in _INDEX:
        with open(zp, 'rb') as f: _INDEX[zp] = {e['n'].lower(): e for e in fm4zip.entries(f, os.path.getsize(zp))}
    e = _INDEX[zp][name.replace('\\', '/').lower()]
    with open(zp, 'rb') as f:
        f.seek(struct.unpack('<I', bytes.fromhex(e['x'])[4:8])[0]); c = f.read(e['c'])
    d = c if e['m'] == 0 else lzxdec.decode(c, e['u'], 17)
    if zlib.crc32(d) & 0xffffffff != e['crc']: raise IOError(f'{zp}: {name} fails its CRC')
    return d


def xds_image(b):
    """an 8888 .xds as an RGB PIL image"""
    fc = struct.unpack('>6I', b[0x1C:0x34]); fmt = fc[1] & 0x3F
    w = (fc[2] & 0x1FFF) + 1; h = ((fc[2] >> 13) & 0x1FFF) + 1
    if fmt not in (0x06, 0x32): raise ValueError(f'xds format {fmt:#x} is not 8888')
    a = np.frombuffer(tex.untile(b[0x34:], w, h, 4), 'u1').reshape(h, w, 4)
    return Image.fromarray(a[..., 1:].copy())          # ARGB


def ui_image(path):
    """a gamedb texture path ('GAME:\\Media\\UI\\Textures\\UI4\\...\\x.tga') or a Textures.zip name, as an RGB image"""
    p = path.replace('\\', '/'); i = p.lower().find('/textures/')
    name = (p[i + len('/textures/'):] if i >= 0 else p).rsplit('.', 1)[0] + '.xds'
    return xds_image(read(MEDIA / 'UI' / 'Textures.zip', name))


_STR = {}


def string(ref, table='Tracks', lang='EN'):
    """a gamedb '_&<id>' reference in that string table, or ref itself when it isn't one"""
    if not (ref or '').startswith('_&'): return ref
    if (table, lang) not in _STR:
        d = read(MEDIA / 'StringTables' / f'{lang}.zip', f'{table}.str'); n = struct.unpack_from('>I', d, 0x28)[0]
        base = 0x2C + 6 * n + 6; out = {}
        for k in range(n):
            key, off = struct.unpack_from('>HI', d, 0x2C + 6 * k); s = base + 2 * off; e = s
            while d[e:e + 2] != b'\0\0': e += 2
            out[key] = d[s:e].decode('utf-16-be')
        _STR[(table, lang)] = out
    return _STR[(table, lang)].get(int(ref[2:]) & 0xFFFF, ref)


def layouts(track):
    """gamedb's rows for a track's ribbons, as dicts, in ribbon order"""
    db = sqlite3.connect(MEDIA / 'db' / 'gamedb.slt'); db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('select * from Tracks where MediaName=? order by RibbonIndex', (track,))]
    for r in rows:
        r['name'] = string(r['DisplayName']); r['short'] = string(r['ShortDisplayName'])
    return rows


def ribbon_art(row):
    """a layout's large map (Track_Ribbons_LG), else its mini map"""
    mini = row['MiniRibbonPath']
    try: return ui_image(mini.replace('Mini_Track_Ribbons', 'Track_Ribbons_LG'))
    except KeyError: return ui_image(mini)


def environments(rows):
    """the Environments rows (venue name, location, description, photo name) a track's layouts use, in first-use order"""
    db = sqlite3.connect(MEDIA / 'db' / 'gamedb.slt'); db.row_factory = sqlite3.Row; out = []
    for i in dict.fromkeys(r['EnvironmentId'] for r in rows):
        e = db.execute('select * from Environments where Id=?', (i,)).fetchone()
        if e:
            e = dict(e); out.append(e)
            for k in ('Location', 'DisplayName', 'ShortDisplayName', 'Description'): e[k] = string(e[k], 'Environments')
    return out


def photo(env):
    """a venue's photo, UI4/Track_Images/<ThumbImageName>1, or None"""
    try: return ui_image(f"UI4/Track_Images/{env['ThumbImageName']}1.xds")
    except KeyError: return None


def fit(im, size, pad=None):
    """im scaled to size: cropped to the aspect from the centre, or, with a pad colour, its content (pixels unlike the pad) letterboxed"""
    W, H = size
    if pad is not None:
        a = np.asarray(im).astype(int); ys, xs = np.nonzero(np.abs(a - np.array(pad)).sum(2) > 30)
        if len(xs):
            m = 24; box = [max(xs.min() - m, 0), max(ys.min() - m, 0), min(xs.max() + m, im.width), min(ys.max() + m, im.height)]
            im = im.crop(box)
        s = min(W / im.width, H / im.height); im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        out = Image.new('RGB', size, tuple(pad)); out.paste(im, ((W - im.width) // 2, (H - im.height) // 2)); return out
    want = im.height * W / H
    if want <= im.width: l = (im.width - want) / 2; im = im.crop((round(l), 0, round(l + want), im.height))
    else: h = im.width * H / W; t = (im.height - h) / 2; im = im.crop((0, round(t), im.width, round(t + h)))
    return im.resize(size, Image.LANCZOS)


def tracks(have=()):
    """the tracks to offer: gamedb's active ones whose Media/Tracks folder has a bin.zip (or named in have), by venue. Each is
    {media, name, location, layouts}, from the first venue its layouts use"""
    have = {h.lower() for h in have}
    db = sqlite3.connect(MEDIA / 'db' / 'gamedb.slt')
    names = [m for (m,) in db.execute('select distinct MediaName from Tracks where Active=1 and IsHomespace=0 order by MediaName')]
    out = []
    for m in names:
        if m.lower() not in have and not (MEDIA / 'Tracks' / m / 'bin.zip').exists(): continue
        rows = layouts(m); envs = environments(rows)
        out.append({'media': m, 'name': ' / '.join(e['DisplayName'] for e in envs) or m,
                    'location': envs[0]['Location'] if envs else '', 'layouts': [r['name'] for r in rows], 'envs': envs})
    return out


def catalogue(out, have=()):
    """tracks() as out/tracks.json, with each track's first photo as out/<media>.jpg (240 x 180), for the app"""
    out = Path(out); out.mkdir(parents=True, exist_ok=True); T = tracks(have)
    for t in T:
        ph = next((p for p in map(photo, t.pop('envs')) if p is not None), None)
        if ph is not None:
            fit(ph, (240, 180)).save(out / f"{t['media']}.jpg", quality=88); t['photo'] = f"{t['media']}.jpg"
    (out / 'tracks.json').write_text(json.dumps({'dump': str(FM4), 'tracks': T}, indent=1, ensure_ascii=False), encoding='utf8')
    return T


if __name__ == '__main__' and sys.argv[1:2] == ['--list']:
    for t in catalogue(sys.argv[2], sys.argv[3:]): print(t['media'], '|', t['name'], '|', t['location'], '|', ', '.join(t['layouts']))
elif __name__ == '__main__':
    t = sys.argv[1] if len(sys.argv) > 1 else 'Tsukuba'; out = HERE / 'ui'; out.mkdir(exist_ok=True)
    rows = layouts(t)
    for r in rows:
        print(r['RibbonIndex'], r['name'], '|', r['short'], 'reverse' if r['IsReverse'] else '', r['StartLine'], r['FinishLine'])
        ribbon_art(r).save(out / f'{t}_{r["RibbonIndex"]:02d}.png')
    for e in environments(rows):
        ph = photo(e)
        if ph: ph.save(out / f"{t}_{e['ThumbImageName']}.png")
        print(e['DisplayName'], '|', e['Location'], '| photo', ph.size if ph else None, '|', e['Description'])
