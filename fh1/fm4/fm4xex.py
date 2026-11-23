"""FM4's default.xex as a plain PE image, for xshader-style shader reading and code tracing (docs/fm4.md, Executable).
XEX2: optional header 0x3FF is the file format info: encryption 1 (AES-128-CBC, zero IV, whole image), compression 1 (basic: after the
8-byte header, (data size, zero size) pairs; the image is each block's data followed by that many zero bytes). The image key is the
security info's AES key (at +0x150) decrypted with the retail key in ECB.
python fm4xex.py [out]          writes fh1/fm4/default.pe (gitignored), base 0x82000000"""
import os
import struct
import sys

from Crypto.Cipher import AES

XEX = os.path.join(os.environ.get('FM4_DUMP', r'D:\Roms\Xbox 360\Xenia\FM4XeniaFix'), 'default.xex')
RETAIL = bytes.fromhex('20B185A59D28FDC340583FBB0896BF91')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'default.pe')


def image(path=XEX):
    b = open(path, 'rb').read()
    if b[:4] != b'XEX2': raise ValueError('not XEX2')
    _, _, pe, _, sec, n = struct.unpack_from('>4sIIIII', b, 0)
    opt = dict(struct.unpack_from('>II', b, 24 + 8 * i) for i in range(n))
    ff = opt[0x3ff]; size, enc, comp = struct.unpack_from('>IHH', b, ff)
    data = b[pe:]
    if enc == 1:
        key = AES.new(RETAIL, AES.MODE_ECB).decrypt(b[sec + 0x150:sec + 0x160])
        data = AES.new(key, AES.MODE_CBC, bytes(16)).decrypt(data[:len(data) // 16 * 16])
    if comp == 1:
        out = bytearray(); o = 0
        for k in range((size - 8) // 8):
            ds, zs = struct.unpack_from('>II', b, ff + 8 + 8 * k)
            out += data[o:o + ds] + bytes(zs); o += ds
        data = bytes(out)
    elif comp != 0:
        raise ValueError(f'compression {comp} not handled')
    if data[:2] != b'MZ': raise ValueError('image does not start with MZ: wrong key or layout')
    return data


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else OUT
    d = image(); open(out, 'wb').write(d); print(len(d), 'bytes ->', out)
