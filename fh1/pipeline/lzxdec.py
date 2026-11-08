"""XMem LZX decoding for the game's zips: lzx.dll when it has been built (see the README), else this numba port of lzx.c.

The port follows lzx.c line by line, so the two give the same bytes; uint32 C arithmetic is kept with & M32.
decode(data, ulen, wbits=17) -> bytes, or IOError.
"""
import ctypes, os
import numpy as np
from numba import njit

_HERE = os.path.dirname(os.path.abspath(__file__))
M32 = 0xFFFFFFFF
MAINSZ = 656
LENSZ = 249


# bit reader: st = [p, buf, n]
@njit(cache=True)
def _fill(inp, e, st):
    while st[2] <= 16:
        p = st[0]; w = 0
        if p + 1 < e:
            w = inp[p] | (inp[p + 1] << 8)
        st[0] = p + 2
        st[1] = (st[1] | (w << (16 - st[2]))) & M32
        st[2] += 16


@njit(cache=True)
def _peek(inp, e, st, k):
    _fill(inp, e, st)
    return st[1] >> (32 - k) if k else 0


@njit(cache=True)
def _drop(st, k):
    st[1] = (st[1] << k) & M32
    st[2] -= k


@njit(cache=True)
def _rd16(inp, e, st, k):
    v = _peek(inp, e, st, k)
    _drop(st, k)
    return v


@njit(cache=True)
def _rd(inp, e, st, k):
    if k > 16:
        h = _rd16(inp, e, st, k - 16)
        return ((h << 16) | _rd16(inp, e, st, 16)) & M32
    return _rd16(inp, e, st, k)


# tree: t[0:18] first, t[18:36] cnt, t[36:54] idx, t[54:] sym
@njit(cache=True)
def _build(t, ln, n):
    c = np.zeros(17, np.int64)
    for i in range(n):
        c[ln[i]] += 1
    c[0] = 0
    code = 0; idx = 0
    for b in range(1, 17):
        code <<= 1
        t[b] = code & 0xFFFF; t[18 + b] = c[b]; t[36 + b] = idx
        code += c[b]; idx += c[b]
    pos = np.zeros(17, np.int64); a = 0
    for b in range(1, 17):
        pos[b] = a; a += c[b]
    for i in range(n):
        if ln[i]:
            t[54 + pos[ln[i]]] = i
            pos[ln[i]] += 1


@njit(cache=True)
def _dec(inp, e, st, t):
    v = _peek(inp, e, st, 16)
    for k in range(1, 17):
        code = v >> (16 - k)
        if code >= t[k] and code - t[k] < t[18 + k]:
            _drop(st, k)
            return t[54 + t[36 + k] + code - t[k]]
    return -1


@njit(cache=True)
def _rlens(inp, e, st, ln, s, end):
    pl = np.zeros(20, np.int64)
    for i in range(20):
        pl[i] = _rd(inp, e, st, 4)
    pt = np.zeros(54 + MAINSZ, np.int64)
    _build(pt, pl, 20)
    x = s
    while x < end:
        z = _dec(inp, e, st, pt)
        if z < 0:
            return -1
        if z == 17:
            y = _rd(inp, e, st, 4) + 4
            while y > 0 and x < end:
                ln[x] = 0; x += 1; y -= 1
        elif z == 18:
            y = _rd(inp, e, st, 5) + 20
            while y > 0 and x < end:
                ln[x] = 0; x += 1; y -= 1
        elif z == 19:
            y = _rd(inp, e, st, 1) + 4
            z = _dec(inp, e, st, pt)
            if z < 0:
                return -1
            z = (ln[x] + 17 - z) % 17
            while y > 0 and x < end:
                ln[x] = z; x += 1; y -= 1
        else:
            ln[x] = (ln[x] + 17 - z) % 17; x += 1
    return 0


@njit(cache=True)
def _u32(inp, p):
    return inp[p] | (inp[p + 1] << 8) | (inp[p + 2] << 16) | (inp[p + 3] << 24)


