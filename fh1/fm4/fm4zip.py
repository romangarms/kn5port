"""Extract an FM4 track's bin.zip into <work>/x, the way FH1's index_zip.py and extract.py do. Every entry is there once.
python fm4zip.py "<FM4>/Media/Tracks/Tsukuba/bin.zip" tsukuba"""
import collections
import json
import os
import struct
import sys
import zlib

sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pipeline'))
import lzxdec


def entries(f, size):
    f.seek(size - 22); _, _, _, _, _, cs, co, _ = struct.unpack('<IHHHHIIH', f.read(22))
    f.seek(co); b = f.read(cs); o = 0; out = []
    while b[o:o + 4] == b'PK\x01\x02':
        _, _, fl, m, _, _, crc, c, u, nl, el, cml, _, _, _, lo = struct.unpack('<HHHHHHIIIHHHHHII', b[o + 4:o + 46])
        x = b[o + 46 + nl:o + 46 + nl + el]
        out.append(dict(n=b[o + 46:o + 46 + nl].decode('latin1'), m=m, c=c, u=u, lo=lo, x=x.hex(), crc=crc)); o += 46 + nl + el + cml
    return out


def main(zp, work):
    f = open(zp, 'rb'); E = entries(f, os.path.getsize(zp))
    os.makedirs(work + '/x', exist_ok=True); json.dump(E, open(work + '/ents.json', 'w'))
    bad = 0; kinds = collections.Counter()
    for e in E:
        xb = bytes.fromhex(e['x'])
        if xb[:2] != b'\x23\x11': raise ValueError(f"{e['n']}: no 0x1123 extra field")
        f.seek(struct.unpack('<I', xb[4:8])[0]); c = f.read(e['c'])
        d = c if e['m'] == 0 else lzxdec.decode(c, e['u'], 17)
        bad += zlib.crc32(d) & 0xffffffff != e['crc']
        p = os.path.join(work, 'x', e['n'].replace('/', '_').replace('\\', '_'))
        if os.path.exists(p): p += '.lc'
        open(p, 'wb').write(d); kinds[os.path.splitext(e['n'])[1]] += 1
    print(len(E), 'entries, bad crc', bad, kinds.most_common(8))


if __name__ == '__main__':
    main(*sys.argv[1:3])
