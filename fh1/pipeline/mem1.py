"""Read-only scan of the running Xenia process for FH1 world transforms."""
import ctypes, ctypes.wintypes as W, struct, collections, pickle, sys, hashlib
import numpy as np
k = ctypes.WinDLL('kernel32', use_last_error=True)
class MBI(ctypes.Structure):
    _fields_ = [('Base', ctypes.c_void_p), ('AllocBase', ctypes.c_void_p), ('AllocProt', W.DWORD), ('Part', W.WORD),
                ('Size', ctypes.c_size_t), ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD)]
k.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]; k.VirtualQueryEx.restype = ctypes.c_size_t
k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k.OpenProcess.restype = W.HANDLE
pid = int(sys.argv[1]); h = k.OpenProcess(0x0400 | 0x0010, False, pid)       # QUERY_INFORMATION | VM_READ only
print('handle', bool(h))
addr = 0; regs = []; m = MBI()
while addr < 0x7fffffff0000:
    if not k.VirtualQueryEx(h, addr, ctypes.byref(m), ctypes.sizeof(m)):
        break
    a = m.Base or 0
    if m.State == 0x1000 and not (m.Protect & 0x101) and m.Size >= 0x10000:
        regs.append((a, m.Size, m.Type))
    addr = a + m.Size
print(len(regs), 'regions', sum(r[1] for r in regs) >> 20, 'MB', collections.Counter(hex(r[2]) for r in regs))
X0, X1, Z0, Z1 = -8200., -3800., 2000., 6500.
found = {}; seen = set(); total = 0
CH = 1 << 26
for a, sz, ty in regs:
    if sz > 1 << 33:
        continue
    for o in range(0, sz, CH):
        n = min(CH + 64, sz - o); buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
        if not k.ReadProcessMemory(h, a + o, buf, n, ctypes.byref(got)) or got.value < 64:
            continue
        raw = buf.raw[:got.value & ~3]
        d = hashlib.md5(raw[:65536]).digest()
        if (d, len(raw)) in seen:
            continue               # guest memory is mapped several times
        seen.add((d, len(raw))); total += len(raw)
        f = np.frombuffer(raw, '>f4')
        with np.errstate(all='ignore'):
            w1 = np.abs(f - 1) < 1e-4
            i = np.where(w1)[0]
            i = i[(i >= 15) & (i + 10 < len(f))]
            x = f[i - 3]; y = f[i - 2]; z = f[i - 1]
            ok = (x > X0) & (x < X1) & (y > -60) & (y < 600) & (((z > Z0) & (z < Z1)) | ((-z > Z0) & (-z < Z1)))
        for j in i[ok]:
            R = f[j - 15:j - 3].reshape(3, 4)[:, :3].astype('f8')
            n_ = np.linalg.norm(R, axis=1)
            if n_.min() < .2 or n_.max() > 6 or np.abs(n_ - n_.mean()).max() > .05 * n_.mean():
                continue
            found[(a + o + 4 * int(j - 15))] = (float(f[j - 3]), float(f[j - 2]), float(f[j - 1]), R.round(4).tolist())
print('scanned', total >> 20, 'MB', 'matrices', len(found))
pickle.dump(found, open('../memmat.pkl', 'wb'))
ad = np.array(sorted(found)); df = np.diff(ad)
print('stride', collections.Counter(df.tolist()).most_common(10))
uniq = {(round(v[0], 1), round(v[1], 1), round(v[2], 1)) for v in found.values()}; print('unique positions', len(uniq), list(uniq)[:8])
