"""Convert an Assetto Corsa track (unencrypted .kn5 files) into a BeamNG.drive level skeleton.

    python kn5port convert "<AC track folder>" "<output mod folder>" [options]

Example (Windows):
    python kn5port convert "D:\\Steam Library\\steamapps\\common\\assettocorsa\\content\\tracks\\skyline_boulevard" ^
        "%LOCALAPPDATA%\\BeamNG\\BeamNG.drive\\current\\mods\\unpacked\\skyline_boulevard"

What it produces (inside <output>/levels/<level>/):
    info.json                      level metadata + spawn points
    main/...items.level.json       scene: sky, time of day, spawns, one TSStatic per grid cell
    art/shapes/<kn5>/*.dae         geometry, split into grid cells; *_col.dae carry collision
    art/textures/*.dds             textures copied straight out of the kn5 files
    art/shapes/<level>.materials.json   BeamNG (v1.5) materials with ground types

Geometry handling:
  * AC axes are X=east, Y=up, Z=south. BeamNG is Z-up, so (x, y, z) -> (x, -z, y).
  * Everything is shifted so the road's centre sits at the origin (keeps float precision sane).
  * Meshes are merged per material inside each grid cell, so BeamNG can cull cells.
  * AC physics meshes (names like 1ROAD_xxx, 1GRASS_xxx) go into *_col.dae shapes that
    BeamNG collides with; everything else is visual only (no collision).
  * Only the highest LOD of each AC mesh is kept.

This is a starting point, not a finished level: AC's shaders (multilayer terrain, CSP GrassFX,
reflections) don't exist in BeamNG, so materials are simplified to base colour + normal (+ detail).
"""
import argparse
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .kn5 import KN5, KN5Error, Dummy, Mesh
from .overlay import OverlayLifter, STEP as LAYER_STEP
from .profile import Profile, read_surfaces, mostly_flat, phys_key, material_uses, is_natural_ground

MAX_VERTS_PER_CHUNK = 65000

# ---------------------------------------------------------------------------------------------------

PHYS_RE = re.compile(r"^(\d+)([A-Z]+)")


def ac_to_bng(p):
    """(N,3) AC (x east, y up, z south) -> BeamNG (x east, y north, z up)."""
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1)


def safe(name):
    s = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return s or "unnamed"


def _unused_read_surface_keys(track_dir):
    keys = set()
    for ini in Path(track_dir).rglob("surfaces.ini"):
        for line in ini.read_text(errors="replace").splitlines():
            m = re.match(r"\s*KEY\s*=\s*(\w+)", line)
            if m:
                keys.add(m.group(1).upper())
    return keys


def models_from_ini(track_dir):
    """kn5 files listed by the layouts' models_*.ini (union), else every kn5 in the folder."""
    names = []
    for ini in sorted(Path(track_dir).glob("models*.ini")):
        for line in ini.read_text(errors="replace").splitlines():
            m = re.match(r"\s*FILE\s*=\s*(.+\.kn5)", line, re.I)
            if m and m.group(1).strip() not in names:
                names.append(m.group(1).strip())
    if not names:
        names = sorted(p.name for p in Path(track_dir).glob("*.kn5"))
    return names


# ---- Collada writer -------------------------------------------------------------------------------

def _fmt(arr, fmt):
    # fast-ish float formatting: one row per vertex
    import io
    buf = io.StringIO()
    np.savetxt(buf, arr, fmt=fmt, delimiter=" ", newline=" ")
    return buf.getvalue()


