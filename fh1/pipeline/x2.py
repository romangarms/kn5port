"""print a generated function as PowerPC with strings and calls annotated: x2.py sub_XXXX [more]; x2.py --callers sub_XXXX"""
import re, sys, glob, pickle, os, struct
B = r'D:\Forza Horizon 1 Recomp\Forza Horizon 1 ReImagined (TU4)\build\forza-recomp\generated_tu4\default'
img = open('../image.bin', 'rb').read(); base = 0x82000000
def s_at(a):
    o = a - base
    if not 0 <= o < len(img) - 1:
        return None
    e = o
    while e < len(img) and e - o < 100 and 32 <= img[e] < 127:
        e += 1
    return img[o:e].decode() if e - o >= 3 else None
idx = '../fnidx.pkl'
if os.path.exists(idx):
    I = pickle.load(open(idx, 'rb'))
else:
    I = {}
    for f in glob.glob(B + r'\forza_recomp.*.cpp'):
        off = 0
        with open(f, 'rb') as h:
            for line in h:
                if line.startswith(b'DEFINE_REX_FUNC('):
                    I[line[16:line.index(b')')].decode()] = (f, off)
                off += len(line)
    pickle.dump(I, open(idx, 'wb'))
def body(n):
    f, o = I[n]; out = []
    with open(f, 'rb') as h:
        h.seek(o); first = True
        for line in h:
            if not first and line.startswith(b'DEFINE_REX_FUNC('):
                break
            first = False; out.append(line.decode(errors='replace'))
    return out
def show(n, limit=400):
    hi = {}; k = 0; print('=====', n, len(body(n)))
    for l in body(n):
        m = re.match(r'\s*// (.*)', l)
        if m:
            t = m.group(1); note = ''
            a = re.match(r'lis r(\d+),(-?\d+)', t)
            if a:
                hi[a.group(1)] = int(a.group(2)) * 65536
            b = re.match(r'(addi|lwz|lfs|stw) r?f?(\d+),?(-?\d+)?\(?r?(\d+)?', t)
            c = re.match(r'addi r(\d+),r(\d+),(-?\d+)', t)
            if c and c.group(2) in hi:
                ad = (hi[c.group(2)] + int(c.group(3))) & 0xffffffff; st = s_at(ad); note = f'   ; {ad:08X} {st!r}' if st else f'   ; {ad:08X}'
            if t.startswith(('stwu', 'mflr', 'mtlr', 'std ', 'ld ', 'blr', 'bcctr')) and not t.startswith('blr'):
                continue
            print('  ' + t + note); k += 1
            if k > limit:
                print('...'); break
        elif re.match(r'loc_', l):
            print(l.strip())
if sys.argv[1] == '--callers':
    pat = (sys.argv[2] + '(').encode(); r = []
    for f in sorted({v[0] for v in I.values()}):
        d = open(f, 'rb').read()
        for m in re.finditer(re.escape(pat), d):
            j = d.rfind(b'DEFINE_REX_FUNC(', 0, m.start()); r.append(d[j + 16:d.index(b')', j)].decode())
    print(sorted(set(r)))
else:
    for n in sys.argv[1:]:
        show(n)
