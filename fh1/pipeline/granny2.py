"""RAD Granny 2 files inside FH1's version 44 .pgeo (animatedobjects.zip and the track's Anim_ copies), read as Granny files: header, sections,
pointer fixups and the type definitions they carry, so the object tree can be walked by member name. Big-endian, 32-bit pointers. Layout is in docs/formats.md."""
import struct
import numpy as np

VERSION = bytes([0, 0, 0, 7])         # the file info's first word

# Granny member types. The value sizes are for 32-bit files
END, INLINE, REFERENCE, REF_ARRAY, ARRAY_OF_REFS, VARIANT, _, VARIANT_ARRAY, STRING, TRANSFORM, REAL32 = range(11)
SCALAR = {10: '>f4', 11: 'i1', 12: 'u1', 13: 'i1', 14: 'u1', 15: '>i2', 16: '>u2', 17: '>i2', 18: '>u2', 19: '>i4', 20: '>u4', 21: '>f2'}
SIZE = {INLINE: None, REFERENCE: 4, REF_ARRAY: 8, ARRAY_OF_REFS: 8, VARIANT: 8, VARIANT_ARRAY: 12, STRING: 4, TRANSFORM: 68, 22: 4}


def find(b):
    """offset of the Granny header: the file info that follows it has version 7 and the file's total size, and its section array starts 0x48 in"""
    o = b.find(VERSION, 0)
    while o >= 0:
        h = o - 0x20
        if h >= 0 and o + 0x14 <= len(b):
            total, _, sec_at, nsec = struct.unpack_from('>4I', b, o + 4); hdr = struct.unpack_from('>I', b, h + 0x10)[0]
            if sec_at == 0x48 and 1 <= nsec <= 16 and h + total <= len(b) and 0x40 <= hdr < 0x1000:
                return h
        o = b.find(VERSION, o + 1)
    raise ValueError('no Granny header')


class File:
    def __init__(self, b):
        self.b = b; self.at = at = find(b); info = at + 0x20
        nsec = struct.unpack_from('>I', b, info + 0x10)[0]
        self.root_type = struct.unpack_from('>2I', b, info + 0x14); self.root = struct.unpack_from('>2I', b, info + 0x1c)
        self.sections = [struct.unpack_from('>11I', b, info + 0x48 + 44 * i) for i in range(nsec)]
        self.ptr = {}                                       # file offset of a pointer slot -> file offset it points to
        for fmt, off, size, _, _, _, _, fix, nfix, _, _ in self.sections:
            for k in range(nfix):
                src, sec, dst = struct.unpack_from('>3I', b, at + fix + 12 * k)
                self.ptr[at + off + src] = self.addr(sec, dst)

    def addr(self, sec, off):
        return self.at + self.sections[sec][1] + off

    def deref(self, o):
        """the target of the pointer stored at file offset o, or None for a null one"""
        return self.ptr.get(o)

    def string(self, o):
        if o is None:
            return None
        e = self.b.index(b'\0', o)
        return self.b[o:e].decode('latin-1')

    def members(self, t):
        """(type, name, reference type, array width) of each member of the type definition at t"""
        out = []
        while True:
            kind = struct.unpack_from('>I', self.b, t)[0]
            if kind == END:
                return out
            width = struct.unpack_from('>i', self.b, t + 12)[0]
            out.append((kind, self.string(self.deref(t + 4)), self.deref(t + 8), width)); t += 32

    def size(self, t):
        n = 0
        for kind, _, ref, width in self.members(t):
            one = self.size(ref) if kind == INLINE else SIZE.get(kind) or np.dtype(SCALAR[kind]).itemsize
            n += one * max(width, 1)
        return n

    def read(self, t, o, depth=0):
        """the object of type t at file offset o, as a dict. Arrays of plain structs whose members are all scalars come back as numpy record arrays"""
        out = {}
        for kind, name, ref, width in self.members(t):
            w = max(width, 1)
            if kind == INLINE:
                v = [self.read(ref, o + i * self.size(ref), depth) for i in range(w)]; out[name] = v[0] if width == 0 else v; o += self.size(ref) * w
                continue
            if kind in SCALAR:
                a = np.frombuffer(self.b, SCALAR[kind], w, o); out[name] = a[0] if width == 0 else a; o += a.nbytes
                continue
            if kind == TRANSFORM:
                out[name] = dict(flags=struct.unpack_from('>I', self.b, o)[0], position=np.frombuffer(self.b, '>f4', 3, o + 4), orientation=np.frombuffer(self.b, '>f4', 4, o + 16),
                                 scale_shear=np.frombuffer(self.b, '>f4', 9, o + 32).reshape(3, 3))
            elif kind == STRING:
                out[name] = self.string(self.deref(o))
            elif kind == REFERENCE:
                p = self.deref(o); out[name] = self.read(ref, p, depth + 1) if p is not None and depth < 12 else None
            elif kind == REF_ARRAY:
                n = struct.unpack_from('>I', self.b, o)[0]; p = self.deref(o + 4)
                out[name] = self.array(ref, p, n, depth + 1) if p is not None else []
            elif kind == ARRAY_OF_REFS:
                n = struct.unpack_from('>I', self.b, o)[0]; p = self.deref(o + 4)
                out[name] = [self.read(ref, self.deref(p + 4 * i), depth + 1) if p is not None and self.deref(p + 4 * i) is not None else None for i in range(n)] if depth < 12 else []
            elif kind == VARIANT:
                out[name] = (self.deref(o), self.deref(o + 4))      # type and object, left for the caller
            elif kind == VARIANT_ARRAY:
                n = struct.unpack_from('>I', self.b, o + 4)[0]; out[name] = (self.deref(o), n, self.deref(o + 8))
            o += SIZE.get(kind, 4)
        return out

    def array(self, t, p, n, depth):
        ms = self.members(t)
        if all(k in SCALAR for k, *_ in ms):
            dt = np.dtype([(name, SCALAR[k], (w,) if w else ()) for k, name, _, w in ms])
            return np.frombuffer(self.b, dt, n, p)
        sz = self.size(t)
        return [self.read(t, p + i * sz, depth) for i in range(n)]

    def tree(self):
        return self.read(self.addr(*self.root_type), self.addr(*self.root))

    def vertices(self, vd):
        """a VertexDatas entry's Vertices variant array as a numpy record array, its member names from the vertex type"""
        t, n, p = vd['Vertices']
        return self.array(t, p, n, 0), self.members(t)
