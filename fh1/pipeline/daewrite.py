"""Shape writer. It has its own module so that worker processes can load it without running build3.py."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath('../..'))
from kn5port.level import _fmt

FARCUT = 3


def has_col(groups, collide):
    """whether write_lod will give these groups a collision mesh"""
    return any(collide is not None and collide in g[0][1] or collide == 2 and FARCUT in g[0][1] for g in groups)


def weld(pos, nrm, uv, uv1, idx):
    """merge vertices that write the same text. The importer keeps every listed vertex, and clip_far leaves separate triangles.
    Rounding matches the formats in write_lod"""
    key = np.rint(np.c_[pos * 1e4, nrm * 1e3, uv * 1e5, uv1 * 1e5]).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return pos[first], nrm[first], uv[first], uv1[first], inv.ravel()[idx]


def write_lod(path, groups, origin, sizes, collide, extra=()):
    """base00 > start01 > o<i>_a<size> per LOD, as stock shapes. groups: ((material, levels), pos, normal, uv, idx, uv2).
    A trailing number on those node names is the detail size. Geometry of level `collide` is reused as Colmesh_<i>-1.
    Level FARCUT draws nothing: it keeps a mesh for collision after its LOD2 copy is dropped."""
    mats = sorted({g[0][0] for g in groups}); out = []; w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
    w('<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>\n<library_effects>\n')
    for m in mats:
        w(f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>0.8 0.8 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>\n')
    w('</library_effects>\n<library_materials>\n')
    for m in mats:
        w(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>\n')
    w('</library_materials>\n<library_geometries>\n'); nodes = []; col = False
    ident = '<matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>'
    for gi, g_ in enumerate(groups):
        (mat, levels), pos, nrm, uv, idx = g_[:5]; uv1 = g_[5] if len(g_) > 5 and g_[5] is not None else uv
        local, nrm, uv, uv1, idx = weld(pos - origin, nrm, uv, uv1, idx)
        g = f'g{gi}'; n = len(local)
        w(f'<geometry id="{g}" name="{g}"><mesh>\n')
        for sid, arr, fmt, params in ((f'{g}-p', local, '%.4f', 'XYZ'), (f'{g}-n', nrm, '%.3f', 'XYZ'),
                                      (f'{g}-t', np.c_[uv[:, 0], 1 - uv[:, 1]], '%.5f', 'ST'), (f'{g}-u', np.c_[uv1[:, 0], 1 - uv1[:, 1]], '%.5f', 'ST')):
            w(f'<source id="{sid}"><float_array id="{sid}-a" count="{arr.size}">'); w(_fmt(arr, fmt))
            w(f'</float_array><technique_common><accessor source="#{sid}-a" count="{n}" stride="{len(params)}">')
            w(''.join(f'<param name="{c}" type="float"/>' for c in params)); w('</accessor></technique_common></source>\n')
        w(f'<vertices id="{g}-v"><input semantic="POSITION" source="#{g}-p"/></vertices>\n')
        w(f'<triangles material="{mat}" count="{len(idx) // 3}"><input semantic="VERTEX" source="#{g}-v" offset="0"/><input semantic="NORMAL" source="#{g}-n" offset="0"/>'
          f'<input semantic="TEXCOORD" source="#{g}-t" offset="0" set="0"/><input semantic="TEXCOORD" source="#{g}-u" offset="0" set="1"/><p>')
        w(' '.join(map(str, idx.tolist()))); w('</p></triangles>\n</mesh></geometry>\n')
        inst = f'<instance_geometry url="#{g}"><bind_material><technique_common><instance_material symbol="{mat}" target="#{mat}"/></technique_common></bind_material></instance_geometry>'
        for lv in levels:
            if lv < len(sizes):
                nm_ = f'o{gi}_a{sizes[lv]}'; nodes.append(f'<node id="{nm_}" name="{nm_}" type="NODE">{ident}{inst}</node>\n')
        if collide is not None and collide in levels or collide == 2 and FARCUT in levels:
            col = True; nm_ = f'Colmesh_{gi}-1'; nodes.append(f'<node id="{nm_}" name="{nm_}" type="NODE">{ident}{inst}</node>\n')
    w('</library_geometries>\n<library_visual_scenes><visual_scene id="Scene" name="Scene">\n')
    w(f'<node id="base00" name="base00" type="NODE">{ident}<node id="start01" name="start01" type="NODE">{ident}\n'); out.extend(nodes); w('</node>\n')
    for e_ in (list(extra) + (['collision-1'] if col else [])):
        w(f'<node id="{e_}" name="{e_}" type="NODE">{ident}</node>\n')
    w('</node></visual_scene></library_visual_scenes>\n<scene><instance_visual_scene url="#Scene"/></scene>\n</COLLADA>\n')
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(''.join(out), encoding='utf-8')
    return col
