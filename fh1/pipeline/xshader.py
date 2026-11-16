"""Xbox 360 shaders compiled into FH1's default.xex: lists them, or disassembles one with its constant names.
python xshader.py                 lists every shader container (name from its .updb path)
python xshader.py CloseCloud_PS   disassembles that shader: constant table, literal constants, control flow, ALU and fetch instructions
Bit layouts are Xenia's ucode.h (in the ReImagined recomp's rexglue SDK, include/rex/graphics/format/ucode.h). Read-only; needs the uncompressed TU4 xex."""
import re, struct, sys

XEX = r'D:\Forza Horizon 1 Recomp\Forza Horizon 1 ReImagined (TU4)\build\tu\default.xex'
MAGIC = re.compile(rb'\x10\x2a\x11[\x00\x01]')
VEC = ['add', 'mul', 'max', 'min', 'seq', 'sgt', 'sge', 'sne', 'frc', 'trunc', 'floor', 'mad', 'cndeq', 'cndge', 'cndgt', 'dp4', 'dp3', 'dp2add', 'cube',
       'max4', 'setp_eq_push', 'setp_ne_push', 'setp_gt_push', 'setp_ge_push', 'kill_eq', 'kill_gt', 'kill_ge', 'kill_ne', 'dst', 'maxa']
VEC_ARGS = {'frc': 1, 'trunc': 1, 'floor': 1, 'max4': 1, 'mad': 3, 'cndeq': 3, 'cndge': 3, 'cndgt': 3, 'dp2add': 3}
SCA = {0: 'adds', 1: 'adds_prev', 2: 'muls', 3: 'muls_prev', 4: 'muls_prev2', 5: 'maxs', 6: 'mins', 7: 'seqs', 8: 'sgts', 9: 'sges', 10: 'snes', 11: 'frcs',
       12: 'truncs', 13: 'floors', 14: 'exp', 15: 'logc', 16: 'log', 17: 'rcpc', 18: 'rcpf', 19: 'rcp', 20: 'rsqc', 21: 'rsqf', 22: 'rsq', 23: 'maxas',
       24: 'maxasf', 25: 'subs', 26: 'subs_prev', 27: 'setp_eq', 28: 'setp_ne', 29: 'setp_gt', 30: 'setp_ge', 31: 'setp_inv', 32: 'setp_pop', 33: 'setp_clr',
       34: 'setp_rstr', 35: 'kills_eq', 36: 'kills_gt', 37: 'kills_ge', 38: 'kills_ne', 39: 'kills_one', 40: 'sqrt', 42: 'mulsc', 43: 'mulsc', 44: 'addsc',
       45: 'addsc', 46: 'subsc', 47: 'subsc', 48: 'sin', 49: 'cos', 50: 'retain_prev'}
# Scalar operands: 0 none, 1 one (a), 2 two (a, b) of src3, 'c' constant .a and temp .b
# ('_prev' ops add/multiply/subtract the previous scalar result, ps, to their operand)
SCA_ARGS = {**{k: 2 for k in (0, 2, 4, 5, 6, 23, 24, 25)}, **{k: 0 for k in (33, 50)}, **{k: 'c' for k in range(42, 48)}}
CF = ['nop', 'exec', 'exece', 'cexec', 'cexece', 'cexec_pred', 'cexece_pred', 'loop_start', 'loop_end', 'ccall', 'ret', 'cjmp', 'alloc', 'cexec_pred_clean',
      'cexece_pred_clean', 'mark_vs_fetch_done']
XYZW = 'xyzw'


def u32(b, o):
    return struct.unpack_from('>I', b, o)[0]


def containers(x):
    """(offset, name, blob) for every shader container. A container is its virtual part then its physical part (the microcode)."""
    out = []
    for m in MAGIC.finditer(x):
        o = m.start(); virt, phys = u32(x, o + 4), u32(x, o + 8)
        if not (0x24 <= virt < 0x40000 and phys < 0x40000):
            continue
        blob = x[o:o + virt + phys]; dbg = u32(blob, 12)
        p = re.search(rb'([A-Za-z0-9_]+)\.updb', blob[dbg:dbg + 0x200])
        out.append((o, p.group(1).decode() if p else '?', blob))
    return out


def ctab(blob):
    """Constant table: {(set, register): name}, set 'c' float, 'b' bool, 'i' int, 's' sampler. Also the target, eg ps_3_0."""
    base = u32(blob, 16) + 4
    n, info = u32(blob, base + 12), u32(blob, base + 16)
    sets = 'bics'; out = {}
    for i in range(n):
        e = base + info + 20 * i
        name = blob[base + u32(blob, e):].split(b'\0')[0].decode()
        rs, ri, rc = struct.unpack_from('>HHH', blob, e + 4)
        for k in range(rc):
            out[(sets[rs], ri + k)] = name if rc == 1 else f'{name}[{k}]'
    return out, blob[base + u32(blob, base + 24):].split(b'\0')[0].decode()


