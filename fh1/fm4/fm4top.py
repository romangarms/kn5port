"""Top-down render of every mesh an FM4 ribbon places, to check placements.
python fm4top.py tsukuba "<FM4>/Media/Tracks/Tsukuba/Ribbon_00/tsukuba_00.pvs" out.png"""
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4mesh
import fm4pvs
import fm4rmb


def main(work, pvs, png):
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    t = fm4pvs.tables(open(pvs, 'rb').read()); F = fm4mesh.fetch_lists(work)
    stem = os.path.basename(glob.glob(work + '/x/*out.00000.rmb.bin')[0]).split('.')[0]
    P = np.array([d['pos'] for d in t['draws']]); cache = {}; polys = []; cols = []
    for d in t['draws']:
        fn = f"{work}/x/{stem}.{d['mesh']:05d}.rmb.bin"
        if fn not in cache:
            b = open(fn, 'rb').read(); p = fm4rmb.parse(b); cache[fn] = (p, fm4rmb.file_shaders(b, p))
        p, shs = cache[fn]
        for m in p['meshes']:
            if any(f'LOD0{k}' in m['name'] for k in (1, 2, 3)) or m['name'].startswith('SKY'): continue
            w = fm4mesh.attr(m, {'position0': 0}, 'position0') @ d['rot'] + d['pos']
            if np.ptp(w[:, 0]) > 1500 or np.ptp(w[:, 2]) > 1500: continue      # far backdrop
            lay = fm4mesh.mesh_layout(m, shs, F)
            nor = fm4mesh.attr(m, lay, 'normal0') @ d['rot'] if lay and 'normal0' in lay else None
            for s in m['secs']:
                tr = fm4rmb.tris(s['ib'], m['nv'], s['ints'][1])
                if not len(tr): continue
                polys.extend(w[tr][:, :, [0, 2]])
                g = 0.25 + 0.6 * np.abs(nor[tr].mean(1)[:, 1]) if nor is not None else np.full(len(tr), 0.5)
                cols.extend(np.stack([g * 0.9, g, g * 0.85], 1).clip(0, 1))
    fig, ax = plt.subplots(figsize=(16, 16), dpi=100)
    ax.add_collection(PolyCollection(polys, facecolors=cols, edgecolors='none'))
    ax.set_xlim(P[:, 0].min() - 50, P[:, 0].max() + 50); ax.set_ylim(P[:, 2].max() + 50, P[:, 2].min() - 50); ax.set_aspect('equal')
    fig.savefig(png, bbox_inches='tight'); print(len(t['draws']), 'draws', len(polys), 'triangles ->', png)


if __name__ == '__main__':
    main(*sys.argv[1:4])
