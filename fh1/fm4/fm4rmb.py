"""FM4 *.rmb.bin meshes: FH1's format (rmb.py, version 6) with two differences, see docs/fm4.md.
- no pad word after the vertex stride
- a section's index header is (3, count, index size), not (4, 0, count, index size)
The material block after the meshes names its shaders as 'shaders\\track\\<name>.fx' strings."""
import os
import sys

sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'pipeline'))
import numpy as np
import rmb

tris = rmb.tris


def parse(b):
    r = rmb.R(b); ver = r.u(); bbox = r.fs(8); mat = r.fs(16); hdr = r.us(6); meshes = []
    for mi in range(hdr[4]):
        if mi:
            sep = r.us(2)
            if sep[1] != 1: raise ValueError(f'mesh sep {sep} @{r.o:x}')
        c = r.fs(4); lo = r.fs(4); hi = r.fs(4)
        if r.u() != 1: raise ValueError(f'name count @{r.o:x}')
        name = r.st(); t = r.u(); nv = r.u(); sd = r.u()
        if sd < 12 or sd > 128 or nv > 2000000: raise ValueError(f'bad vb {nv} {sd} @{r.o:x}')
        vb = b[r.o:r.o + nv * sd]; r.o += nv * sd
        a = r.u(); secs = []; x = r.u()
        while True:
            y = r.u(); z = r.u()
            if not (y == 1 and z == 2): r.o -= 8; break
            sn = r.st(); ints = r.us(8); fl = r.fs(8); three, cnt, isz = r.us(3)
            if isz not in (2, 4) or cnt > 5000000: raise ValueError(f'bad ib {cnt} {isz} @{r.o:x} {sn}')
            ib = np.frombuffer(b, '>u2' if isz == 2 else '>u4', cnt, r.o); r.o += cnt * isz
            secs.append(dict(name=sn, ints=ints, fl=fl, ib=ib, x=x))
            x = r.u()
        r.o -= 4
        meshes.append(dict(name=name, t=t, nv=nv, sd=sd, vb=vb, secs=secs, a=a, c=c, lo=lo, hi=hi, end=r.o))
    return dict(ver=ver, bbox=bbox, mat=mat, hdr=hdr, meshes=meshes, end=r.o)


def file_shaders(b, p):
    """shader names in the file's material block, lower case, in order"""
    sep = b'shaders' + bytes([92]) + b'track' + bytes([92])
    return [s.split(b'.fx')[0].decode('latin1').lower() for s in b[p['end']:].split(sep)[1:]]
