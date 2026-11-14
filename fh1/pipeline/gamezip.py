"""Reads entries from the game's LZX zips in media/, or from a zip given by its full path. These keep normal local headers, unlike bin.zip."""
import os, struct, zlib
import lzxdec

from gamepath import MEDIA


def read(name, wanted):
    b = open(name if os.path.isabs(name) else MEDIA + name, 'rb').read()
    i = b.rfind(b'PK\x05\x06'); n = struct.unpack('<H', b[i + 10:i + 12])[0]; o = struct.unpack('<I', b[i + 16:i + 20])[0]
    for _ in range(n):
        crc, cs, us = struct.unpack('<III', b[o + 16:o + 28]); nl, xl, cl = struct.unpack('<HHH', b[o + 28:o + 34])
        lo = struct.unpack('<I', b[o + 42:o + 46])[0]; nm = b[o + 46:o + 46 + nl].decode('latin1'); o += 46 + nl + xl + cl
        if nm != wanted:
            continue
        ln, lx = struct.unpack('<HH', b[lo + 26:lo + 30]); c = b[lo + 30 + ln + lx:lo + 30 + ln + lx + cs]
        out = lzxdec.decode(c, us, 17)
        if zlib.crc32(out) & 0xffffffff != crc:
            raise IOError(wanted)
        return out
    raise KeyError(wanted)


def names(name):
    """every entry's name in one of the zips"""
    b = open(MEDIA + name, 'rb').read()
    i = b.rfind(b'PK'); n = struct.unpack('<H', b[i + 10:i + 12])[0]; o = struct.unpack('<I', b[i + 16:i + 20])[0]; out = []
    for _ in range(n):
        nl, xl, cl = struct.unpack('<HHH', b[o + 28:o + 34]); out.append(b[o + 46:o + 46 + nl].decode('latin1')); o += 46 + nl + xl + cl
    return out
