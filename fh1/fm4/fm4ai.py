"""FM4 AI files (Media/AI/Tracks/<Track>_<ribbon>.geo, .seg, ...): MLP containers. A text header between 'MLPDataStart:' and 'MLPDataEnd:'
lists each field as name:type:count:?:offset, the offset absolute in the file; values are big endian.
python fm4ai.py "<FM4>/Media/AI/Tracks/Tsukuba_00.geo"      lists the fields"""
import re
import sys

import numpy as np

TYPES = {'float': '>f4', 'int32': '>i4', 'uint32': '>u4', 'int16': '>i2', 'uint16': '>u2', 'int8': 'i1', 'uint8': 'u1'}


def read(path):
    b = open(path, 'rb').read(); i = b.index(b'MLPDataStart:'); j = b.index(b'MLPDataEnd:')
    out = {}
    for line in b[i:j].decode('latin1').splitlines()[1:]:
        m = re.match(r'(\w+):(\w+):(\d+):(\d+):\s*(\d+)', line)
        if not m: continue
        name, typ, n, k, off = m.groups(); dt = np.dtype(TYPES[typ])
        out[name] = np.frombuffer(b, dt, int(n) * int(k), int(off)).astype(dt.newbyteorder('=')).reshape(int(n), int(k)).squeeze(-1) if int(k) == 1 else \
            np.frombuffer(b, dt, int(n) * int(k), int(off)).reshape(int(n), int(k))
    return out


def line(path):
    """the AI waypoints (n, 3) and their normals, in the game's frame"""
    g = read(path)
    return np.c_[g['fWaypointX'], g['fWaypointY'], g['fWaypointZ']], np.c_[g['fNormalX'], g['fNormalY'], g['fNormalZ']], g


if __name__ == '__main__':
    for k, v in read(sys.argv[1]).items():
        print(f'{k:24} {v.dtype} {v.shape} {v[:4]}')
