"""Shape writer. It has its own module so that worker processes can load it without running build3.py."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port.level import _fmt

FARCUT = 3


def has_col(groups, collide, colmesh=None):
    """whether write_lod will give these groups a collision mesh"""
    if colmesh is not None:
        return collide is not None and len(colmesh[1]) > 0
    return any(collide is not None and collide in g[0][1] or collide == 2 and FARCUT in g[0][1] for g in groups)


def weld(pos, nrm, uv, uv1, idx):
    """merge vertices that write the same text. The importer keeps every listed vertex, and clip_far leaves separate triangles.
    Rounding matches the formats in write_lod"""
    key = np.rint(np.c_[pos * 1e4, nrm * 1e3, uv * 1e5, uv1 * 1e5]).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return pos[first], nrm[first], uv[first], uv1[first], inv.ravel()[idx]


BOX_TRIS = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])


def box_mesh(boxes):
    """(lo, hi) boxes to positions and triangles wound outward, as BeamNG collision is one-sided. Corner i has x from bit 0, y bit 1, z bit 2"""
    P = []; T = []
    for lo, hi in boxes:
        T.append(BOX_TRIS + len(P) * 8); P.append(np.array([[(lo, hi)[i & 1][0], (lo, hi)[i >> 1 & 1][1], (lo, hi)[i >> 2][2]] for i in range(8)]))
    return np.concatenate(P), np.concatenate(T).ravel()


def write_lod(path, groups, origin, sizes, collide, extra=(), colmesh=None):
    """base00 > start01 > o<i>_a<size> per LOD, as stock shapes. groups: ((material, levels), pos, normal, uv, idx, uv2).
    A trailing number on those node names is the detail size. Geometry of level `collide` is copied into one Colmesh_0-1: BeamNG's vehicle
    physics drops some of a shape's collision meshes when it has several (c21029_col lost one of its eight, the same one every load), and the
    same pieces as one mesh collide.
    Level FARCUT draws nothing: it keeps a mesh for collision after its LOD2 copy is dropped.
    colmesh: (vertices, triangle indices) that collide instead of that geometry, such as colfit.py's boxes, as one more Colmesh that's never drawn"""
    if colmesh is not None and collide is not None:
        bp, bi = colmesh
        groups = list(groups) + [((groups[0][0][0], ('box',)), bp, np.zeros_like(bp), np.zeros((len(bp), 2)), bi)]
        collide = 'box'
    mats = sorted({g[0][0] for g in groups}); out = []; w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
    w('<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>\n<library_effects>\n')
    for m in mats:
        w(f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>0.8 0.8 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>\n')
    w('</library_effects>\n<library_materials>\n')
    for m in mats:
        w(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>\n')
    w('</library_materials>\n<library_geometries>\n'); nodes = []; coll = []
    ident = '<matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>'

    def geometry(g, local, nrm, uv, uv1, tris):
        """one <geometry>, with a <triangles> for each (material, indices) in tris -> its instance_geometry"""
        n = len(local); w(f'<geometry id="{g}" name="{g}"><mesh>\n')
        for sid, arr, fmt, params in ((f'{g}-p', local, '%.4f', 'XYZ'), (f'{g}-n', nrm, '%.3f', 'XYZ'),
                                      (f'{g}-t', np.c_[uv[:, 0], 1 - uv[:, 1]], '%.5f', 'ST'), (f'{g}-u', np.c_[uv1[:, 0], 1 - uv1[:, 1]], '%.5f', 'ST')):
            w(f'<source id="{sid}"><float_array id="{sid}-a" count="{arr.size}">'); w(_fmt(arr, fmt))
            w(f'</float_array><technique_common><accessor source="#{sid}-a" count="{n}" stride="{len(params)}">')
            w(''.join(f'<param name="{c}" type="float"/>' for c in params)); w('</accessor></technique_common></source>\n')
        w(f'<vertices id="{g}-v"><input semantic="POSITION" source="#{g}-p"/></vertices>\n')
        for mat, idx in tris:
            w(f'<triangles material="{mat}" count="{len(idx) // 3}"><input semantic="VERTEX" source="#{g}-v" offset="0"/><input semantic="NORMAL" source="#{g}-n" offset="0"/>'
              f'<input semantic="TEXCOORD" source="#{g}-t" offset="0" set="0"/><input semantic="TEXCOORD" source="#{g}-u" offset="0" set="1"/><p>')
            w(' '.join(map(str, np.asarray(idx).tolist()))); w('</p></triangles>\n')
        w('</mesh></geometry>\n')
        bind = ''.join(f'<instance_material symbol="{m}" target="#{m}"/>' for m in dict.fromkeys(m for m, _ in tris))
        return f'<instance_geometry url="#{g}"><bind_material><technique_common>{bind}</technique_common></bind_material></instance_geometry>'

    for gi, g_ in enumerate(groups):
        (mat, levels), pos, nrm, uv, idx = g_[:5]; uv1 = g_[5] if len(g_) > 5 and g_[5] is not None else uv
        local, nrm, uv, uv1, idx = weld(pos - origin, nrm, uv, uv1, idx)
        if collide is not None and collide in levels or collide == 2 and FARCUT in levels:
            coll.append((mat, local, nrm, uv, uv1, idx))
        draws = [lv for lv in levels if lv != 'box' and lv < len(sizes)]
        if draws:
            inst = geometry(f'g{gi}', local, nrm, uv, uv1, [(mat, idx)])
            for lv in draws:
                nm_ = f'o{gi}_a{sizes[lv]}'; nodes.append(f'<node id="{nm_}" name="{nm_}" type="NODE">{ident}{inst}</node>\n')
    col = bool(coll)
    if col:     # all of it in one mesh, a <triangles> per material so each keeps its groundType
        at = np.cumsum([0] + [len(c[1]) for c in coll]); by = {}
        for c, o_ in zip(coll, at):
            by.setdefault(c[0], []).append(np.asarray(c[5]) + o_)
        inst = geometry('gcol', *(np.concatenate([c[k] for c in coll]) for k in range(1, 5)), [(m, np.concatenate(v)) for m, v in by.items()])
        nodes.append(f'<node id="Colmesh_0-1" name="Colmesh_0-1" type="NODE">{ident}{inst}</node>\n')
    w('</library_geometries>\n<library_visual_scenes><visual_scene id="Scene" name="Scene">\n')
    w(f'<node id="base00" name="base00" type="NODE">{ident}<node id="start01" name="start01" type="NODE">{ident}\n'); out.extend(nodes); w('</node>\n')
    for e_ in (list(extra) + (['collision-1'] if col else [])):
        w(f'<node id="{e_}" name="{e_}" type="NODE">{ident}</node>\n')
    w('</node></visual_scene></library_visual_scenes>\n<scene><instance_visual_scene url="#Scene"/></scene>\n</COLLADA>\n')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(''.join(out), encoding='utf-8')
    return col
