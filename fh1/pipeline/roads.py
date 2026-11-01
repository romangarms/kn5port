"""Road raster at 10 m per pixel, used to fit the developer map art to world coordinates."""
import pickle, re
import numpy as np, rmb
x0, z0, S, W, H = -9000, -7000, 10, 1800, 1500
R = np.zeros((H, W), 'f4')
for c in pickle.load(open('../cat2.pkl', 'rb')):
    if not re.search('road', c['name'], re.I) or re.search('LOD0[12]|LOD[12]', c['name']) or c['sd'] < 20:
        continue
    m = rmb.parse(open('../x/' + c['f'], 'rb').read())['meshes'][c['mi']]; p, _, _ = rmb.verts(m)
    i = ((p[:, 0] - x0) / S).astype(int); j = ((p[:, 2] - z0) / S).astype(int); k = (i >= 0) & (i < W) & (j >= 0) & (j < H)
    R[j[k], i[k]] = 1
np.save('../roads10.npy', R)
