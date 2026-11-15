"""COLLADA shape writer and reader shared by the converters.

What it does, and why (FH1 measured most of it; fh1/docs/beamng-output.md, Shapes and Collision):
  * Welds vertices that would write the same text. The importer keeps every listed vertex: welding took FH1's
    cooked shapes from 2.80 GB to 1.91 GB.
  * Writes the stock node tree, base00 > start01 > o<i>_a<size>, one node per detail level. A trailing number on a
    node name is its detail size: the shape draws while it covers at least that many pixels.
  * Puts every colliding triangle in ONE mesh, Colmesh_0-1, with a <triangles> per material so each keeps its
    groundType. BeamNG's vehicle physics silently drops some of a shape's collision meshes when it has several
    (FH1: about 1.3% of them, the same ones every load, and cars fell through), and the same pieces as one mesh
    collide. Positions only: a cooked mesh keeps whatever vertex data it's given and collision reads none of it.
    A shape written with collision wants the TSStatic's collisionType "Collision Mesh".
  * Geometry can collide without being drawn (hidden physics copies), and be drawn without colliding.
"""
import io
import xml.etree.ElementTree as ET

import numpy as np

NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
COLMESH_MAX_VERTS = 60000       # FH1's largest working Colmesh is about 30k; Torque meshes once had 16-bit indices


def fmt(arr, f):
    """rows of floats as one space-separated line (fast-ish)"""
    buf = io.StringIO()
    np.savetxt(buf, arr, fmt=f, delimiter=" ", newline=" ")
    return buf.getvalue()


def weld(pos, nrm, uv, uv1, idx):
    """merge vertices that write the same text. Rounding matches the formats write_lod uses"""
    key = np.rint(np.c_[pos * 1e4, nrm * 1e3, uv * 1e5, uv1 * 1e5]).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return pos[first], nrm[first], uv[first], uv1[first], inv.ravel()[idx]


def weld_positions(pos, idx):
    """weld for collision, which needs no normals or UVs, so seams in those don't keep vertices apart"""
    _, first, inv = np.unique(np.rint(pos * 1e4).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    return pos[first], inv.ravel()[idx]


BOX_TRIS = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6], [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7], [0, 4, 2],
                     [2, 4, 6], [1, 3, 5], [3, 7, 5]])


def box_mesh(boxes):
    """(lo, hi) boxes to positions and triangles wound outward, as BeamNG collision is one-sided. Corner i has x from
    bit 0, y bit 1, z bit 2"""
    P, T = [], []
    for lo, hi in boxes:
        T.append(BOX_TRIS + len(P) * 8)
        P.append(np.array([[(lo, hi)[i & 1][0], (lo, hi)[i >> 1 & 1][1], (lo, hi)[i >> 2][2]] for i in range(8)]))
    return np.concatenate(P), np.concatenate(T).ravel()


def _levels(collide):
    if collide is None:
        return frozenset()
    return frozenset([collide]) if isinstance(collide, (int, str)) else frozenset(collide)


def has_col(groups, collide, colmesh=None):
    """whether write_lod will give these groups a collision mesh"""
    if colmesh is not None:
        return collide is not None and len(colmesh[1]) > 0
    c = _levels(collide)
    return any(c & set(g[0][1]) for g in groups)


