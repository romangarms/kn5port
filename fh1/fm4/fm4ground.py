"""Top-down render of an FM4 track's ground shaders as the game composes them (docs/fm4.md, Ground blending), to check the decoded
formulas before BeamNG: road = B(world xz * c0.x) * mask.g, terr = lerp(A * tint, B, mask.g), shldr = lerp(A, B, mask.g) * vcolour.
python fm4ground.py tsukuba "<pvs>" cx cz size out.png [px per metre]      (cx, cz in the draw frame, as fm4pvs positions)"""
import glob
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4mesh
import fm4pvs
import fm4rmb
import tex
import xenos


def main(work, pvs, cx, cz, size, png, ppm=10):
    cx, cz, size, ppm = float(cx), float(cz), float(size), float(ppm)
    tex.X = work + '/x'; t = fm4pvs.tables(open(pvs, 'rb').read()); TT = t['textures']; F = fm4mesh.fetch_lists(work)
    smp = {}
    for f_ in glob.glob(work + '/x/shaders_track_*.fxobj'):
        ps = [s for s in xenos.shaders(open(f_, 'rb').read()) if s['kind'] == 'ps']
        if ps: smp[os.path.basename(f_)[14:-6].lower()] = {c[0]: c[2] for c in ps[0]['consts'] if c[1] == 3}
    stem = os.path.basename(glob.glob(work + '/x/*out.00000.rmb.bin')[0]).split('.')[0]
    N = int(size * ppm); img = np.full((N, N, 3), 40.0); zb = np.full((N, N), -1e9); cache = {}

    def T(i):
        if i not in cache:
            r = tex.load(i) if tex.bix(i) and tex.bix(i)['fmt'] in (0x52, 0x53, 0x54) else None
            cache[i] = r[0][..., :3].astype('f4') / 255 if r is not None and r[0] is not None else None
        return cache[i]

    def sample(a, uv):
        h, w = a.shape[:2]; x = (np.floor(uv[:, 0] * w).astype(int)) % w; y = (np.floor(uv[:, 1] * h).astype(int)) % h
        return a[y, x]
    for d in t['draws']:
        if not (np.allclose(d['rot'], np.diag([1, 1, -1]), atol=1e-3) and np.linalg.norm(d['pos']) < 1e-3): continue
        b = open(f"{work}/x/{stem}.{d['mesh']:05d}.rmb.bin", 'rb').read(); p = fm4rmb.parse(b); shs = fm4rmb.file_shaders(b, p)
        M = fm4mesh.materials(b, p); rec = t['meshes'][d['mesh']]
        for m in p['meshes']:
            lay = fm4mesh.mesh_layout(m, shs, F)
            if lay is None: continue
            pos = fm4mesh.attr(m, lay, 'position0')
            gx = (pos[:, 0] - (cx - size / 2)) * ppm; gy = (-pos[:, 2] - (-cz - size / 2)) * ppm      # draw frame z = -mesh z
            if gx.max() < 0 or gy.max() < 0 or gx.min() > N or gy.min() > N: continue
            for s in m['secs']:
                sh, slots, ps = M[s['ints'][2]]; nm = shs[sh]
                if not nm.startswith(('road_blnd', 'terr_blnd', 'shldr_blnd')): continue
                sm = smp.get(nm, {})

                def tid(name):
                    r = sm.get(name)
                    return TT[rec['tex'][slots[r]]][0] if r is not None and r < len(slots) and slots[r] < len(rec['tex']) else None
                box = lambda k: np.array(s['fl'][4:6]) + fm4mesh.attr(m, lay, k) * np.array(s['fl'][6:8])
                c = lambda row, k, n=2: np.array(ps[row][k:k + n]) if row < len(ps) else np.ones(n)
                A, Bt, V = (T(tid(n)) if tid(n) is not None else None for n in ('Blend_ASampler', 'Blend_BSampler', 'Blend_ValueSampler'))
                if nm.startswith('road'):
                    if Bt is None: continue
                    uvA = uvB = pos[:, [0, 2]] * c(0, 0, 1)[0]; uvM = box('texcoord2') * c(0, 1); shade = np.ones(len(pos))
                elif nm.startswith('terr'):
                    if A is None or Bt is None: continue
                    uvA = box('texcoord0') * c(1, 0); uvB = box('texcoord0') * c(2, 0); uvM = box('texcoord2'); shade = np.ones(len(pos))
                else:
                    if A is None or Bt is None or 'texcoord1' not in lay: continue
                    sA = c(0, 1) if 'decal' in nm else c(0, 0)
                    uvA = box('texcoord1') * sA; uvB = box('texcoord1') * c(1, 0); uvM = box('texcoord0')
                    shade = fm4mesh.attr(m, lay, 'color0')[:, 1:4].mean(1) / 255 * c(0, 0 if 'decal' in nm else 2, 1)[0] if 'color0' in lay else np.ones(len(pos))
                tint = c(0, 0, 3) if nm.startswith('terr') else np.ones(3)
                for tr in fm4rmb.tris(s['ib'], m['nv'], s['ints'][1]):
                    P2 = np.c_[gx[tr], gy[tr]]; x0, y0 = np.floor(P2.min(0)).astype(int); x1, y1 = np.ceil(P2.max(0)).astype(int)
                    x0, y0 = max(x0, 0), max(y0, 0); x1, y1 = min(x1, N - 1), min(y1, N - 1)
                    if x1 < x0 or y1 < y0: continue
                    yy, xx = np.mgrid[y0:y1 + 1, x0:x1 + 1]; q = np.c_[xx.ravel() + .5, yy.ravel() + .5]
                    Mx = np.array([[P2[1, 0] - P2[0, 0], P2[2, 0] - P2[0, 0]], [P2[1, 1] - P2[0, 1], P2[2, 1] - P2[0, 1]]])
                    if abs(np.linalg.det(Mx)) < 1e-9: continue
                    l = np.linalg.solve(Mx, (q - P2[0]).T).T; w = np.c_[1 - l.sum(1), l]; ok = (w >= -1e-6).all(1)
                    if not ok.any(): continue
                    w = w[ok]; X = xx.ravel()[ok]; Y = yy.ravel()[ok]; hgt = w @ pos[tr, 1]
                    front = hgt >= zb[Y, X] - 0.05
                    if not front.any(): continue
                    w, X, Y, hgt = w[front], X[front], Y[front], hgt[front]
                    g = sample(V, w @ uvM[tr])[:, 1:2] if V is not None else np.ones((len(w), 1))
                    if nm.startswith('road'):
                        col = sample(Bt, w @ uvB[tr]) * g
                    else:
                        col = sample(A, w @ uvA[tr]) * tint * (1 - g) + sample(Bt, w @ uvB[tr]) * g
                    img[Y, X] = col * (w @ shade[tr])[:, None] * 255; zb[Y, X] = hgt
    Image.fromarray(np.clip(img, 0, 255).astype('u1')).save(png); print('->', png)


if __name__ == '__main__':
    main(*sys.argv[1:])
