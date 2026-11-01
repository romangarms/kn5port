import re, struct, glob, pickle, collections, os, time
B = r'D:\Forza Horizon 1 Recomp\Forza Horizon 1 ReImagined (TU4)\build'
x = open(B + r'\tu\default.xex', 'rb').read()
pe = struct.unpack_from('>I', x, 8)[0]; print('pe offset', hex(pe), x[pe:pe + 4], len(x))
# optional header: find image base and basic file format
n = struct.unpack_from('>I', x, 20)[0]
for i in range(n):
    k, v = struct.unpack_from('>II', x, 24 + i * 8)
    if k >> 8 in (0x102, 0x3):
        print(hex(k), hex(v))
    if k == 0x00010201:
        print('base', hex(v))
    if k >> 8 == 0x3:
        print('ffi', x[v:v + 12].hex())
i = x.find(b'CProceduralGeometryWorker'); print('string file offset', hex(i))
img = x[pe:]; base = 0x82000000
def s_at(a):
    o = a - base
    if not 0 <= o < len(img) - 1:
        return None
    e = o
    while e < len(img) and e - o < 120 and 32 <= img[e] < 127:
        e += 1
    return img[o:e].decode() if e - o >= 4 and img[e:e + 1] == b'\0' else None
print('sample', s_at(base + i - pe))
t0 = time.time(); xref = collections.defaultdict(list)
LIS = re.compile(r'// lis r(\d+),(-?\d+)'); ADD = re.compile(r'// addi r(\d+),r(\d+),(-?\d+)'); FN = re.compile(r'DEFINE_REX_FUNC\((sub_[0-9A-F]+)\)')
for f in glob.glob(B + r'\forza-recomp\generated_tu4\default\forza_recomp.*.cpp'):
    cur = ''; hi = {}
    for line in open(f, errors='replace'):
        if line.startswith('DEFINE'):
            m = FN.search(line); cur = m.group(1) if m else cur; hi = {}
        elif '// lis' in line:
            m = LIS.search(line); hi[m.group(1)] = int(m.group(2)) * 65536
        elif '// addi' in line:
            m = ADD.search(line)
            if m and m.group(2) in hi:
                a = (hi[m.group(2)] + int(m.group(3))) & 0xffffffff
                t = s_at(a)
                if t:
                    xref[t].append(cur)
print(len(xref), 'strings referenced', f'{time.time() - t0:.0f}s')
pickle.dump(dict(xref), open('../xref.pkl', 'wb'))
open('../image.bin', 'wb').write(img)
for t, v in xref.items():
    if re.search(r'proc|ungroup|layer|visib|activat|barrier|freeroam|route|prop|gameobj|pvs|pgeo|geometry|\bset\b', t, re.I) and len(t) < 90:
        print(len(v), v[0], repr(t))
