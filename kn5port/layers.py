"""Sharp ground for AC's multilayer shader: split each ground mesh by the layer that dominates under each triangle.

AC's ksMultilayer* shaders blend four tiling detail textures (txDetailR/G/B/A: grass, dirt, rock...) by a mask
(txMask) and multiplies them over a large macro map (txDiffuse) that carries the ground's colour. A BeamNG mesh
material has one colour map and one detail map. Here every triangle takes the layer with the highest mask weight at
its centre: the macro map stays the colour (AC's own colours, blending softly across the hillside) and that layer's
texture becomes a neutral-grey detail map tiled at AC's own scale, so grass, dirt and rock are sharp up close. Using
the layer texture itself as the colour was tried first: the layers are much darker than the macro map (this track's
grass averages 0.23 against a 0.48 macro), which turned hillsides near-black with hard-edged blotches.
"""
import numpy as np

LAYERS = "RGBA"


def tile_scale(mult, m_per_uv):
    """Detail repeats per mesh UV unit (see level.detail_scale): mult >= 1 tiles per UV unit (Kunos), mult < 1 is
    CSP's object-space mode, one tile every 1/mult metres."""
    if not mult or mult <= 0:
        return None
    if mult >= 1 or not m_per_uv:
        return float(mult)
    return float(m_per_uv * mult)


def mesh_m_per_uv(pos, uvs, idx):
    t = idx.reshape(-1, 3)
    a3 = np.linalg.norm(np.cross(pos[t[:, 1]] - pos[t[:, 0]], pos[t[:, 2]] - pos[t[:, 0]]), axis=1).sum()
    e1, e2 = uvs[t[:, 1]] - uvs[t[:, 0]], uvs[t[:, 2]] - uvs[t[:, 0]]
    auv = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum()
    return float(np.sqrt(a3 / auv)) if auv > 0 else None


class LayerSplitter:
    def __init__(self, baker):
        self.baker = baker
        self._masks = {}

    def _mask(self, k, acmat):
        """Mask as (H, W, 4) float weights, A dropped when the mask has no real alpha."""
        name = acmat.samplers.get("txMask", "")
        if name not in self._masks:
            im = self.baker._img(k, name)
            if im is None:
                self._masks[name] = None
            else:
                if max(im.size) > 2048:
                    sc = 2048 / max(im.size)
                    im = im.resize((max(1, int(im.size[0] * sc)), max(1, int(im.size[1] * sc))))
                m = np.asarray(im, dtype=np.float32) / 255.0
                if np.ptp(m[..., 3]) < 0.02:           # no alpha in the file: A isn't a layer
                    m = m.copy(); m[..., 3] = 0
                self._masks[name] = m
        return self._masks[name]

    def layers_of(self, acmat):
        """Layers that have a texture and a tiling."""
        s = acmat.samplers
        return [c for c in LAYERS if f"txDetail{c}" in s and acmat.props.get(f"mult{c}", (0,))[0] > 0]

    def split(self, k, acmat, pos, uvs, idx):
        """Per-triangle layer index (into LAYERS, -1 = none) and per-layer UV repeats, or None if not splittable."""
        layers = self.layers_of(acmat)
        mask = self._mask(k, acmat) if layers else None
        if mask is None:
            return None
        t = idx.reshape(-1, 3)
        uv = uvs[t].mean(1)
        h, w = mask.shape[:2]
        x = np.clip((uv[:, 0] % 1.0) * w, 0, w - 1).astype(np.int64)
        y = np.clip((uv[:, 1] % 1.0) * h, 0, h - 1).astype(np.int64)
        wts = mask[y, x].copy()
        for i, c in enumerate(LAYERS):
            if c not in layers:
                wts[:, i] = -1
        lay = np.argmax(wts, 1)
        lay[wts.max(1) <= 0] = -1
        mpu = mesh_m_per_uv(pos, uvs, idx)
        scales = {c: tile_scale(acmat.props[f"mult{c}"][0], mpu) for c in layers}
        return lay, scales


def pieces(part, split):
    """Split one (pos, nrm, uv, idx) part into per-layer parts (UVs unchanged: the macro colour map uses them).
    Yields (layer letter or None, (pos, nrm, uv, idx), the layer's detail repeats per UV unit)."""
    pos, nrm, uv, idx = part[:4]
    lay, scales = split
    t = idx.reshape(-1, 3)
    for li in np.unique(lay):
        sel = t[lay == li]
        c = LAYERS[li] if li >= 0 else None
        sc = scales.get(c) if c else None
        if c is None or not sc:
            yield None, (pos, nrm, uv, sel.reshape(-1)), None
            continue
        used, inv = np.unique(sel.reshape(-1), return_inverse=True)
        yield c, (pos[used], nrm[used], uv[used], inv.astype(np.uint32)), round(float(sc), 3)


# AC multiplies a layer over the macro map, so a layer's relative variation (std / mean, ~0.35 for grass) shows up
# around the macro colour (~0.5): roughly twice the layer's own absolute contrast
LAYER_DETAIL_CONTRAST = 1.6


def layer_material(name, acmat, c, k, baker, tex_rel, uv_scale, ground_type=None):
    """The macro map as base colour on the mesh's own UVs, the layer's tiling texture as the detail map."""
    s = acmat.samplers
    stage = {"roughnessFactor": 0.95, "metallicFactor": 0.0, "useAnisotropic": True}
    base = baker.opaque(k, s["txDiffuse"]) if "txDiffuse" in s else None
    stage["baseColorMap"] = base or baker.opaque(k, s[f"txDetail{c}"]) or f"{tex_rel}/{s[f'txDetail{c}']}"
    dm = baker.detail(k, s[f"txDetail{c}"], LAYER_DETAIL_CONTRAST) if base else None
    if dm:
        stage["detailMap"] = dm
        stage["detailScale"] = [round(float(uv_scale), 3)] * 2
    mat = {"name": name, "mapTo": name, "class": "Material", "version": 1.5,
           "Stages": [stage, {}, {}, {}], "materialTag0": "beamng", "materialTag1": "kn5_port",
           "ac_shader": acmat.shader, "ac_material": acmat.name, "ac_layer": c,
           "ac_tile_repeats_per_uv": round(float(uv_scale), 4)}
    if ground_type:
        mat["groundType"] = ground_type
    return mat
