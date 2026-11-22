"""FM4 vertex layouts. The vertex buffer is the shader's vertex fetches packed in fetch order: position 12 bytes, tangent 8 (a second
packed vector follows it), everything else 4. A mesh drawn with several shaders uses the layout of one whose total equals its stride.
python fm4mesh.py tsukuba     checks every mesh: layout found, normals and tangents unit length and perpendicular"""
import collections
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4rmb
import xenos

SIZE = {'position0': 12, 'tangent0': 8}


def fetch_lists(work):
    """{shader name: [usage+index, ...]} from the first vertex shader in each track .fxobj"""
    F = {}
    for n in glob.glob(work + '/x/shaders_track_*.fxobj'):
        S = [s for s in xenos.shaders(open(n, 'rb').read()) if s['kind'] == 'vs']
        if S: F[os.path.basename(n)[14:-6].lower()] = [f'{u}{i}' for _, u, i in S[0]['bindings']]
    return F


def layout(fetches):
    o = 0; out = {}
    for f in fetches:
        out[f] = o; o += SIZE.get(f, 4)
    return out, o


def s10(w):
    w = w.astype(np.int64)
    f = lambda v: np.where(v >= 512, v - 1024, v) / 511.0
    return np.stack([f(w & 1023), f((w >> 10) & 1023), f((w >> 20) & 1023)], 1)


def attr(m, lay, name):
    a = np.frombuffer(m['vb'], 'u1').reshape(-1, m['sd']); o = lay[name]
    if name == 'position0': return a[:, :12].copy().view('>f4').astype('f8')
    w = a[:, o:o + 4].copy().view('>u4')[:, 0]
    if name in ('normal0', 'tangent0'): return s10(w)
    if name.startswith('texcoord'): return a[:, o:o + 4].copy().view('>u2').astype('f8') / 65535.0
    return a[:, o:o + 4]


def unit_share(m, lay, name):
    v = attr(m, lay, name); return np.mean(np.abs(np.linalg.norm(v, axis=1) - 1) < 0.03)


def mesh_layout(m, shaders, F):
    """the layout for a mesh, or None. Where two of the file's shaders fit the stride in different orders, the one whose normals read
    as unit vectors wins."""
    c = [layout(F[s])[0] for s in shaders if s in F and layout(F[s])[1] == m['sd']]
    if not c: return None
    return max(c, key=lambda lay: unit_share(m, lay, 'normal0') if 'normal0' in lay else 0)


if __name__ == '__main__':
    work = sys.argv[1]; F = fetch_lists(work)
    st = collections.Counter(); nun = []; tun = []; dots = []
    for n in sorted(glob.glob(work + '/x/*.rmb.bin')):
        b = open(n, 'rb').read(); p = fm4rmb.parse(b); shs = fm4rmb.file_shaders(b, p)
        for m in p['meshes']:
            lay = mesh_layout(m, shs, F)
            if lay is None: st['no layout'] += 1; continue
            st['ok'] += 1
            if 'normal0' in lay:
                nun.append(unit_share(m, lay, 'normal0'))
                if 'tangent0' in lay:
                    tun.append(unit_share(m, lay, 'tangent0'))
                    dots.append(np.median(np.abs((attr(m, lay, 'normal0') * attr(m, lay, 'tangent0')).sum(1))))
    print(dict(st), f'normals unit: {np.mean(np.array(nun) > 0.9):.3f} of meshes, tangents {np.mean(np.array(tun) > 0.9):.3f}, '
          f'median |N.T| {np.median(dots):.4f}')
