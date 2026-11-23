"""Read-only access to FM4's memory while it runs in Xenia (docs/fm4.md, Live memory). Never writes.
Xenia maps guest memory contiguously at a host base; the base is found from a string at a known image address.
python fm4mem.py base                        host base of guest memory
python fm4mem.py find <hex bytes>            guest addresses of a byte pattern
python fm4mem.py floats <guest hex> [n]      n big-endian floats from a guest address"""
import ctypes
import ctypes.wintypes as W
import struct
import subprocess
import sys

k = ctypes.WinDLL('kernel32', use_last_error=True)


class MBI(ctypes.Structure):
    _fields_ = [('Base', ctypes.c_void_p), ('AllocBase', ctypes.c_void_p), ('AllocProt', W.DWORD), ('Part', W.WORD),
                ('Size', ctypes.c_size_t), ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD)]


k.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]; k.VirtualQueryEx.restype = ctypes.c_size_t
k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k.OpenProcess.restype = W.HANDLE
ANCHOR = (0x821528A4, b'SkyboxHeightOffset\0')       # a TrackSettings tag name in FM4's image (fm4code.py refs)


def pid():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq xenia_canary.exe', '/FO', 'CSV', '/NH'], capture_output=True, text=True).stdout
    return int(out.split(',')[1].strip('"'))


H = k.OpenProcess(0x0400 | 0x0010, False, pid())        # query + VM read only


def read(addr, n):
    buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
    if not k.ReadProcessMemory(H, addr, buf, n, ctypes.byref(got)): return None
    return buf.raw[:got.value]


def regions(lo=0, hi=0x7fffffff0000):
    addr = lo; m = MBI()
    while addr < hi and k.VirtualQueryEx(H, addr, ctypes.byref(m), ctypes.sizeof(m)):
        a = m.Base or 0
        if m.State == 0x1000 and not (m.Protect & 0x101): yield a, m.Size
        addr = a + m.Size


def search(pat, lo=0, hi=0x7fffffff0000, chunk=1 << 26):
    for a, sz in regions(lo, hi):
        for o in range(0, sz, chunk):
            b = read(a + o, min(chunk + len(pat), sz - o))
            if not b: continue
            i = b.find(pat)
            while i >= 0:
                yield a + o + i; i = b.find(pat, i + 1)


_BASE = None


def base():
    global _BASE
    if _BASE is None:
        for h in search(ANCHOR[1]):
            _BASE = (h - ANCHOR[0]) & ~0xffffff; break      # the running image may be a title-update build, its strings shifted
    return _BASE


def guest(addr, n):
    return read(base() + addr, n)


def find(pat, lo=0x40000000, hi=0x100000000):
    b = base(); return [h - b for h in search(pat, b + lo, b + hi)]


def floats(addr, n=4):
    d = guest(addr, 4 * n); return struct.unpack('>%df' % (len(d) // 4), d)


def u32(addr, n=1):
    d = guest(addr, 4 * n); return struct.unpack('>%dI' % (len(d) // 4), d)


if __name__ == '__main__':
    if sys.argv[1] == 'base': print(hex(base()))
    elif sys.argv[1] == 'find': print([hex(a) for a in find(bytes.fromhex(sys.argv[2]))])
    else: print(floats(int(sys.argv[2], 16), int(sys.argv[3]) if len(sys.argv) > 3 else 4))
