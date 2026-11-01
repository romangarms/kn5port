import struct,numpy as np
class R:
    def __init__(s,b,o=0): s.b=b; s.o=o
    def u(s): v=struct.unpack_from('>I',s.b,s.o)[0]; s.o+=4; return v
    def us(s,n): v=struct.unpack_from('>%dI'%n,s.b,s.o); s.o+=4*n; return v
    def fs(s,n): v=struct.unpack_from('>%df'%n,s.b,s.o); s.o+=4*n; return v
    def st(s):
        n=s.u()
        if n>512: raise ValueError(f'str len {n} @{s.o-4:x}')
        v=s.b[s.o:s.o+n]; s.o+=n; return v.decode('latin1')
def parse(b):
    """All meshes of a file (hdr[4] of them), then the material block."""
    r=R(b); ver=r.u(); bbox=r.fs(8); mat=r.fs(16); hdr=r.us(6); meshes=[]
    for mi in range(hdr[4]):
        if mi:
            sep=r.us(2)
            if sep[1]!=1: raise ValueError(f'mesh sep {sep} @{r.o:x}')
        c=r.fs(4); lo=r.fs(4); hi=r.fs(4)
        if r.u()!=1: raise ValueError(f'name count @{r.o:x}')
        name=r.st(); t=r.u(); nv=r.u(); sd=r.u(); pad=r.u()
        if pad!=0: raise ValueError(f'pad {pad} @{r.o:x}')
        if sd<12 or sd>128 or nv>2000000: raise ValueError(f'bad vb {nv} {sd} @{r.o:x}')
        vb=b[r.o:r.o+nv*sd]; r.o+=nv*sd
        a=r.u(); secs=[]; x=r.u()
        while True:
            y=r.u(); z=r.u()
            if not (y==1 and z==2): r.o-=8; break
            sn=r.st(); ints=r.us(8); fl=r.fs(8); four,zero,cnt,isz=r.us(4)
            if isz not in(2,4) or cnt>5000000: raise ValueError(f'bad ib {cnt} {isz} @{r.o:x} {sn}')
            ib=np.frombuffer(b,'>u2' if isz==2 else '>u4',cnt,r.o); r.o+=cnt*isz
            secs.append(dict(name=sn,ints=ints,fl=fl,ib=ib,x=x))
            x=r.u()
        r.o-=4
        meshes.append(dict(name=name,t=t,nv=nv,sd=sd,vb=vb,secs=secs,a=a,hdr=hdr,end=r.o,lo=lo,hi=hi))
    return dict(ver=ver,bbox=bbox,mat=mat,hdr=hdr,meshes=meshes,end=r.o)
def tris(ib,nv):
    """triangle-strip with restart -> triangle list, dropping degenerates"""
    out=[]; ib=np.asarray(ib,dtype=np.int64); rs=(0xffff if ib.dtype else 0)
    start=0
    cuts=np.where((ib==0xffff)|(ib==0xffffffff))[0].tolist()+[len(ib)]
    for c in cuts:
        s=ib[start:c]; start=c+1
        if len(s)<3: continue
        t=np.stack([s[:-2],s[1:-1],s[2:]],1)
        odd=np.arange(len(t))%2==1
        t[odd]=t[odd][:,[1,0,2]]
        keep=(t[:,0]!=t[:,1])&(t[:,1]!=t[:,2])&(t[:,0]!=t[:,2])
        out.append(t[keep])
    return np.concatenate(out) if out else np.zeros((0,3),int)

def verts(m):
    """-> pos(N,3) f8, normal(N,3), uv(N,2) raw s16/32767 (first set)"""
    a=np.frombuffer(m['vb'],'u1').reshape(-1,m['sd'])
    P=a[:,:12].reshape(-1,3,4)[:,:,::-1].reshape(-1,12)
    pos=np.frombuffer(P.tobytes(),'<f4').reshape(-1,3).astype('f8')
    n=np.frombuffer(a[:,12:16].tobytes(),'>u4').astype(np.int64)
    def s10(v): return np.where(v>=512,v-1024,v)/511.0
    nor=np.stack([s10(n&1023),s10((n>>10)&1023),s10((n>>20)&1023)],1)
    uv=np.frombuffer(a[:,16:20].tobytes(),'>i2').reshape(-1,2)/32767.0
    return pos,nor,uv
