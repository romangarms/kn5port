"""Minimal reader for Assetto Corsa .kn5 model files (versions 5 and 6, unencrypted).

Usage as a library:
    from kn5 import KN5
    k = KN5("skyline_road.kn5")
    for mesh in k.meshes():          # world-space meshes, transforms already applied
        print(mesh.name, mesh.material.name, len(mesh.positions))

Coordinates are returned exactly as stored by AC (Y-up). Conversion to other
axis conventions is left to the caller.
"""
import mmap
import struct
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"sc6969"

# Vertex layouts (bytes): position 3f, normal 3f, uv 2f, tangent 3f
VERT_DTYPE = np.dtype([("pos", "<f4", 3), ("nrm", "<f4", 3), ("uv", "<f4", 2), ("tan", "<f4", 3)])
SKIN_VERT_DTYPE = np.dtype([("pos", "<f4", 3), ("nrm", "<f4", 3), ("uv", "<f4", 2), ("tan", "<f4", 3),
                            ("weights", "<f4", 4), ("bones", "<f4", 4)])


class KN5Error(Exception):
    pass


PROTECTED_MSG = ("could not be read: the file is damaged, an unsupported variant, or protected/encrypted. "
                 "kn5port only converts unprotected files and does not try to get around protection.")


@dataclass
class Texture:
    name: str
    offset: int   # byte offset of the image data inside the kn5
    size: int
    active: int


@dataclass
class Material:
    index: int
    name: str
    shader: str
    alpha_blend: int
    alpha_tested: bool
    depth_mode: int
    props: dict = field(default_factory=dict)      # name -> (float, vec2, vec3, vec4)
    samplers: dict = field(default_factory=dict)   # sampler name (txDiffuse...) -> texture name

    @property
    def uv_mult(self):
        """UV tiling the shader applies to the stored UVs: the *_UVMult shaders sample their textures at
        uv * diffuseMult (game rips use them for grass, rock, gravel and road), everything else at uv."""
        if "UVMult" in self.shader:
            v = self.props.get("diffuseMult", (1.0,))[0]
            if 0 < v < 10000:
                return float(v)
        return 1.0


@dataclass
class Mesh:
    name: str
    path: str                # full node path, e.g. "root/ROAD/1ROAD_03"
    material: Material
    positions: np.ndarray    # (N,3) float32, world space
    normals: np.ndarray      # (N,3) float32, world space
    uvs: np.ndarray          # (N,2) float32, as stored (DirectX convention, v down)
    indices: np.ndarray      # (M,) uint32, triangle list
    visible: bool
    transparent: bool
    lod_in: float
    lod_out: float
    skinned: bool = False
    renderable: bool = True  # AC skips drawing meshes with this flag (or `visible`) cleared

    @property
    def hidden(self):
        """Not drawn by AC: physics-only or switched-off meshes (they still collide if they're physics)."""
        return not (self.visible and self.renderable)


@dataclass
class Dummy:
    name: str
    path: str
    matrix: np.ndarray       # 4x4 world matrix (row-vector convention: p' = p @ M)

    @property
    def position(self):
        return self.matrix[3, :3].copy()


class _Reader:
    def __init__(self, buf):
        self.buf = buf
        self.pos = 0

    def take(self, n):
        if self.pos + n > len(self.buf):
            raise KN5Error(f"unexpected end of file at {self.pos} (wanted {n} bytes)")
        b = self.buf[self.pos:self.pos + n]
        self.pos += n
        return b

    def skip(self, n):
        self.take(0)
        self.pos += n

    def i32(self):
        return struct.unpack_from("<i", self.take(4))[0]

    def u8(self):
        return self.take(1)[0]

    def f32(self):
        return struct.unpack_from("<f", self.take(4))[0]

    def floats(self, n):
        return struct.unpack_from(f"<{n}f", self.take(4 * n))

    def string(self):
        n = self.i32()
        if n < 0 or n > 1 << 16:
            raise KN5Error(f"implausible string length {n} at {self.pos - 4}")
        return self.take(n).decode("utf-8", errors="replace")

    def array(self, dtype, count):
        nbytes = dtype.itemsize * count
        start = self.pos
        self.skip(nbytes)
        return np.frombuffer(self.buf, dtype=dtype, count=count, offset=start)