def write_dae(path, groups, origin):
    """groups: list of (material_name, positions(N,3) BeamNG world, normals(N,3), uvs(N,2), idx(M,))."""
    mats = sorted({g[0] for g in groups})
    out = []
    w = out.append
    w('<?xml version="1.0" encoding="utf-8"?>\n')
    w('<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">\n')
    w('<asset><contributor><authoring_tool>kn5port convert</authoring_tool></contributor>'
      '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>\n')
    w('<library_effects>\n')
    for m in mats:
        w(f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse>'
          f'<color>0.8 0.8 0.8 1</color></diffuse></lambert></technique></profile_COMMON></effect>\n')
    w('</library_effects>\n<library_materials>\n')
    for m in mats:
        w(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>\n')
    w('</library_materials>\n<library_geometries>\n')
    nodes = []
    for gi, (mat, pos, nrm, uv, idx) in enumerate(groups):
        g = f"g{gi}"
        n = len(pos)
        local = pos - origin
        uv2 = np.stack([uv[:, 0], 1.0 - uv[:, 1]], axis=1)   # DirectX (v down) -> Collada (v up)
        w(f'<geometry id="{g}" name="{g}"><mesh>\n')
        for sid, arr, fmt, params in ((f"{g}-p", local, "%.4f", "XYZ"),
                                      (f"{g}-n", nrm, "%.3f", "XYZ"),
                                      (f"{g}-t", uv2, "%.5f", "ST")):
            w(f'<source id="{sid}"><float_array id="{sid}-a" count="{arr.size}">')
            w(_fmt(arr, fmt))
            w(f'</float_array><technique_common><accessor source="#{sid}-a" count="{n}" stride="{len(params)}">')
            w("".join(f'<param name="{c}" type="float"/>' for c in params))
            w('</accessor></technique_common></source>\n')
        w(f'<vertices id="{g}-v"><input semantic="POSITION" source="#{g}-p"/></vertices>\n')
        w(f'<triangles material="{mat}" count="{len(idx) // 3}">'
          f'<input semantic="VERTEX" source="#{g}-v" offset="0"/>'
          f'<input semantic="NORMAL" source="#{g}-n" offset="0"/>'
          f'<input semantic="TEXCOORD" source="#{g}-t" offset="0" set="0"/><p>')
        w(" ".join(map(str, idx.tolist())))
        w('</p></triangles>\n</mesh></geometry>\n')
        # Torque reads a trailing number in a node name as a detail size, so end names with letters.
        nodes.append(f'<node id="n{gi}" name="{safe(mat)}_c{gi}_mesh"><instance_geometry url="#{g}">'
                     f'<bind_material><technique_common><instance_material symbol="{mat}" target="#{mat}"/>'
                     f'</technique_common></bind_material></instance_geometry></node>\n')
    w('</library_geometries>\n<library_visual_scenes><visual_scene id="Scene" name="Scene">\n')
    out.extend(nodes)
    w('</visual_scene></library_visual_scenes>\n<scene><instance_visual_scene url="#Scene"/></scene>\n</COLLADA>\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(out), encoding="utf-8")


def merge_chunks(parts):
    """parts: list of (pos, nrm, uv, idx). Yields merged chunks under MAX_VERTS_PER_CHUNK."""
    cur, cur_v = [], 0
    for p in parts:
        nv = len(p[0])
        if cur and cur_v + nv > MAX_VERTS_PER_CHUNK:
            yield _concat(cur); cur, cur_v = [], 0
        cur.append(p); cur_v += nv
    if cur:
        yield _concat(cur)


def _concat(parts):
    pos = np.concatenate([p[0] for p in parts]); nrm = np.concatenate([p[1] for p in parts])
    uv = np.concatenate([p[2] for p in parts])
    idx, base = [], 0
    for p in parts:
        idx.append(p[3] + base); base += len(p[0])
    return pos, nrm, uv, np.concatenate(idx)


# ---- materials ------------------------------------------------------------------------------------
#
# AC and BeamNG shade differently, so some textures are re-baked rather than copied:
#  * ksPerPixelMultiMap* : AC keeps a detail-blend mask in the diffuse alpha and pairs it with a bright
#    detail texture. BeamNG reads both differently (dark/purple roads), so we bake an opaque base colour.
#  * ksMultilayer*       : the "macro" diffuse is near-white; the real colour comes from four detail layers
#    blended by txMask. We bake mask-weighted layer colours x macro variation into one colour map.
#  * detail textures    : AC tiles a small detail texture over the low-res base (txDetail on MultiMap, the
#    multilayer layers). They become BeamNG detailMaps, baked to greyscale around mid grey.
#  * alpha-tested / translucent : BeamNG v1.5 materials take alpha from a separate opacity map.

PROFILE = Profile()          # replaced in main()
SURFACES = {}


def surface_of(mesh):
    """AC physics surface key of a mesh (from its 1ROAD_/1GRASS_-style name prefix), or None."""
    return phys_key(mesh.name, SURFACES)


def ground_type_of(key):
    return PROFILE.ground_type(key, SURFACES.get(key))


def is_horizon_kn5(stem):
    return bool(PROFILE.rx("horizon_re").search(stem))


USES = {}                # material -> ground types of its physics meshes, for the kn5 being converted


def is_ac_ground(mesh):
    """Natural ground the BeamNG terrain replaces (rules: profile.is_natural_ground)."""
    return is_natural_ground(mesh, SURFACES, PROFILE, USES)


def is_vegetation(mesh):
    """Trees, bushes and grass cards (replaced by BeamNG forest items). Never a physics mesh."""
    if surface_of(mesh):
        return False
    m = mesh.material
    if PROFILE.rx("trunk_re").search(m.name):
        return True
    if not PROFILE.rx("vegetation_re").search(m.name) and m.shader != "ksTree":
        return False
    return m.shader == "ksTree" or m.alpha_tested or m.alpha_blend > 0 or "AT" in m.shader.replace("MultiMap", "")


class TextureBaker:
    def __init__(self, tex_dir, tex_rel):
        self.tex_dir, self.tex_rel = tex_dir, tex_rel
        self.done = {}
        self._mean_cache = {}
        self.ml_weights = {}

    def _img(self, k, name):
        from PIL import Image
        import io
        t = next((t for t in k.textures if t.name == name), None)
        if t is None:
            return None
        try:
            im = Image.open(io.BytesIO(k.texture_bytes(t)))
            im.load()
            return im.convert("RGBA")
        except Exception as e:  # unsupported DDS format etc.
            print(f"    ! could not decode {name}: {e}")
            return None

    @staticmethod
    def pow2(v):
        """Nearest power of two (BeamNG imports only power-of-two textures, at least 16 px)."""
        return int(min(4096, max(16, 2 ** round(math.log2(max(v, 1))))))

    def _save(self, im, name):
        self.tex_dir.mkdir(parents=True, exist_ok=True)
        size = (self.pow2(im.size[0]), self.pow2(im.size[1]))
        if size != im.size:
            from PIL import Image
            im = im.resize(size, Image.LANCZOS)
        im.save(self.tex_dir / name, optimize=False)
        return f"{self.tex_rel}/{name}"

    def opaque(self, k, name):
        key = ("opaque", name)
        if key not in self.done:
            im = self._img(k, name)
            self.done[key] = self._save(im.convert("RGB"), Path(name).stem + "_rgb.color.png") if im else None
        return self.done[key]

    def rgba_png(self, k, name, kind="color"):
        # BeamNG picks the colour space from the suffix: .color. = sRGB, .data. = linear, .normal. = normal map
        key = ("rgba", name, kind)
        if key not in self.done:
            im = self._img(k, name)
            self.done[key] = self._save(im, f"{Path(name).stem}.{kind}.png") if im else None
        return self.done[key]

    DDS_OK = {b"DXT1", b"DXT3", b"DXT5", b"ATI1", b"ATI2", b"BC4U", b"BC4S", b"BC5U", b"BC5S", b"DX10"}

    def raw_ok(self, k, name):
        """Can BeamNG use this texture exactly as stored? It imports DDS (DXT/BC-compressed or plain RGB, not
        luminance/alpha-only formats), PNG and JPEG, always power-of-two sized and at least 16 px."""
        t = next((t for t in k.textures if t.name == name), None)
        if t is None:
            return False
        head = bytes(k._mm[t.offset:t.offset + min(t.size, 128)])
        good = lambda v: v >= 16 and (v & (v - 1)) == 0
        if head[:4] == b"DDS ":
            if len(head) < 128:
                return False
            h, w = int.from_bytes(head[12:16], "little"), int.from_bytes(head[16:20], "little")
            pf_flags, fourcc = int.from_bytes(head[80:84], "little"), head[84:88]
            bits = int.from_bytes(head[88:92], "little")
            # compressed (FourCC) formats, or uncompressed 32-bit RGB(A); BeamNG rejects 24-bit RGB DDS
            fmt_ok = fourcc in self.DDS_OK if pf_flags & 0x4 else bool(pf_flags & 0x40) and bits == 32
            return fmt_ok and good(w) and good(h)
        if head[:4] == b"\x89PNG" or head[:3] == b"\xff\xd8\xff":
            try:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(k.texture_bytes(t))) as im:
                    return good(im.size[0]) and good(im.size[1])
            except Exception:
                return False
        return False          # TIFF, TGA, BMP...: converted to PNG

    def raw_or_png(self, k, name, tex_rel, kind="color"):
        return f"{tex_rel}/{name}" if self.raw_ok(k, name) else (self.rgba_png(k, name, kind) or f"{tex_rel}/{name}")

    def opacity(self, k, name):
        key = ("opacity", name)
        if key not in self.done:
            im = self._img(k, name)
            self.done[key] = self._save(im.getchannel("A").convert("RGB"), Path(name).stem + "_opacity.data.png") if im else None
        return self.done[key]

    def layer_mean(self, k, name):
        if name not in self._mean_cache:
            im = self._img(k, name)
            self._mean_cache[name] = (np.asarray(im.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(0) / 255
                                      if im else np.array([0.4, 0.4, 0.35], np.float32))
        return self._mean_cache[name]

    def detail(self, k, name, contrast=0.9):
        """A tiling AC detail texture as a BeamNG detail map: greyscale, neutral at mid grey (Torque adds
        detail - 0.5 to the base colour), so it brings back the close-up texture without shifting the colour."""
        key = ("detail", name)
        if key not in self.done:
            im = self._img(k, name)
            out = None
            if im is not None:
                if max(im.size) > 1024:
                    sc = 1024 / max(im.size)
                    im = im.resize((max(16, int(im.size[0] * sc)), max(16, int(im.size[1] * sc))))
                lum = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
                d = np.clip(0.5 + (lum - lum.mean()) * contrast, 0, 1)
                if float(d.std()) > 0.01:          # a flat texture adds nothing
                    from PIL import Image
                    out = self._save(Image.fromarray((d * 255).astype(np.uint8), "L").convert("RGB"),
                                     f"{Path(name).stem}_detail.data.png")
            self.done[key] = out
        return self.done[key]

    def multilayer(self, k, acmat, macro_mean=0.6):
        key = ("ml", acmat.name)
        if key in self.done:
            return self.done[key]
        s = acmat.samplers
        mask = self._img(k, s.get("txMask", ""))
        macro = self._img(k, s.get("txDiffuse", ""))
        if mask is None or macro is None:
            self.done[key] = None
            return None
        if max(mask.size) > 4096:              # bound memory: the bake never needs more than 4096 px
            sc = 4096 / max(mask.size)
            mask = mask.resize((int(mask.size[0] * sc), int(mask.size[1] * sc)))
        size = mask.size
        m = np.asarray(mask, dtype=np.float32) / 255.0
        w = m / np.maximum(m.sum(-1, keepdims=True), 1e-3)
        cols = np.stack([self.layer_mean(k, s.get(f"txDetail{c}", "")) for c in "RGBA"])  # (4,3)
        col = w @ cols                                                                    # (H,W,3)
        self.ml_weights[acmat.name] = w.reshape(-1, 4).mean(0)    # how much of the ground each layer covers
        mac = np.asarray(macro.convert("RGB").resize(size), dtype=np.float32) / 255.0
        del macro
        col = col * (mac / macro_mean)          # macro supplies light/dark variation only
        out = np.clip(col * 255, 0, 255).astype(np.uint8)
        from PIL import Image
        self.done[key] = self._save(Image.fromarray(out, "RGB"), f"{safe(acmat.name).lower()}_baked.color.png")
        return self.done[key]


def bng_material(name, acmat, k, baker, tex_rel, ground_type=None):
    s = acmat.samplers
    shader = acmat.shader
    stage = {"roughnessFactor": 0.85, "metallicFactor": 0.0, "useAnisotropic": True}
    # blend mode 2 is alpha-to-coverage: a cut-out, like alpha testing (common on decals of game rips)
    alpha_test = acmat.alpha_tested or acmat.alpha_blend == 2 or "AT" in shader.replace("ksPerPixelMultiMap", "") or shader == "ksTree"
    translucent = acmat.alpha_blend == 1

    if shader.startswith("ksMultilayer"):
        base = baker.multilayer(k, acmat)
        if base:
            stage["baseColorMap"] = base
            # AC tiles the four detail layers at multR..multA times the macro UV. One material takes one detail
            # map, so use the layer that covers the most ground for the close-up texture.
            w = baker.ml_weights.get(acmat.name)
            if w is not None:
                c = "RGBA"[int(np.argmax(w))]
                mult = acmat.props.get(f"mult{c}", (0,))[0]
                dm = baker.detail(k, s[f"txDetail{c}"]) if mult > 0 and f"txDetail{c}" in s else None
                if dm:
                    stage["detailMap"] = dm
                    stage["detailScale"] = [round(float(mult), 3)] * 2
        stage["roughnessFactor"] = 0.95
    elif "txDiffuse" in s:
        if shader.startswith("ksPerPixelMultiMap") or not (alpha_test or translucent):
            stage["baseColorMap"] = baker.opaque(k, s["txDiffuse"]) or f"{tex_rel}/{s['txDiffuse']}"
        else:
            stage["baseColorMap"] = baker.raw_or_png(k, s["txDiffuse"], tex_rel)
        mult = acmat.props.get("detailUVMultiplier", (0,))[0]
        if shader.startswith("ksPerPixelMultiMap") and "txDetail" in s and mult > 0 and not translucent:
            # AC blends a small tiling detail texture over the base; without it roads look blurry up close
            dm = baker.detail(k, s["txDetail"])
            if dm:
                stage["detailMap"] = dm
                stage["detailScale"] = [round(float(mult), 3)] * 2
        if alpha_test or translucent:
            op = baker.opacity(k, s["txDiffuse"])
            if op:
                stage["opacityMap"] = op
    if "txNormal" in s:
        stage["normalMap"] = baker.raw_or_png(k, s["txNormal"], tex_rel, "normal")
    if not ground_type and not (alpha_test or translucent) and \
            PROFILE.rx("water_re").search(f"{acmat.name} {s.get('txDiffuse', '')}"):
        # AC water is a tiling diffuse + normal map made glossy by a strong specular; BeamNG's PBR at the
        # default roughness shows every tile of that normal map as a matte square. Smooth, unbumped water
        # (the sky and shore reflections give it the look) matches AC far better.
        stage.pop("normalMap", None)
        stage.update({"roughnessFactor": 0.04, "metallicFactor": 0.0})
        stage.pop("useAnisotropic", None)
        mat_water = True
    else:
        mat_water = False
    if acmat.props.get("ksEmissive", (0,))[0] > 0:
        stage["emissiveFactor"] = [acmat.props["ksEmissive"][0]] * 3
    mat = {
        "name": name, "mapTo": name, "class": "Material", "version": 1.5,
        "Stages": [stage, {}, {}, {}],
        "materialTag0": "beamng", "materialTag1": "kn5_port",
        "ac_shader": shader, "ac_material": acmat.name,
    }
    if mat_water:
        mat["kn5_water"] = True
    if alpha_test:
        ref = acmat.props.get("ksAlphaRef", (0.5,))[0]
        mat["alphaTest"] = True
        mat["alphaRef"] = int(round(255 * (ref if 0 < ref < 1 else 0.5)))
    if translucent:
        mat["translucent"] = True
        mat["translucentZWrite"] = False
        if acmat.props.get("ksDiffuse", (1,))[0] == 0 and acmat.props.get("ksAmbient", (1,))[0] == 0:
            # unlit in AC (pure darkening overlay: soft AO / dirt decals) -> black, alpha from the texture
            stage["baseColorFactor"] = [0, 0, 0, 1]
    if shader == "ksTree" or alpha_test:
        mat["doubleSided"] = True
    if ground_type:
        mat["groundType"] = ground_type
    return mat


def hidden_material(name, ground_type=None):
    """Invisible material for physics meshes AC doesn't draw: every pixel is alpha-discarded (no shadow either),
    but the shape still collides with its ground type."""
    mat = {"name": name, "mapTo": name, "class": "Material", "version": 1.5,
           "Stages": [{"baseColorFactor": [1, 1, 1, 0], "opacityFactor": 0.0}, {}, {}, {}],
           "alphaTest": True, "alphaRef": 255, "castShadows": False,
           "materialTag0": "beamng", "materialTag1": "kn5_port", "kn5_hidden": True}
    if ground_type:
        mat["groundType"] = ground_type
    return mat


# ---- main -----------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track_dir")
    ap.add_argument("out_dir", help="mod root, e.g. ...\\mods\\unpacked\\skyline_boulevard")
    ap.add_argument("--level", default=None, help="level folder name (default: track folder name)")
    ap.add_argument("--profile", default=None, help="track profile JSON (see kn5port/profile.py)")
    ap.add_argument("--only", nargs="*", help="convert only these kn5 files (names without .kn5)")
    ap.add_argument("--include", nargs="*", default=[], help="also convert these normally-skipped kn5s")
    ap.add_argument("--cell", type=float, default=500.0, help="grid cell size in metres (default 500)")
    ap.add_argument("--crop-margin", type=float, default=1500.0,
                    help="drop meshes further than this from the road's bounding box (default 1500; 0 = keep all)")
    ap.add_argument("--road-kn5", default=None, help="kn5 whose bounds define the centre (default: *road*.kn5)")
    ap.add_argument("--spawn-kn5", default=None, help="kn5 with AC_PIT/AC_START dummies (default: auto)")
    ap.add_argument("--no-extra-collision", action="store_true",
                    help="don't add collision to visual-only terrain (see EXTRA_COLLISION_RE)")
    ap.add_argument("--terrain", default="0", metavar="SIZE",
                    help="leave the AC ground to a BeamNG terrain of SIZE x SIZE samples (built by kn5port (terrain step)); "
                         "e.g. 4096. AC ground meshes inside the terrain are dropped")
    ap.add_argument("--terrain-square", type=float, default=0, help="metres per terrain sample (default: auto)")
    ap.add_argument("--show-hidden", action="store_true",
                    help="also draw meshes AC hides (physics-only road copies; these z-fight with the visual road)")
    ap.add_argument("--keep-ac-trees", action="store_true",
                    help="keep AC's tree/groundcover meshes instead of leaving them to kn5port (forest step)")
    ap.add_argument("--layer-lift", type=float, default=LAYER_STEP, metavar="M",
                    help=f"raise road layers that lie on an earlier-drawn surface (patches, lines, decals) by M "
                         f"metres per layer so they don't z-fight (default {LAYER_STEP}; 0 = off)")
    args = ap.parse_args(argv)

    track = Path(args.track_dir)
    level = safe(args.level or track.name).lower()
    out_level = Path(args.out_dir) / "levels" / level
    lvl_rel = f"/levels/{level}"
    tex_rel = f"{lvl_rel}/art/textures"
    global PROFILE, SURFACES
    PROFILE = Profile.load(args.profile, track)
    SURFACES = read_surfaces(track)
    baker = TextureBaker(out_level / "art" / "textures", tex_rel)
    t0 = time.time()

    all_models = models_from_ini(track)
    stems = [Path(m).stem for m in all_models]
    def is_layout(stem):
        # per-layout kn5s (timing gates, pits) are tiny and carry no textures
        p = track / f"{stem}.kn5"
        if not p.exists() or os.path.getsize(p) > 1_000_000:
            return False
        with KN5(str(p)) as kk:
            return not kk.textures
    layout_kn5 = [s for s in stems if is_layout(s)]
    if args.only:
        todo = args.only
    else:
        todo = [s for s in stems if s not in layout_kn5 and not (args.terrain != "0" and is_horizon_kn5(s))] + \
               [s for s in args.include if s not in stems]
    print(f"converting: {', '.join(todo)}")

    # -- origin from road bounds
    lo = np.full(3, np.inf); hi = np.full(3, -np.inf)
    alo = np.full(3, np.inf); ahi = np.full(3, -np.inf)
    files = [args.road_kn5] if args.road_kn5 else todo
    for stem in files:
        p_ = track / f"{stem}.kn5"
        if not p_.exists():
            continue
        try:
            k = KN5(str(p_))
        except KN5Error as e:
            raise SystemExit(f"{p_.name}: {e}")
        with k:
            for m in k.meshes():
                if not len(m.positions):
                    continue
                p = ac_to_bng(m.positions)
                alo = np.minimum(alo, p.min(0)); ahi = np.maximum(ahi, p.max(0))
                s_ = surface_of(m)
                if s_ and ground_type_of(s_) == "ASPHALT":
                    lo = np.minimum(lo, p.min(0)); hi = np.maximum(hi, p.max(0))
    if not np.isfinite(lo).all():          # no road surfaces found: use everything
        lo, hi = alo, ahi
    offset = np.round((lo + hi) / 2 / 10) * 10
    offset[2] = 0.0   # keep real-ish elevations
    road_lo, road_hi = lo - offset, hi - offset
    terrain_rect = None
    if args.terrain != "0":
        extent = float(max(road_hi[0] - road_lo[0], road_hi[1] - road_lo[1])) + 2 * PROFILE.terrain_margin
        sq = args.terrain_square or max(PROFILE.terrain_min_square,
                                        math.ceil(extent / PROFILE.terrain_max_size * 4) / 4)
        if args.terrain == "auto":
            size = 512
            while size * sq < extent and size < PROFILE.terrain_max_size:
                size *= 2
        else:
            size = int(args.terrain)
        args.terrain, args.terrain_square = size, sq
        print(f"terrain: {size}x{size} samples at {sq} m ({size * sq / 1000:.1f} km)")
        half = args.terrain * args.terrain_square / 2
        cx, cy = (road_lo[0] + road_hi[0]) / 2, (road_lo[1] + road_hi[1]) / 2
        terrain_rect = [cx - half, cy - half, cx + half - args.terrain_square, cy + half - args.terrain_square]
    print(f"origin offset (BeamNG xyz): {offset.tolist()}  road extent: {np.round(hi - lo).tolist()} m")

    materials = {}         # bng name -> material dict
    textures_needed = {}   # texture name -> (kn5 path, Texture)
    statics = defaultdict(list)   # group -> list of TSStatic dicts
    stats = {}
    lifter = OverlayLifter(step=args.layer_lift)

    for stem in todo:
        path = track / f"{stem}.kn5"
        if not path.exists():
            print(f"  ! {path.name} not found, skipping"); continue
        ts = time.time()
        k = KN5(str(path))
        tex_by_name = {t.name: t for t in k.textures}
        global USES
        USES = material_uses(k, SURFACES, PROFILE)
        cells = defaultdict(lambda: defaultdict(list))  # (cx, cy, kind) -> matname -> [parts]
        mat_surfaces = defaultdict(set)
        ground_mats = set()
        kept = dropped_lod = dropped_crop = dropped_veg = dropped_ground = dropped_hidden = 0

        for m in k.meshes():
            if not len(m.indices) or m.name.startswith("AC_"):
                continue
            if m.lod_in > 0:
                dropped_lod += 1; continue
            if not args.keep_ac_trees and is_vegetation(m):
                dropped_veg += 1; continue
            pos = ac_to_bng(m.positions.astype(np.float64)) - offset
            if args.crop_margin > 0:
                mlo, mhi = pos.min(0), pos.max(0)
                if (mhi[0] < road_lo[0] - args.crop_margin or mlo[0] > road_hi[0] + args.crop_margin or
                        mhi[1] < road_lo[1] - args.crop_margin or mlo[1] > road_hi[1] + args.crop_margin):
                    dropped_crop += 1; continue
            nrm = ac_to_bng(m.normals)
            idx = m.indices
            if is_ac_ground(m):
                ground_mats.add(m.material.name)
            if terrain_rect and is_ac_ground(m):
                # triangles whose centre lies well inside the terrain are replaced by it
                t = idx.reshape(-1, 3)
                c = pos[t].mean(1)
                inner = ((c[:, 0] > terrain_rect[0] + 50) & (c[:, 0] < terrain_rect[2] - 50) &
                         (c[:, 1] > terrain_rect[1] + 50) & (c[:, 1] < terrain_rect[3] - 50))
                if inner.all():
                    dropped_ground += 1; continue
                idx = t[~inner].reshape(-1)
            surf = surface_of(m)
            if surf is None and not args.no_extra_collision and PROFILE.rx("ground_name_re").search(m.name):
                surf = "_EXTRA"
            # meshes AC never draws: physics-only copies of the road (common on Forza/other game rips, where
            # they sit exactly on the visual road and would z-fight with it) collide invisibly; others go
            hidden = m.hidden and not args.show_hidden
            if hidden and not surf:
                dropped_hidden += 1; continue
            kind = "col" if surf else "vis"
            if surf:
                mat_surfaces[m.material.name].add(surf)
            c = pos.mean(0)
            cell = (int(math.floor(c[0] / args.cell)), int(math.floor(c[1] / args.cell)), kind)
            pos32, idx32 = pos.astype(np.float32), idx.astype(np.uint32)
            if not hidden:     # drawn: may be a layer on top of an earlier surface (lifted in place below)
                lifter.add(pos32, idx32, m.transparent or m.material.alpha_blend == 1)
            cells[cell][(m.material.name, surf, hidden)].append((pos32, nrm.astype(np.float32), m.uvs, idx32))
            kept += 1

        lifted0 = lifter.stats["meshes"]
        lifter.resolve()
        lifted = lifter.stats["meshes"] - lifted0

        # materials for this kn5
        def bng_name(acname, surf):
            base = f"{level}_{safe(acname)}"
            if surf and len(mat_surfaces[acname]) > 1:
                return f"{base}_{surf}"
            return base

        for (cx, cy, kind), bymat in cells.items():
            groups = []
            for (acname, surf, hidden), parts in bymat.items():
                name = bng_name(acname, surf)
                acmat = next(mm for mm in k.materials if mm.name == acname) if acname != "__missing__" else None
                if hidden:
                    gt = ("GRASS" if surf == "_EXTRA" else ground_type_of(surf)) if surf else None
                    name = f"{level}_kn5_hidden_{safe(gt or 'none').lower()}"
                    if name not in materials:
                        materials[name] = hidden_material(name, gt)
                elif acmat is not None and name not in materials:
                    gt = ("GRASS" if surf == "_EXTRA" else ground_type_of(surf)) if surf else None
                    materials[name] = bng_material(name, acmat, k, baker, tex_rel, gt)
                    if acname in ground_mats:
                        materials[name]["kn5_ground"] = True   # natural ground (the terrain ignores it)
                    used = {Path(v).name for v in materials[name]["Stages"][0].values() if isinstance(v, str)}
                    for tname in acmat.samplers.values():
                        if tname in used and tname in tex_by_name and tname not in textures_needed:
                            textures_needed[tname] = (path, tex_by_name[tname])
                for chunk in merge_chunks(parts):
                    groups.append((name, *chunk))
            origin = np.array([(cx + 0.5) * args.cell, (cy + 0.5) * args.cell, 0.0])
            fname = f"x{cx:+04d}_y{cy:+04d}_{kind}".replace("+", "p").replace("-", "m")
            dae = out_level / "art" / "shapes" / stem / f"{fname}.dae"
            write_dae(dae, groups, origin)
            st = {"class": "TSStatic", "name": f"{stem}_{fname}", "__parent": stem,
                  "shapeName": f"{lvl_rel}/art/shapes/{stem}/{fname}.dae",
                  "position": [round(float(v), 3) for v in origin]}
            if kind == "col":
                st.update({"collisionType": "Visible Mesh Final", "decalType": "Visible Mesh Final"})
            else:
                st.update({"collisionType": "None", "decalType": "None"})
            statics[stem].append(st)
        stats[stem] = dict(meshes=kept, lod_dropped=dropped_lod, cropped=dropped_crop, layers_lifted=lifted,
                           shapes=len(cells), seconds=round(time.time() - ts, 1))
        print(f"  {stem}: {kept} meshes -> {len(cells)} shapes "
              f"(dropped {dropped_lod} LOD, {dropped_crop} outside crop, {dropped_veg} vegetation, "
              f"{dropped_ground} terrain-replaced, {dropped_hidden} hidden; {lifted} layers lifted) "
              f"in {stats[stem]['seconds']}s")
        # copy textures for this kn5 now, then close
        tex_dir = out_level / "art" / "textures"
        tex_dir.mkdir(parents=True, exist_ok=True)
        for tname, (tpath, t) in list(textures_needed.items()):
            if tpath == path and not (tex_dir / tname).exists():
                (tex_dir / tname).write_bytes(k.texture_bytes(t))
        k.close()

    # -- materials file
    mat_file = out_level / "art" / "shapes" / f"{level}.materials.json"
    mat_file.parent.mkdir(parents=True, exist_ok=True)
    mat_file.write_text(json.dumps(materials, indent=2))

    # -- spawns: one per AC layout (start line for point-to-point, first pit otherwise). Layouts are the ui/
    #    subfolders holding a ui_track.json (or ui/ itself for single-layout tracks); their pit/start
    #    dummies live in a per-layout kn5 when there is one, otherwise in the main track kn5s.
    spawns, spawn_info = [], []
    ui_root = track / "ui"
    layouts = sorted(p.parent.name for p in ui_root.glob("*/ui_track.json")) if ui_root.exists() else []
    single = not layouts and (ui_root / "ui_track.json").exists()
    if not layouts:
        layouts = [""]
    main_dummies = None

    def dummies_of(stems_):
        out = {}
        for st in stems_:
            p = track / f"{st}.kn5"
            if p.exists():
                try:
                    with KN5(str(p)) as k:
                        for d in k.dummies():
                            if d.name.startswith("AC_") and d.name not in out:
                                out[d.name] = d
                except KN5Error:
                    pass
        return out

    for lay in layouts:
        if args.spawn_kn5:
            d_by = dummies_of([args.spawn_kn5])
        elif lay and lay in layout_kn5:
            d_by = dummies_of([lay])
        else:
            if main_dummies is None:
                main_dummies = dummies_of(todo + layout_kn5)
            d_by = main_dummies
        ui_dir = ui_root / lay if lay else ui_root
        ui = {}
        uij = ui_dir / "ui_track.json"
        if uij.exists():
            try:
                ui = json.loads(uij.read_text(encoding="utf-8-sig", errors="replace"))
            except ValueError:
                pass
        free = "free" in (ui.get("run", "") + lay).lower()
        d = (d_by.get("AC_PIT_0") if free else None) or d_by.get("AC_START_0") or d_by.get("AC_PIT_0") or \
            d_by.get("AC_HOTLAP_START_0")
        if d is None:
            continue
        pos = ac_to_bng(d.position[None, :])[0] - offset
        fwd = ac_to_bng(d.matrix[2:3, :3])[0]
        yaw = math.atan2(fwd[0], -fwd[1])     # vehicle forward is -Y in BeamNG
        c, sn = math.cos(yaw), math.sin(yaw)
        name = f"spawn_{safe(lay or level)}"
        if any(s_["name"] == name for s_ in spawns):
            continue
        spawns.append({"class": "SpawnSphere", "name": name, "__parent": "MissionGroup",
                       "dataBlock": "SpawnSphereMarker",
                       "position": [round(float(v), 3) for v in pos + [0, 0, 0.5]],
                       "rotationMatrix": [c, -sn, 0, sn, c, 0, 0, 0, 1], "radius": 1})
        prev = ui_dir / "preview.png"
        jpg = None
        if prev.exists():
            from PIL import Image
            jpg = f"{safe(lay or level)}.jpg"
            out_level.mkdir(parents=True, exist_ok=True)
            Image.open(prev).convert("RGB").save(out_level / jpg, quality=92)
        title = ui.get("name", (lay or level).replace("_", " ").title())
        title = title.split(" - ", 1)[1] if " - " in title and not single else title
        spawn_info.append({"objectname": name, "translationId": title,
                           "description": ui.get("description", ""), "preview": jpg, "ui": ui})
    if not spawns:
        spawns.append({"class": "SpawnSphere", "name": "spawn_default", "__parent": "MissionGroup",
                       "dataBlock": "SpawnSphereMarker", "position": [0, 0, 300], "radius": 1})
        spawn_info.append({"objectname": "spawn_default", "translationId": "Default", "ui": {}})
    # free roam first: it is the natural default
    order = sorted(range(len(spawns)), key=lambda i: "free" not in spawns[i]["name"])
    spawns = [spawns[i] for i in order]; spawn_info = [spawn_info[i] for i in order]

    # -- scene files (newline-delimited JSON, like the stock levels)
    main_dir = out_level / "main"
    mg_dir = main_dir / "MissionGroup"
    mg_dir.mkdir(parents=True, exist_ok=True)
    (main_dir / "items.level.json").write_text(json.dumps(
        {"name": "MissionGroup", "class": "SimGroup", "enabled": "1"}) + "\n")
    mg = [
        {"class": "LevelInfo", "name": "theLevelInfo", "__parent": "MissionGroup",
         "visibleDistance": 4000, "gravity": -9.81, "canvasClearColor": [0.5, 0.6, 0.7, 1]},
        {"class": "TimeOfDay", "name": "tod", "__parent": "MissionGroup", "time": 0.1, "play": False,
         "azimuthOverride": 0, "startTime": 0.1},
        {"class": "ScatterSky", "name": "sunsky", "__parent": "MissionGroup", "position": [0, 0, 0],
         "shadowDistance": 1600, "shadowSoftness": 0.2, "brightness": 1.0},
        *spawns,
    ] + [{"name": stem, "class": "SimGroup", "__parent": "MissionGroup"} for stem in statics]
    (mg_dir / "items.level.json").write_text("".join(json.dumps(o) + "\n" for o in mg))
    for stem, items in statics.items():
        d = mg_dir / stem
        d.mkdir(exist_ok=True)
        (d / "items.level.json").write_text("".join(json.dumps(o) + "\n" for o in items))

    # -- info.json (level picker: title, description, preview, spawn points)
    size = [int(road_hi[0] - road_lo[0] + 2 * args.crop_margin), int(road_hi[1] - road_lo[1] + 2 * args.crop_margin)]
    ui0 = next((si["ui"] for si in spawn_info if si["ui"]), {})
    base_title = ui0.get("name", track.name.replace("_", " ").title()).split(" - ")[0]
    desc = ui0.get("description", "")
    readme = next((p for p in (track / "README.txt", track / "readme.txt") if p.exists()), None)
    if readme:   # first paragraph after the title line is usually the overview
        paras = [p.strip() for p in readme.read_text(errors="replace").split("\n\n") if p.strip()]
        if len(paras) > 1 and not paras[1].isupper():
            desc = " ".join(paras[1].split())
    author = ui0.get("author", "the original authors")
    info = {
        "title": base_title,
        "description": (desc + "\n\n" if desc else "") +
                       f"Personal port of the Assetto Corsa track by {author}. Not for redistribution.",
        "authors": f"{author} (original Assetto Corsa track); converted with kn5port convert",
        "country": ui0.get("country", ""), "biome": "temperate",
        "roads": ui0.get("length", ""), "size": size,
        "previews": [si["preview"] for si in spawn_info if si.get("preview")][:3],
        "spawnPoints": [{k: v for k, v in si.items() if k in ("objectname", "translationId", "description", "preview") and v}
                        for si in spawn_info],
        "defaultSpawnPointName": spawns[0]["name"],
    }
    (out_level / "info.json").write_text(json.dumps(info, indent=2))
    (out_level / "kn5_port.json").write_text(json.dumps(
        {"source": str(track), "offset_bng": offset.tolist(), "level": level, "crop_margin": args.crop_margin,
         "road_lo": road_lo.tolist(), "road_hi": road_hi.tolist(),
         "terrain": {"size": args.terrain, "square": args.terrain_square, "rect": terrain_rect} if args.terrain else None, "cell": args.cell, "stats": stats}, indent=2))

    print(f"materials: {len(materials)}  textures copied: {len(textures_needed)}  baked: "
          f"{sum(1 for v in baker.done.values() if v)}  spawns: {len(spawns)}")
    print(f"done in {time.time() - t0:.0f}s -> {out_level}")


if __name__ == "__main__":
    main()