def swz(s, n=4):
    """ALU source swizzle: component i reads ((s >> 2i) + i) & 3."""
    return ''.join(XYZW[((s >> (2 * i)) + i) & 3] for i in range(n))


class Dis:
    def __init__(self, blob, ps):
        self.blob, self.ps = blob, ps
        self.names, self.target = ctab(blob)
        virt = u32(blob, 4); sh = u32(blob, 24); off, size = u32(blob, sh), u32(blob, sh + 4)
        self.code = struct.unpack(f'>{size // 4}I', blob[virt + off:virt + off + size])
        # Literal constants (def c#): the definition table at header field 5 lists (register, float count, offset into the physical part).
        # Registers count pixel constants from 256
        self.defs = {}
        d = u32(blob, 20)
        if d:
            for k in range(u32(blob, d + 16) // 8):
                reg, n, po = struct.unpack_from('>HHI', blob, d + 20 + 8 * k)
                if not n:
                    break
                vals = struct.unpack_from(f'>{n}f', blob, virt + po)
                for j in range(n // 4):
                    self.defs[reg % 256 + j] = vals[4 * j:4 * j + 4]

    def cname(self, r):
        n = self.names.get(('c', r))
        if r in self.defs:
            n = 'def(' + ', '.join(f'{v:g}' for v in self.defs[r]) + ')'
        return f'c{r}' + (f'<{n}>' if n else '')

    def src(self, d, i):
        reg = [None, d[2] >> 16 & 0xff, d[2] >> 8 & 0xff, d[2] & 0xff][i]
        temp = d[2] >> [None, 31, 30, 29][i] & 1
        neg = d[1] >> [None, 26, 25, 24][i] & 1
        sw = [None, d[1] >> 16 & 0xff, d[1] >> 8 & 0xff, d[1] & 0xff][i]
        if temp:
            s = f'r{reg & 0x3f}' + ('[aL]' if reg & 0x40 else ''); ab = reg & 0x80
        else:
            rel = d[1] >> [None, 31, 30, 30][i] & 1 if i == 1 else None
            s = self.cname(reg); ab = d[0] >> 7 & 1
        return reg, temp, neg, sw, s, ab

    def alu(self, d):
        out = []
        pred = ('(p) ' if d[1] >> 27 & 1 else '(!p) ') if d[1] >> 28 & 1 else ''
        exp = d[0] >> 15 & 1
        vop = VEC[d[2] >> 24 & 0x1f] if (d[2] >> 24 & 0x1f) < len(VEC) else f'vop{d[2] >> 24 & 0x1f}'
        vmask, smask = d[0] >> 16 & 0xf, d[0] >> 20 & 0xf
        vdst, sdst = d[0] & 0x3f, d[0] >> 8 & 0x3f
        sop = d[0] >> 26 & 0x3f

        def dst(r, mask, export):
            if export:
                n = (['oC0', 'oC1', 'oC2', 'oC3'] + [''] * 57 + ['oDepth'])[r] if self.ps and r < 62 else (
                    f'o{r}' if r < 16 else {62: 'oPos', 63: 'oPts', 32: 'eA'}.get(r, f'eM{r - 33}'))
            else:
                n = f'r{r}'
            return n + '.' + ''.join(XYZW[i] if mask >> i & 1 else '_' for i in range(4))

        def fmt(s, n, comps=None):
            reg, temp, neg, sw, name, ab = s
            t = (f'|{name}|' if ab else name) + '.' + (comps if comps else swz(sw, n))
            return ('-' if neg else '') + t

        vm = vmask & ~smask if exp else vmask
        if vm or not exp and vop not in ('max',) or vmask:
            if vm or vop.startswith(('kill', 'setp')):
                args = [fmt(self.src(d, i), 4) for i in range(1, VEC_ARGS.get(vop, 2) + 1)]
                out.append(f'{pred}{vop}{"_sat" if d[0] >> 24 & 1 else ""} {dst(vdst, vm, exp)}, ' + ', '.join(args))
        sm = smask & ~vmask if exp else smask
        if sop != 50 or sm:
            kind = SCA_ARGS.get(sop, 1); s3 = self.src(d, 3); sw = s3[3]
            a = XYZW[((sw >> 6) + 3) & 3]; b = XYZW[(sw & 3) & 3]
            if kind == 'c':
                temp = (sop & 1) | (d[2] >> 29 & 1) << 1 | (sw & 0x3c)
                args = [fmt(s3[:1] + (0,) + s3[2:], 1, a), f'r{temp}.{b}']
                args[0] = ('-' if s3[2] else '') + self.cname(s3[0]) + '.' + a
            elif kind == 2:
                args = [fmt(s3, 1, a + b)]
            elif kind == 1:
                args = [fmt(s3, 1, a)]
            else:
                args = []
            if sm or sop in range(27, 40):
                out.append(f'{pred}{SCA.get(sop, f"sop{sop}")}{"_sat" if d[0] >> 25 & 1 else ""} {dst(sdst, sm, exp)}' + (', ' + ', '.join(args) if args else ''))
            else:
                out.append(f'{pred}{SCA.get(sop, f"sop{sop}")} ps' + (', ' + ', '.join(args) if args else ''))
        if exp and vmask & smask:
            out.append(f'  (export const 1.0 to {dst(vdst, vmask & smask, 1)})')
        return out

    def fetch(self, d):
        op = d[0] & 0x1f
        pred = ('(p) ' if d[2] >> 31 & 1 else '(!p) ') if d[1] >> 31 & 1 else ''
        src, dst = d[0] >> 5 & 0x3f, d[0] >> 12 & 0x3f
        dsw = ''.join('xyzw01?_'[d[1] >> (3 * i) & 7] for i in range(4))
        if op == 0:
            ci = (d[0] >> 20 & 0x1f) * 3 + (d[0] >> 25 & 3)
            fmt = d[1] >> 16 & 0x3f; stride = d[2] & 0xff; off = (d[2] >> 8) & 0x7fffff
            return f'{pred}vfetch r{dst}.{dsw}, r{src}.{XYZW[d[0] >> 30 & 3]}, vf{ci} (format {fmt}, stride {stride}, offset {off}){" mini" if d[1] >> 30 & 1 else ""}'
        ci = d[0] >> 20 & 0x1f
        ssw = ''.join(XYZW[(d[0] >> (26 + 2 * i)) & 3] for i in range(3))
        name = self.names.get(('s', ci), '')
        dim = ['1D', '2D', '3D', 'Cube'][d[2] >> 14 & 3]
        lod = ' reg_lod' if d[1] >> 29 & 1 else ''
        ops = {1: 'tfetch', 16: 'getTextureBorderColorFrac', 17: 'getTextureComputedLod', 18: 'getTextureGradients', 19: 'getTextureWeights',
               24: 'setTextureLod', 25: 'setTextureGradientsHorz', 26: 'setTextureGradientsVert'}
        return f'{pred}{ops.get(op, f"fop{op}")}{dim} r{dst}.{dsw}, r{src}.{ssw}, tf{ci}<{name}>{lod}'

    def run(self):
        lines = [f'// {self.target}']
        for (s, r), n in sorted(self.names.items()):
            lines.append(f'//   {s}{r}: {n}')
        code = self.code; cfs = []; i = 0; first = None
        while True:
            w = code[3 * i:3 * i + 3]
            if len(w) < 3:
                break
            for d0, d1 in ((w[0], w[1] & 0xffff), (w[1] >> 16 | (w[2] << 16) & 0xffffffff, w[2] >> 16)):
                cfs.append((d0, d1))
            i += 1
            for d0, d1 in cfs[-2:]:
                op = d1 >> 12 & 0xf
                if 1 <= op <= 6 or op in (13, 14):
                    a = d0 & 0xfff
                    first = a if first is None else min(first, a)
            if first is not None and i >= first:
                break
        for k, (d0, d1) in enumerate(cfs):
            op = d1 >> 12 & 0xf; name = CF[op]
            if 1 <= op <= 6 or op in (13, 14):
                addr, cnt, seq = d0 & 0xfff, d0 >> 12 & 7, d0 >> 16 & 0xfff
                cond = ''
                if op in (3, 4):
                    cond = f' {"" if d1 >> 10 & 1 else "!"}b{d1 >> 2 & 0xff}'
                elif op in (5, 6, 13, 14):
                    cond = f' {"" if d1 >> 10 & 1 else "!"}p'
                lines.append(f'  {name}{cond}  (addr {addr}, count {cnt})')
                for j in range(cnt):
                    d = code[3 * (addr + j):3 * (addr + j) + 3]
                    if seq >> (2 * j) & 1:
                        lines.append(f'    {addr + j:3d}  ' + self.fetch(d))
                    else:
                        for t in self.alu(d):
                            lines.append(f'    {addr + j:3d}  ' + t)
                if op in (2, 4, 6, 14):
                    break
            elif op == 12:
                lines.append(f'  alloc {["none", "position/colors", "interpolators", "memory"][d1 >> 8 & 3]} {d0 & 7}')
            elif op == 0:
                continue
            else:
                lines.append(f'  {name} {d0:08x} {d1:04x}')
        return '\n'.join(lines)


def literals(blob):
    """Literal constants the compiler defined (def c#): the definition table at header field 5, values in the physical part."""
    o = u32(blob, 20)
    return blob[o:o + 0x40].hex(' ') if o else ''


if __name__ == '__main__':
    x = open(XEX, 'rb').read(); cs = containers(x)
    if len(sys.argv) == 1:
        for o, n, b in cs:
            print(f'{o:08x} {len(b):6d} {n}')
    else:
        for o, n, b in cs:
            if n == sys.argv[1]:
                print(f'// {n} at {o:08x}')
                print(Dis(b, n.endswith('PS')).run())
                if '--defs' in sys.argv:
                    print('// definition table:', literals(b))