@njit(cache=True)
def _decode(inp, clen, out, ulen, wbits):
    slots = (30, 32, 34, 36, 38, 42, 50)[wbits - 15]
    ex = np.zeros(51, np.int64); base = np.zeros(51, np.int64)
    for i in range(51):
        x = 0 if i < 4 else (i - 2) // 2
        ex[i] = 17 if x > 17 else x
    for i in range(50):
        base[i + 1] = base[i] + (1 << ex[i])
    wsz = 1 << wbits; wmask = wsz - 1
    w = np.zeros(wsz, np.uint8); wp = 0
    ml = np.zeros(MAINSZ, np.int64); ll = np.zeros(LENSZ, np.int64); al = np.zeros(8, np.int64)
    mt = np.zeros(54 + MAINSZ, np.int64); lt = np.zeros(54 + MAINSZ, np.int64); at = np.zeros(54 + MAINSZ, np.int64)
    R0 = 1; R1 = 1; R2 = 1; btype = 0; brem = 0; blen = 0; hdr = 0; op = 0; ip = 0; rc = 0
    st = np.zeros(3, np.int64)
    e = clen
    while op < ulen:
        fu = 32768
        if ip >= clen:
            rc = -2; break
        if inp[ip] == 0xFF and ip + 3 < clen:
            fu = (inp[ip + 1] << 8) | inp[ip + 2]; ip += 3
        if ip + 2 > clen:
            rc = -2; break
        fc = (inp[ip] << 8) | inp[ip + 1]; ip += 2
        if fc == 0:
            break
        if ip + fc > clen:
            rc = -10; break
        if fu > ulen - op:
            fu = ulen - op
        st[0] = ip; st[1] = 0; st[2] = 0
        fo = 0
        if not hdr:
            if _rd(inp, e, st, 1):
                _rd(inp, e, st, 32)
            hdr = 1
        while fo < fu:
            if brem == 0:
                if btype == 3 and (blen & 1):
                    st[0] += 1
                btype = _rd(inp, e, st, 3); blen = _rd(inp, e, st, 24); brem = blen
                if btype == 2:
                    for i in range(8):
                        al[i] = _rd(inp, e, st, 3)
                    _build(at, al, 8)
                if btype == 1 or btype == 2:
                    nm = 256 + 8 * slots
                    if _rlens(inp, e, st, ml, 0, 256) or _rlens(inp, e, st, ml, 256, nm):
                        return -3
                    _build(mt, ml, nm)
                    if _rlens(inp, e, st, ll, 0, LENSZ):
                        return -3
                    _build(lt, ll, LENSZ)
                elif btype == 3:
                    st[0] -= (st[2] // 16) * 2; st[1] = 0; st[2] = 0
                    if st[0] < 0 or st[0] + 12 > e:
                        return -9
                    R0 = _u32(inp, st[0]); R1 = _u32(inp, st[0] + 4); R2 = _u32(inp, st[0] + 8); st[0] += 12
                else:
                    return -4
            todo = fu - fo
            if todo > brem:
                todo = brem
            target = fo + todo
            if btype == 3:
                p = st[0]
                if p < 0 or p + todo > e:
                    return -8
                for i in range(todo):
                    c = inp[p + i]; out[op + fo + i] = c; w[wp] = c; wp = (wp + 1) & wmask
                st[0] = p + todo; fo += todo; brem -= todo
                continue
            while fo < target:
                s = _dec(inp, e, st, mt)
                if s < 0:
                    return -5
                if s < 256:
                    w[wp] = s; wp = (wp + 1) & wmask; out[op + fo] = s; fo += 1; brem -= 1
                    continue
                s -= 256
                mlen = s & 7
                if mlen == 7:
                    l = _dec(inp, e, st, lt)
                    if l < 0:
                        return -6
                    mlen += l
                mlen += 2
                slot = s >> 3
                if slot == 0:
                    off = R0
                elif slot == 1:
                    off = R1; R1 = R0; R0 = off
                elif slot == 2:
                    off = R2; R2 = R0; R0 = off
                else:
                    x = ex[slot]
                    if btype == 2:
                        if x > 3:
                            v = (_rd(inp, e, st, x - 3) << 3) & M32
                            a = _dec(inp, e, st, at)
                            if a < 0:
                                return -7
                            off = (base[slot] - 2 + v + a) & M32
                        elif x == 3:
                            a = _dec(inp, e, st, at)
                            if a < 0:
                                return -7
                            off = (base[slot] - 2 + a) & M32
                        elif x > 0:
                            off = (base[slot] - 2 + _rd(inp, e, st, x)) & M32
                        else:
                            off = 1
                    else:
                        off = (base[slot] - 2 + (_rd(inp, e, st, x) if x else 0)) & M32
                    R2 = R1; R1 = R0; R0 = off
                for i in range(mlen):
                    c = w[(wp - off) & wmask]; w[wp] = c; wp = (wp + 1) & wmask
                    if op + fo < ulen:
                        out[op + fo] = c
                    fo += 1
                brem -= mlen
        ip += fc; op += fu
    return rc if rc else op


_dll = None
if os.path.exists(os.path.join(_HERE, 'lzx.dll')) and not os.environ.get('FH1_LZX_NUMBA'):
    try:
        _dll = ctypes.CDLL(os.path.join(_HERE, 'lzx.dll'))
        _work = ctypes.create_string_buffer((1 << 21) + 16384)
    except OSError:
        _dll = None


def decode(data, ulen, wbits=17):
    if _dll is not None:
        out = ctypes.create_string_buffer(ulen + 8)
        r = _dll.lzx_decode(data, len(data), out, ulen, wbits, _work)
        if r < 0:
            raise IOError(f'lzx {r}')
        return out.raw[:ulen]
    out = np.zeros(ulen + 8, np.uint8)
    r = _decode(np.frombuffer(data, np.uint8).astype(np.int64), len(data), out, ulen, wbits)
    if r < 0:
        raise IOError(f'lzx {r}')
    return out[:ulen].tobytes()