def write_lod(path, groups, origin, sizes, collide, extra=(), colmesh=None, skip_mid=False, uv1_copy=False,
              tool=None):
    """Write a shape. Returns whether it has a collision mesh.

    groups: ((material, levels), pos, normal, uv, idx[, uv1]). Positions are world space; origin is subtracted.
      levels: the detail levels the group draws at, indices into `sizes`. A level outside `sizes` draws nothing,
      so a group can exist only to collide.
      uv1: a second UV set; a third column is a grey vertex colour that vertColor materials multiply by. Without it
      only one UV set is written, or a copy of the first with uv1_copy.
      UVs are DirectX-style (v down) and flipped for COLLADA.
    sizes: detail size per level, largest first. (1,) is one level that draws until the shape is under a pixel.
    collide: a level or set of levels whose geometry goes into the one Colmesh_0-1, or None.
    colmesh: (vertices, triangle indices) that collide instead, such as boxes from box_mesh: never drawn.
    skip_mid: a three-detail shape drops its middle level (LOD0 draws out to the LOD1 distance), as each detail
      node is its own cooked copy.
    extra: names of empty nodes to add, such as bb_autobillboard25."""
    coll_lv = _levels(collide)
    mats = sorted({g[0][0] for g in groups})
    out = []
    w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
    w((f'<asset><contributor><authoring_tool>{tool}</authoring_tool></contributor>' if tool else '<asset>')
      + '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>\n<library_effects>\n')
    for m in mats:
        w(f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse><color>0.8 0.8 0.8 1</color>'
          f'</diffuse></lambert></technique></profile_COMMON></effect>\n')
    w('</library_effects>\n<library_materials>\n')
    for m in mats:
        w(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>\n')
    w('</library_materials>\n<library_geometries>\n')
    nodes, coll = [], []
    skip = skip_mid and len(sizes) == 3
    sz = (sizes[1], sizes[1], sizes[2]) if skip else sizes
    ident = '<matrix sid="transform">1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1</matrix>'

    def geometry(g, local, nrm, uv, uv1, tris):
        """one <geometry>, with a <triangles> for each (material, indices) in tris -> its instance_geometry.
        nrm None writes positions only; uv1 None writes one UV set"""
        n = len(local)
        w(f'<geometry id="{g}" name="{g}"><mesh>\n')
        srcs = [(f'{g}-p', local, '%.4f', 'XYZ')]
        if nrm is not None:
            srcs += [(f'{g}-n', nrm, '%.3f', 'XYZ'), (f'{g}-t', np.c_[uv[:, 0], 1 - uv[:, 1]], '%.5f', 'ST')]
            if uv1 is not None:
                srcs.append((f'{g}-u', np.c_[uv1[:, 0], 1 - uv1[:, 1]], '%.5f', 'ST'))
                if uv1.shape[1] > 2:
                    srcs.append((f'{g}-c', np.repeat(np.clip(uv1[:, 2:3], 0, 1), 3, 1), '%.3f', 'RGB'))
        for sid, arr, f, params in srcs:
            w(f'<source id="{sid}"><float_array id="{sid}-a" count="{arr.size}">')
            w(fmt(arr, f))
            w(f'</float_array><technique_common><accessor source="#{sid}-a" count="{n}" stride="{len(params)}">')
            w(''.join(f'<param name="{c}" type="float"/>' for c in params))
            w('</accessor></technique_common></source>\n')
        w(f'<vertices id="{g}-v"><input semantic="POSITION" source="#{g}-p"/></vertices>\n')
        rest = ''
        if nrm is not None:
            rest = (f'<input semantic="NORMAL" source="#{g}-n" offset="0"/>'
                    f'<input semantic="TEXCOORD" source="#{g}-t" offset="0" set="0"/>')
            if uv1 is not None:
                rest += f'<input semantic="TEXCOORD" source="#{g}-u" offset="0" set="1"/>'
                if uv1.shape[1] > 2:
                    rest += f'<input semantic="COLOR" source="#{g}-c" offset="0" set="0"/>'
        for mat, idx in tris:
            w(f'<triangles material="{mat}" count="{len(idx) // 3}"><input semantic="VERTEX" source="#{g}-v" offset="0"/>{rest}<p>')
            w(' '.join(map(str, np.asarray(idx).tolist())))
            w('</p></triangles>\n')
        w('</mesh></geometry>\n')
        bind = ''.join(f'<instance_material symbol="{m}" target="#{m}"/>' for m in dict.fromkeys(m for m, _ in tris))
        return (f'<instance_geometry url="#{g}"><bind_material><technique_common>{bind}</technique_common>'
                f'</bind_material></instance_geometry>')

    for gi, g_ in enumerate(groups):
        (mat, levels), pos, nrm, uv, idx = g_[:5]
        uv1 = g_[5] if len(g_) > 5 and g_[5] is not None else (uv if uv1_copy else None)
        if uv1 is None:
            local, nrm, uv, _, idx = weld(pos - origin, nrm, uv, uv, idx)
        else:
            local, nrm, uv, uv1, idx = weld(pos - origin, nrm, uv, uv1, idx)
        if colmesh is None and coll_lv & set(levels):
            coll.append((mat, local, idx))
        draws = [lv for lv in levels if isinstance(lv, int) and lv < len(sizes) and not (skip and lv == 1)]
        if draws:
            inst = geometry(f'g{gi}', local, nrm, uv, uv1, [(mat, idx)])
            for lv in draws:
                nm_ = f'o{gi}_a{sz[lv]}'
                nodes.append(f'<node id="{nm_}" name="{nm_}" type="NODE">{ident}{inst}</node>\n')
    if colmesh is not None and collide is not None and len(colmesh[1]):
        bp, bi = colmesh
        coll.append((groups[0][0][0], *weld_positions(np.asarray(bp, np.float64) - origin, np.asarray(bi))))
    col = bool(coll)
    if col:     # all of it in one mesh, a <triangles> per material
        at = np.cumsum([0] + [len(c[1]) for c in coll])
        by = {}
        for c, o_ in zip(coll, at):
            by.setdefault(c[0], []).append(np.asarray(c[2]) + o_)
        cp, ci = np.concatenate([c[1] for c in coll]), {m: np.concatenate(v) for m, v in by.items()}
        cp, cat_ = weld_positions(cp, np.concatenate(list(ci.values())))
        bounds = np.cumsum([0] + [len(v) for v in ci.values()])
        inst = geometry('gcol', cp, None, None, None, [(m, cat_[a:b]) for m, a, b in zip(ci, bounds[:-1], bounds[1:])])
        nodes.append(f'<node id="Colmesh_0-1" name="Colmesh_0-1" type="NODE">{ident}{inst}</node>\n')
    w('</library_geometries>\n<library_visual_scenes><visual_scene id="Scene" name="Scene">\n')
    w(f'<node id="base00" name="base00" type="NODE">{ident}<node id="start01" name="start01" type="NODE">{ident}\n')
    out.extend(nodes)
    w('</node>\n')
    for e_ in (list(extra) + (['collision-1'] if col else [])):
        w(f'<node id="{e_}" name="{e_}" type="NODE">{ident}</node>\n')
    w('</node></visual_scene></library_visual_scenes>\n<scene><instance_visual_scene url="#Scene"/></scene>\n</COLLADA>\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(out), encoding='utf-8')
    return col


# ---- reading shapes back --------------------------------------------------------------------------------------

def read_geometries(path):
    """[(geometry id, positions (N,3) shape-local, [(material, triangle indices (M,3))])] of a .dae, every
    <triangles> of every geometry. Geometries without a '<id>-p' position source (other writers) are skipped."""
    root = ET.parse(path).getroot()
    out = []
    for g in root.iterfind(".//c:geometry", NS):
        fa = g.find(f".//c:source[@id='{g.get('id')}-p']/c:float_array", NS)
        if fa is None:
            continue
        p = np.array(fa.text.split(), np.float64).reshape(-1, 3)
        tris = [(t.get("material"), np.array(t.find("c:p", NS).text.split(), np.int64).reshape(-1, 3))
                for t in g.iterfind(".//c:triangles", NS)]
        out.append((g.get("id"), p, tris))
    return out


def collision_tris(path):
    """[(material, triangles (M,3,3) shape-local)] the shape collides with: its Colmesh when it has one, else (a
    shape from before kn5port wrote Colmeshes, collided as "Visible Mesh Final") every geometry"""
    geos = read_geometries(path)
    col = [g for g in geos if g[0] == "gcol"]
    return [(m, p[i]) for _, p, tris in (col or geos) for m, i in tris]
