"""Xbox 360 shaders in FH1's .fxobj files: each shader's constant table, vertex input bindings and a disassembly of its Xenos ucode.
Instruction layouts follow Xenia's src/xenia/gpu/ucode.h. Layout of the container is in docs/formats.md.
python xenos.py ../x/shaders_track_H_DIFF_SPEC_AO_2_LM.fxobj [shader number]
Vertex fetches print blank, as the game patches their format, offset and stride in from the vertex declaration: the bindings say which
usage each fetch reads. Operands of the scalar constant ops (mulsc, addsc, subsc) aren't decoded exactly."""
import struct, sys

MAGIC = (0x102A1100, 0x102A1101)        # pixel, vertex
VEC = ('add mul max min seq sgt sge sne frc trunc floor mad cndeq cndge cndgt dp4 dp3 dp2add cube max4 setp_eq_push setp_ne_push setp_gt_push '
       'setp_ge_push kill_eq kill_gt kill_ge kill_ne dst maxa').split()
VARGS = [2, 2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 3, 3, 3, 3, 2, 2, 3, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
SCA = ('adds adds_prev muls muls_prev muls_prev2 maxs mins seqs sgts sges snes frcs truncs floors exp logc log rcpc rcpf rcp rsqc rsqf rsq maxas maxasf '
       'subs subs_prev setp_eq setp_ne setp_gt setp_ge setp_inv setp_pop setp_clr setp_rstr kills_eq kills_gt kills_ge kills_ne kills_one sqrt ? mulsc0 mulsc1 '
       'addsc0 addsc1 subsc0 subsc1 sin cos retain_prev').split()
USAGE = 'position blendweight blendindices normal psize texcoord tangent binormal tessfactor positiont color fog depth sample'.split()
EXEC_END = (2, 4, 8, 10, 14)


def bits(v, lo, n):
    return (v >> lo) & ((1 << n) - 1)


def cstr(b, o):
    return b[o:b.index(b'\0', o)].decode('latin1')


def constants(b, base):
    """A D3DX constant table at base: [(name, register set, register, count, default or None)]. Register set 2 is c, 3 is s, 0 is b."""
    _, _, _, n, info, _, _ = struct.unpack_from('>7I', b, base); out = []
    for k in range(n):
        nm, rs, ri, rc, _, _, dv = struct.unpack_from('>IHHHHII', b, base + info + 20 * k)
        out.append((cstr(b, base + nm), rs, ri, rc, struct.unpack_from('>4f', b, base + dv) if dv else None))
    return out


def shaders(b):
    """Every shader in the file: dict(at, kind, ucode, size, consts, bindings). The constant table follows its header at +0x28; the info block at
    header word 6 gives the ucode's offset past the header's word 1, and its size. Vertex shaders list their fetches there as
    (fetch instruction address, usage, usage index)."""
    out = []
    for at in range(0, len(b) - 48, 4):
        w = struct.unpack_from('>12I', b, at)
        if w[0] not in MAGIC:
            continue
        vs = w[0] == MAGIC[1]; off, size = struct.unpack_from('>II', b, at + w[6])
        binds = []
        if vs:      # the fetch count at +0x1c, then from +0x28 one word per fetch: low 12 bits its address, then usage and index
            o = at + w[6]; n = struct.unpack_from('>I', b, o + 0x1c)[0]
            for v in struct.unpack_from('>%dI' % min(n, 32), b, o + 0x28):
                if bits(v, 12, 4) < len(USAGE):
                    binds.append((bits(v, 0, 12), USAGE[bits(v, 12, 4)], bits(v, 16, 4)))
        out.append(dict(at=at, kind='vs' if vs else 'ps', ucode=at + w[1] + off, size=size, consts=constants(b, at + 0x28), bindings=binds))
    return out


def swz(s, n=4):
    """ALU source swizzle: each 2-bit field is an offset from its own component"""
    return ''.join('xyzw'[((s >> (2 * i)) + i) & 3] for i in range(n))


def fetch(d):
    op = bits(d[0], 0, 5); src = bits(d[0], 5, 6); dst = bits(d[0], 12, 6); ci = bits(d[0], 20, 5)
    ds = ''.join('xyzw01?_'[bits(bits(d[1], 0, 12), 3 * i, 3)] for i in range(4))
    if op == 0:
        return f"vfetch r{dst}.{ds}, r{src}, vf{ci * 3 + bits(d[0], 25, 2)}"
    if op == 1:
        return f"tfetch r{dst}.{ds}, r{src}.{''.join('xyzw'[bits(d[0], 26 + 2 * i, 2)] for i in range(3))}, tf{ci}"
    return f"fetch_op{op} r{dst}.{ds}, r{src}, {ci}"


def src_text(reg, temp, sw, neg):
    s = (f"r{reg & 0x3F}" if temp else f"c{reg}") + ('' if sw == 0 else '.' + swz(sw))
    return ('-' if neg else '') + (f"|{s}|" if temp and reg & 0x80 else s)


def alu(d, vs):
    a, b, c = d
    vdst, sdst, exp, vmask, smask, sop = bits(a, 0, 6), bits(a, 8, 6), bits(a, 15, 1), bits(a, 16, 4), bits(a, 20, 4), bits(a, 26, 6)
    srcs = [src_text(bits(c, 16, 8), bits(c, 31, 1), bits(b, 16, 8), bits(b, 26, 1)), src_text(bits(c, 8, 8), bits(c, 30, 1), bits(b, 8, 8), bits(b, 25, 1)),
            src_text(bits(c, 0, 8), bits(c, 29, 1), bits(b, 0, 8), bits(b, 24, 1))]
    vop = bits(c, 24, 5)

    def dest(r, mask):
        if exp:
            r = ('oPos' if r == 62 else 'oPts' if r == 63 else f"o{r}") if vs else (f"oC{r}" if r < 4 else 'oDepth' if r == 61 else f"exp{r}")
        else:
            r = f"r{r}"
        return r + '.' + ''.join('xyzw'[i] for i in range(4) if mask >> i & 1)
    out = []
    if vmask:
        out.append(f"{VEC[vop] if vop < len(VEC) else vop} {dest(vdst, vmask)}, {', '.join(srcs[:VARGS[vop] if vop < len(VARGS) else 3])}")
    if smask:
        out.append(f"{SCA[sop] if sop < len(SCA) else sop} {dest(sdst, smask)}, {srcs[2]}")
    return '   ||   '.join(out) or 'nop'


def disasm(b, sh):
    """Lines of text for one shader. Control flow comes first, two instructions per 3 dwords; exec addresses count 3-dword slots from the ucode start."""
    w = struct.unpack_from('>%dI' % (sh['size'] // 4), b, sh['ucode']); vs = sh['kind'] == 'vs'; lines = []; at = 0; first = None
    while at + 3 <= len(w) and (first is None or at < first * 3):
        d0, d1, d2 = w[at:at + 3]
        for c0, c1 in ((d0, d1 & 0xFFFF), ((d1 >> 16) | ((d2 << 16) & 0xFFFFFFFF), d2 >> 16)):
            op = bits(c1, 12, 4)
            if op in (1, 2, 3, 4, 7, 8, 9, 10, 13, 14):
                addr, cnt, seq = bits(c0, 0, 12), bits(c0, 12, 3), bits(c0, 16, 12); first = addr if first is None else min(first, addr)
                lines.append(f"-- exec{' (end)' if op in EXEC_END else ''} {addr}")
                for k in range(cnt):
                    ins = w[(addr + k) * 3:(addr + k) * 3 + 3]
                    lines.append(f"   {addr + k:3d} " + (fetch(ins) if seq >> (2 * k) & 1 else alu(ins, vs)))
                if op in EXEC_END:
                    return lines
            elif op == 12:
                lines.append(f"-- alloc {['none', 'position' if vs else 'colors', 'interpolators' if vs else 'colors', 'memory'][bits(c1, 9, 2)]}")
            elif op:
                lines.append(f"-- cf op {op}")
        at += 3
    return lines


if __name__ == '__main__':
    b = open(sys.argv[1], 'rb').read(); want = int(sys.argv[2]) if len(sys.argv) > 2 else None
    for i, sh in enumerate(shaders(b)):
        if want is not None and i != want:
            continue
        print(f"== shader {i}: {sh['kind']} at {sh['at']:#x}, ucode {sh['ucode']:#x} +{sh['size']:#x}")
        print('   ' + ' '.join(f"{n}={'b' if rs == 0 else 'c' if rs == 2 else 's'}{r}" for n, rs, r, _, _ in sh['consts']))
        if sh['bindings']:
            print('   fetches: ' + ' '.join(f"{a}:{u}{k}" for a, u, k in sh['bindings']))
        print('\n'.join(disasm(b, sh)))
