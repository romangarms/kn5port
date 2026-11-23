"""FM4 code reading over default.pe (fm4xex.py): string references and PowerPC disassembly (capstone), base 0x82000000.
python fm4code.py refs <string>             functions referencing that string (lis/addi pairs), with their address
python fm4code.py dis <hex addr> [n]        n instructions from addr (default 80), with string operands annotated"""
import re
import struct
import sys

import capstone

BASE = 0x82000000
X = open(__file__.rsplit('\\', 1)[0].rsplit('/', 1)[0] + '/default.pe', 'rb').read() if __name__ != '__main__' or True else None
TEXT = (0x2a0000, 0x2a0000 + 0xaa101c)
CS = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)


def cstr(addr):
    o = addr - BASE
    if not 0 <= o < len(X): return None
    e = X.find(b'\0', o, o + 200)
    s = X[o:e]
    return s.decode('latin1') if e > o and all(32 <= c < 127 for c in s) else None


def refs(addr):
    """instruction addresses forming addr by lis rX, hi ; addi/ori/lwz/lfs rY, rX, lo (within 64 instructions)"""
    hi = (addr + 0x8000) >> 16 & 0xffff; lo = addr & 0xffff; out = []
    w = struct.unpack_from('>%dI' % ((TEXT[1] - TEXT[0]) // 4), X, TEXT[0])
    lis = {}
    for i, v in enumerate(w):
        op = v >> 26
        if op == 15 and (v >> 16 & 31) == 0 and (v & 0xffff) == hi: lis[v >> 21 & 31] = i
        elif op in (14, 32, 48, 24) and (v & 0xffff) == lo:
            ra = v >> 16 & 31
            if ra in lis and i - lis[ra] < 64: out.append(BASE + TEXT[0] + 4 * i)
    return out


def dis(addr, n=80):
    o = addr - BASE; lines = []; regs = {}
    for k in range(n):
        ins = next(CS.disasm(X[o + 4 * k:o + 4 * k + 4], addr + 4 * k), None)
        if ins is None:
            lines.append(f'{addr + 4 * k:08x}  .long    0x{X[o + 4 * k:o + 4 * k + 4].hex()}'); continue
        note = ''
        ops = ins.op_str.replace(' ', '').split(',')
        if ins.mnemonic == 'lis' and len(ops) == 2:
            regs[ops[0]] = int(ops[1], 0) << 16
        elif ins.mnemonic in ('addi', 'lfs', 'lwz', 'stw', 'stfs', 'lfd') and len(ops) >= 2:
            m = re.match(r'(-?(?:0x)?[0-9a-f]+)\((r\d+)\)', ops[1]) if '(' in ops[1] else None
            base, off = (m.group(2), int(m.group(1), 0)) if m else (ops[1], int(ops[2], 0) if len(ops) > 2 else 0)
            if base in regs:
                a = (regs[base] + (off if off < 0x8000 else off - 0x10000)) & 0xffffffff
                s = cstr(a) if ins.mnemonic == 'addi' else None
                note = f'  ; {a:08x}' + (f' "{s}"' if s else '')
                if ins.mnemonic in ('lfs',) and 0 <= a - BASE < len(X): note += f' = {struct.unpack_from(">f", X, a - BASE)[0]:g}'
            if ins.mnemonic == 'addi' and ops[0] != ops[1]: regs.pop(ops[0], None)
        lines.append(f'{ins.address:08x}  {ins.mnemonic:8s} {ins.op_str}{note}')
    return '\n'.join(lines)


if __name__ == '__main__':
    if sys.argv[1] == 'refs':
        s = sys.argv[2].encode()
        for m in re.finditer(re.escape(s) + b'\0', X):
            a = BASE + m.start(); print(f'"{sys.argv[2]}" at {a:08x}:', ' '.join(f'{r:08x}' for r in refs(a)))
    else:
        print(dis(int(sys.argv[2], 16), int(sys.argv[3]) if len(sys.argv) > 3 else 80))
