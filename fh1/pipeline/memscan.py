"""Read-only: while Xenia runs, keep collecting FH1 object placements (matrix + draw id at +132) around Red Rock."""
import ctypes, ctypes.wintypes as W, struct, collections, pickle, sys, time, hashlib, os
import numpy as np
k = ctypes.WinDLL('kernel32', use_last_error=True)
class MBI(ctypes.Structure):
    _fields_ = [('Base', ctypes.c_void_p), ('AllocBase', ctypes.c_void_p), ('AllocProt', W.DWORD), ('Part', W.WORD),
                ('Size', ctypes.c_size_t), ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD)]
k.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]; k.VirtualQueryEx.restype = ctypes.c_size_t
k.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k.OpenProcess.restype = W.HANDLE
pid = int(sys.argv[1]); passes = int(sys.argv[2]); h = k.OpenProcess(0x0400 | 0x0010, False, pid)
mt = np.load('../mt.npy'); T = mt['t'].astype(int); F = pickle.load(open('../cat2.pkl', 'rb')); ext = {}
for c in F:
    no = int(c['f'].split('.')[1]); ext[no] = max(ext.get(no, 0), abs(c['mn'][0]), abs(c['mx'][0]), abs(c['mn'][2]), abs(c['mx'][2]))
local = {no for no, e in ext.items() if e < 120}
X0, X1, Z0, Z1 = -8200., -3800., 2000., 6500.
P = '../memobj_all.pkl'; ALL = pickle.load(open(P, 'rb')) if os.path.exists(P) else {}
RP = '../memrec.pkl'; REC = pickle.load(open(RP, 'rb')) if os.path.exists(RP) else {}
CH = 1 << 26
def regions():
    addr = 0; out = []; m = MBI()
    while addr < 0x7fffffff0000:
        if not k.VirtualQueryEx(h, addr, ctypes.byref(m), ctypes.sizeof(m)):
            break
        a = m.Base or 0
        if m.State == 0x1000 and not (m.Protect & 0x101) and 0x10000 <= m.Size <= 1 << 33:
            out.append((a, m.Size))
        addr = a + m.Size
    return out
for p_ in range(passes):
    t0 = time.time(); new = 0; seen = set()
    rs = regions()
    if not rs:
        print('xenia gone'); break
    for a, sz in rs:
        for o in range(0, sz, CH):
            n = min(CH + 256, sz - o); buf = ctypes.create_string_buffer(n); got = ctypes.c_size_t()
            if not k.ReadProcessMemory(h, a + o, buf, n, ctypes.byref(got)) or got.value < 256:
                continue
            raw = buf.raw[:got.value & ~3]; d = hashlib.md5(raw[:65536]).digest()
            if (d, len(raw)) in seen:
                continue
            seen.add((d, len(raw)))
            f = np.frombuffer(raw, '>f4'); u = np.frombuffer(raw, '>u4')
            with np.errstate(all='ignore'):
                i = np.where(np.abs(f - 1) < 1e-4)[0]; i = i[(i >= 15) & (i + 20 < len(f))]
                x = f[i - 3]; y = f[i - 2]; z = -f[i - 1]
                ok = (x > X0) & (x < X1) & (y > -60) & (y < 600) & (z > Z0) & (z < Z1)
            for j in i[ok]:
                s = j - 15; v = int(u[s + 33])              # matrix start + 132 bytes
                if not 256 <= v < len(T) or int(T[v]) not in local:
                    continue
                with np.errstate(all='ignore'):
                    R = f[s:s + 12].reshape(3, 4)[:, :3].astype('f8'); n_ = np.linalg.norm(R, axis=1)
                if not np.isfinite(n_).all() or n_.min() < .2 or n_.max() > 6 or np.abs(n_ - n_.mean()).max() > .05 * n_.mean():
                    continue
                key = (int(T[v]), round(float(f[j - 3])), round(float(f[j - 1])))
                r_ = REC.setdefault(key, {'id': v, 'n': 0, 'raw': []}); r_['n'] += 1
                if len(r_['raw']) < 6:
                    r_['raw'].append(bytes(raw[max(0, 4 * s - 96):4 * s + 256]))
                if key not in ALL:
                    ALL[key] = (float(f[j - 3]), float(f[j - 2]), float(f[j - 1]), R.round(5).tolist()); new += 1
    pickle.dump(ALL, open(P, 'wb')); pickle.dump(REC, open(RP, 'wb'))
    print(time.strftime('%H:%M:%S'), 'pass', p_, 'new', new, 'total', len(ALL), f'{time.time() - t0:.0f}s', flush=True)
    time.sleep(20)