class KN5:
    def __init__(self, path):
        self.path = path
        self._verify = None
        self._f = open(path, "rb")
        self._mm = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            self._parse_header()
        except KN5Error as e:
            self.close()
            raise KN5Error(f"{Path(path).name} {PROTECTED_MSG} ({e})") from None
        except (struct.error, ValueError, IndexError, UnicodeError, MemoryError) as e:
            self.close()
            raise KN5Error(f"{Path(path).name} {PROTECTED_MSG} ({type(e).__name__})") from None

    def _parse_header(self):
        path = self.path
        r = _Reader(self._mm)
        if r.take(6) != MAGIC:
            raise KN5Error(f"{path}: not a kn5 file (bad magic)")
        self.version = r.i32()
        if self.version > 5:
            self.extra = r.i32()
        if self.version not in (5, 6):
            raise KN5Error(f"{path}: unsupported kn5 version {self.version}")

        def count(what, limit=200_000):
            c = r.i32()
            if not 0 <= c <= limit:
                raise KN5Error(f"implausible {what} count {c}")
            return c

        self.textures = []
        for _ in range(count("texture")):
            active = r.i32()
            name = r.string()
            size = r.i32()
            self.textures.append(Texture(name, r.pos, size, active))
            r.skip(size)

        self.materials = []
        for idx in range(count("material")):
            m = Material(idx, r.string(), r.string(), r.u8(), bool(r.u8()), r.i32())
            for _ in range(count("property", 4096)):
                pname = r.string()
                val = r.f32()
                extra = r.floats(9)   # vec2, vec3, vec4
                m.props[pname] = (val, extra[0:2], extra[2:5], extra[5:9])
            for _ in range(count("sampler", 4096)):
                sname = r.string()
                r.i32()  # slot
                m.samplers[sname] = r.string()
            self.materials.append(m)

        self._node_start = r.pos
        self._reader = r
        if r.pos + 4 <= len(self._mm):
            first = struct.unpack_from("<i", self._mm, r.pos)[0]
            if first not in (1, 2, 3):
                raise KN5Error(f"unexpected node type {first} at the root")

    # -- textures -------------------------------------------------------
    def texture_bytes(self, tex):
        return bytes(self._mm[tex.offset:tex.offset + tex.size])

    # -- integrity -------------------------------------------------------
    IMAGE_MAGIC = (b"DDS ", b"\x89PNG", b"\xff\xd8\xff", b"BM", b"II*\x00", b"MM\x00*")

    def verify(self, sample_every=10, max_verts=2048):
        """Check that the payload, not just the structure, is plain data: textures carry real image signatures
        and sampled meshes hold plausible geometry (finite, plausible-scale positions, unit normals, indices in
        range). Protection schemes that keep the file structure but scramble the data fail here.
        Returns None if the file looks fine, otherwise a short reason."""
        bad_tex = [t.name for t in self.textures if t.size >= 4 and not t.name.lower().endswith(".tga") and
                   not bytes(self._mm[t.offset:t.offset + 4]).startswith(self.IMAGE_MAGIC)]
        if bad_tex and len(bad_tex) > len(self.textures) // 10:       # tolerate a stray odd file
            return f"{len(bad_tex)} of {len(self.textures)} textures are not plain images"
        self._verify = {"n": 0, "every": sample_every, "max": max_verts, "bad": 0, "checked": 0}
        try:
            for _ in self.walk(include_meshes=False):
                pass
        except KN5Error as e:
            return f"structure: {e}"
        finally:
            v, self._verify = self._verify, None
        if v["bad"] and v["bad"] > v["checked"] // 20:
            return f"{v['bad']} of {v['checked']} sampled meshes hold implausible data"
        return None

    def _verify_mesh(self, name, verts, idx, mat_id):
        v = self._verify
        v["n"] += 1
        if (v["n"] - 1) % v["every"] or not len(verts):
            return
        v["checked"] += 1
        sub = verts[: v["max"]]
        pos, nrm = sub["pos"], sub["nrm"]
        ok = np.isfinite(pos).all() and np.isfinite(nrm).all() and float(np.abs(pos).max()) < 2e6
        if ok:
            ln = np.linalg.norm(nrm.astype(np.float64), axis=1)
            ok = float(np.mean(np.abs(ln - 1) < 0.05)) > 0.8 or float(np.mean(ln < 1e-6)) > 0.8
        if ok and len(idx):
            ok = int(idx.max()) < len(verts) and len(idx) % 3 == 0
        if ok:
            ok = -1 <= mat_id < max(len(self.materials), 1)
        if not ok:
            v["bad"] += 1

    # -- node tree ------------------------------------------------------
    def walk(self, include_meshes=True):
        """Yield Mesh and Dummy objects in file order, transforms applied."""
        r = self._reader
        r.pos = self._node_start
        if r.pos >= len(self._mm):
            return
        yield from self._node(r, np.identity(4, dtype=np.float64), "", include_meshes)

    def mesh_names(self):
        """(mesh name, Material) for every mesh, without building geometry (fast)."""
        return [(n.name, n.material) for n in self._walk_names()]

    def _walk_names(self):
        r = self._reader
        r.pos = self._node_start
        out = []
        if r.pos >= len(self._mm):
            return out

        class _N:
            __slots__ = ("name", "material")

        def node():
            cls = r.i32(); name = r.string(); children = r.i32(); r.u8()
            if cls == 1:
                r.floats(16)
            elif cls in (2, 3):
                r.u8(); r.u8(); r.u8()
                if cls == 3:
                    for _ in range(r.i32()):
                        r.string(); r.floats(16)
                nv = r.i32()
                r.skip(nv * (SKIN_VERT_DTYPE.itemsize if cls == 3 else VERT_DTYPE.itemsize))
                ni = r.i32(); r.skip(ni * 2)
                mid = r.i32(); r.i32(); r.f32(); r.f32()
                if cls == 2:
                    r.floats(4); r.u8()
                o = _N(); o.name = name
                o.material = self.materials[mid] if 0 <= mid < len(self.materials) else \
                    Material(-1, "__missing__", "", 0, False, 0)
                out.append(o)
            else:
                raise KN5Error(f"unknown node class {cls}")
            for _ in range(children):
                node()
        node()
        return out

    def meshes(self):
        return (n for n in self.walk() if isinstance(n, Mesh))

    def dummies(self):
        return [n for n in self.walk(include_meshes=False) if isinstance(n, Dummy)]

    def _node(self, r, parent, parent_path, include_meshes):
        cls = r.i32()
        name = r.string()
        children = r.i32()
        r.u8()  # active
        path = f"{parent_path}/{name}" if parent_path else name
        world = parent

        if cls == 1:  # dummy / transform node
            local = np.array(r.floats(16), dtype=np.float64).reshape(4, 4)
            world = local @ parent
            yield Dummy(name, path, world)
        elif cls == 2:  # static mesh
            r.u8()                       # cast shadows
            visible = bool(r.u8())
            transparent = bool(r.u8())
            nv = r.i32()
            verts = r.array(VERT_DTYPE, nv)
            ni = r.i32()
            idx = r.array(np.dtype("<u2"), ni)
            mat_id = r.i32()
            r.i32()                      # layer
            lod_in, lod_out = r.f32(), r.f32()
            r.floats(4)                  # bounding sphere
            renderable = bool(r.u8())
            if self._verify is not None:
                self._verify_mesh(name, verts, idx, mat_id)
            if include_meshes:
                yield self._make_mesh(name, path, mat_id, verts, idx, world, visible, transparent,
                                      lod_in, lod_out, False, renderable)
        elif cls == 3:  # skinned mesh (rare on tracks); exported in bind pose
            r.u8(); visible = bool(r.u8()); transparent = bool(r.u8())
            for _ in range(r.i32()):
                r.string(); r.floats(16)
            nv = r.i32()
            verts = r.array(SKIN_VERT_DTYPE, nv)
            ni = r.i32()
            idx = r.array(np.dtype("<u2"), ni)
            mat_id = r.i32()
            r.i32()
            lod_in, lod_out = r.f32(), r.f32()
            if include_meshes:
                yield self._make_mesh(name, path, mat_id, verts, idx, np.identity(4), visible, transparent,
                                      lod_in, lod_out, True)
        else:
            raise KN5Error(f"unknown node class {cls} for '{name}' at {r.pos}")

        for _ in range(children):
            yield from self._node(r, world, path, include_meshes)

    def _make_mesh(self, name, path, mat_id, verts, idx, world, visible, transparent, lod_in, lod_out, skinned,
                   renderable=True):
        pos = verts["pos"].astype(np.float64)
        nrm = verts["nrm"].astype(np.float64)
        if not np.allclose(world, np.identity(4)):
            pos = pos @ world[:3, :3] + world[3, :3]
            nrm = nrm @ world[:3, :3]
            ln = np.linalg.norm(nrm, axis=1, keepdims=True)
            ln[ln == 0] = 1
            nrm = nrm / ln
        mat = self.materials[mat_id] if 0 <= mat_id < len(self.materials) else \
            Material(-1, "__missing__", "", 0, False, 0)
        return Mesh(name, path, mat, pos.astype(np.float32), nrm.astype(np.float32),
                    np.array(verts["uv"], dtype=np.float32), idx.astype(np.uint32),
                    visible, transparent, lod_in, lod_out, skinned, renderable)

    def close(self):
        self._mm.close()
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
