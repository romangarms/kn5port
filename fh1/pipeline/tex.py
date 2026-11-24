import functools,struct,os,numpy as np
from PIL import Image
X=os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','x')
def tiled_offset(x,y,w,log_bpp):
    # XGAddress2DTiledOffset; x,y in blocks, w = pitch in blocks (aligned 32), log_bpp = log2(bytes per block)
    aligned=(w+31)&~31
    macro=((x>>5)+(y>>5)*(aligned>>5))<<(log_bpp+7)
    micro=((x&7)+((y&6)<<2))<<log_bpp
    off=(macro+((micro&~15)<<1)+(micro&15)+((y&8)<<(3+log_bpp))+((y&1)<<4))
    return (((off&~511)<<3)+((off&448)<<2)+(off&63)+((y&16)<<7)+(((((y&8)>>2)+(x>>3))&3)<<6))
def untile(d,bw,bh,bpb):
    lb={4:2,8:3,16:4}[bpb]; ab=(bw+31)&~31; hb=(bh+31)&~31
    ys,xs=np.mgrid[0:bh,0:bw]; 
    o=tiled_offset(xs.astype(np.int64),ys.astype(np.int64),bw,lb)
    a=np.frombuffer(d,'u1'); n=len(a)
    out=np.zeros(bh*bw*bpb,'u1'); idx=(ys*bw+xs).ravel()
    src=o.ravel(); keep=src+bpb<=n
    dst=np.tile(np.arange(bpb),(int(keep.sum()),1))
    out[(idx[keep]*bpb)[:,None]+dst]=a[src[keep][:,None]+dst]
    return out.tobytes()
def rgb565(c):
    r=(c>>11)&31; g=(c>>5)&63; b=c&31
    return np.stack([(r*527+23)>>6,(g*259+33)>>6,(b*527+23)>>6],-1)
def dxt_color(blk,four):
    c0=blk[:,0].astype(int); c1=blk[:,1].astype(int)
    a=rgb565(c0); b=rgb565(c1)
    pal=np.zeros((len(blk),4,3),int); pal[:,0]=a; pal[:,1]=b
    g=(c0>c1)|four
    pal[:,2]=np.where(g[:,None],(2*a+b)//3,(a+b)//2); pal[:,3]=np.where(g[:,None],(a+2*b)//3,0)
    return pal,g
def decode(d,w,h,fmt):
    bw=(w+3)//4; bh=(h+3)//4
    bpb={0x52:8,0x53:16,0x54:16,0x71:16}[fmt]
    r=untile(d,bw,bh,bpb)
    u=np.frombuffer(r,'>u2').reshape(bh*bw,bpb//2)
    n=bh*bw
    out=np.zeros((bh*4,bw*4,4),'u1'); out[...,3]=255
    if fmt==0x71:
        return None
    cw=u[:,-4:]; idx=(cw[:,2].astype(np.uint32)|(cw[:,3].astype(np.uint32)<<16))
    pal,g=dxt_color(cw,np.full(n,fmt!=0x52))
    sel=np.stack([(idx>>(2*i))&3 for i in range(16)],1).astype(int)
    px=np.take_along_axis(pal,sel[:,:,None].repeat(3,2),1).reshape(bh,bw,4,4,3)
    out[...,:3]=px.transpose(0,2,1,3,4).reshape(bh*4,bw*4,3)
    if fmt==0x52:
        al=np.where((~g[:,None])&(sel==3),0,255).reshape(bh,bw,4,4)
    elif fmt==0x53:
        bits=np.stack([(u[:,k//4].astype(int)>>(4*(k%4)))&15 for k in range(16)],1)
        al=(bits*17).reshape(bh,bw,4,4)
    else:
        a0=(u[:,0]&255).astype(int); a1=(u[:,0]>>8).astype(int)
        lo=u[:,1].astype(np.uint64)|(u[:,2].astype(np.uint64)<<np.uint64(16))|(u[:,3].astype(np.uint64)<<np.uint64(32))
        s3=np.stack([((lo>>np.uint64(3*k))&np.uint64(7)).astype(int) for k in range(16)],1)
        tb=np.zeros((n,8),int); tb[:,0]=a0; tb[:,1]=a1
        gt=a0>a1
        for k in range(2,8):
            tb[:,k]=np.where(gt,((8-k)*a0+(k-1)*a1)//7,(((6-k)*a0+(k-1)*a1)//5 if k<6 else (0 if k==6 else 255)))
        al=np.take_along_axis(tb,s3,1).reshape(bh,bw,4,4)
    out[...,3]=al.transpose(0,2,1,3).reshape(bh*4,bw*4)
    return out[:h,:w]
def caff(i,n):
    # the other texture container, a .bin: header, .data with the texture's description, .gpu with its mips, top mip first
    p=f'{X}/{n}.bin'
    if not os.path.exists(p): return None
    b=open(p,'rb').read(0x1c0)
    if b[:4]!=b'CAFF': return None
    ds=struct.unpack_from('>I',b,0x60)[0]; fw,kind=struct.unpack_from('>II',b,0x190+0x18); w,hh=struct.unpack_from('>HH',b,0x190+0x24)
    if kind!=0 or fw&0xff not in (0x52,0x53,0x54,0x71): return None    # kind 2 is a cube map
    return dict(w=w,h=hh,mips=0,fw=fw,fmt=fw&0xff,top=0,tot=0,path=p,bpath=p,off=0x190+ds)
def bix(i):
    # the texture's header, read once per process: the build asks for each one many times
    t=_bix(i)
    return dict(t) if t else t
@functools.lru_cache(maxsize=None)
def _bix(i):
    n=f'_0x{i:08X}'
    p=f'{X}/{n}.bix'
    if not os.path.exists(p): return caff(i,n) or (stx().get(i) if STX else None)
    h=open(p,'rb').read(); mag,w,hh,mips,fw,tot,top=struct.unpack('>4sIIIIII',h[:28])
    return dict(w=w,h=hh,mips=mips,fw=fw,fmt=fw&0xff,top=top,tot=tot,path=p,bpath=f'{X}/{n}_B.bix',off=0)
# <Track>.stx.bin, the track's texture pack (fh1/docs/fm4.md, Texture pack): the resident part of every texture, and the only copy of
# those with no .bix or .bin (FM4's texture-table kind 1). Off unless STX is set: FM4's build reads it, FH1's doesn't yet
STX=False
def stx_index(f):
    """{texture id: header dict} from a pack's bytes. Records of 128 bytes from 0x190 ('texture\\0'), one source path each in record
    order (..._0x<id>_s.bin). A texture with no mip chain is at its relocation entry's value (the entry numbered record + 1 whose value
    isn't the record's own .data offset); one with mips at its base field plus the mip region's start (the 01 0C entry numbered
    records + 1). A level whose smaller side is 16 or less sits packed in its tile: at texel (16, 0) when at least as tall as wide,
    else (0, 16)"""
    gs=struct.unpack_from('>I',f,0x88)[0]; g0=len(f)-gs
    n=0
    while f[0x190+128*n:0x190+128*n+8]==b'texture\0': n+=1
    sep=b'compiled'+bytes([92]); paths=[]; at=f.find(sep)
    while at>=0:
        e=f.index(b'\0',at+len(sep)); paths.append(f[at+len(sep):e].decode('latin1')); at=f.find(sep,e)
    s=f.find(b'texture pairs')
    if s<0 or len(paths)<n: return {}
    rel={}; at=s
    while True:
        at=min([x for x in (f.find(b'\x74\x01\x05\x00',at),f.find(b'\x70\x01\x05\x00',at)) if x>=0],default=-1)
        if at<0: break
        rel.setdefault(int.from_bytes(f[at+4:at+7],'big'),[]).append(struct.unpack_from('>I',f,at+7)[0]); at+=4
    j=f.find(b'\x01\x0c\x00\x00'+(n+1).to_bytes(2,'big'),s); mip0=struct.unpack_from('>I',f,j+6)[0] if j>=0 else None
    out={}
    for k in range(n):
        if not paths[k].endswith('_s.bin'): continue
        fw,kind,_,wh,base,mip,_=struct.unpack_from('>IIIIIII',f,0x190+128*k+0x18); w,hh=wh>>16,wh&0xffff
        if kind!=0 or fw&0xff not in (0x52,0x53,0x54,0x71,0x7b): continue
        if mip==0xffffffff:
            c=[v for v in rel.get(k+1,[]) if v!=0x80*k]
            if not c: continue
            off=c[0]
        elif mip0 is None: continue
        else: off=base+mip0
        if min(w,hh)>=32: pack=(0,0)
        elif max(w,hh) in (16,32): pack=(4,0) if hh>=w else (0,4)       # in blocks
        else: continue                                                 # smaller squares: their placement isn't measured
        out[int(paths[k][3:11],16)]=dict(w=w,h=hh,mips=0,fw=fw,fmt=fw&0xff,top=0,tot=0,path='stx',bpath='stx',off=g0+off,pack=pack)
    return out
@functools.lru_cache(maxsize=None)
def _stx(x):
    p=next((os.path.join(x,q) for q in os.listdir(x) if q.endswith('.stx.bin')),None) if os.path.isdir(x) else None
    if not p: return {},None
    idx=stx_index(open(p,'rb').read())
    for t in idx.values(): t['path']=t['bpath']=p
    return idx,p
def stx():
    return _stx(X)[0]
def top_mip(t):
    d=open(t['bpath'],'rb').read()[t['off']:]
    if t.get('pack',(0,0))==(0,0): return d
    # a packed level: move its blocks to the tile's origin, so the plain untile reads it
    bpb={0x52:8,0x53:16,0x54:16,0x71:16,0x7b:8}[t['fmt']]; lb={8:3,16:4}[bpb]; bw=(t['w']+3)//4; bh=(t['h']+3)//4; x0,y0=t['pack']
    ys,xs=np.mgrid[0:bh,0:bw]; src=tiled_offset((xs+x0).astype(np.int64),(ys+y0).astype(np.int64),bw,lb).ravel()
    dst=tiled_offset(xs.astype(np.int64),ys.astype(np.int64),bw,lb).ravel()
    a=np.frombuffer(d[:int(src.max())+bpb],'u1'); out=np.zeros(int(max(dst.max(),src.max()))+bpb,'u1'); k=np.arange(bpb)
    out[dst[:,None]+k]=a[src[:,None]+k]
    return out.tobytes()
def blocks(i):
    # the top mip as plain little-endian DXT blocks, ready for a .dds
    t=bix(i); bpb={0x52:8,0x53:16,0x54:16,0x71:16}[t['fmt']]; bw=(t['w']+3)//4; bh=(t['h']+3)//4
    return np.frombuffer(untile(top_mip(t),bw,bh,bpb),'>u2').astype('<u2').tobytes()
def load(i):
    t=bix(i)
    if not t: return None
    return decode(top_mip(t),t['w'],t['h'],t['fmt']),t
def bc4(blk):
    # n x 8 little-endian BC4 blocks -> n x 16 values
    a0=blk[:,0].astype(int); a1=blk[:,1].astype(int); bits=np.zeros(len(blk),np.uint64)
    for k in range(6): bits|=blk[:,2+k].astype(np.uint64)<<np.uint64(8*k)
    sel=np.stack([((bits>>np.uint64(3*i))&np.uint64(7)).astype(int) for i in range(16)],1)
    tb=np.zeros((len(blk),8),int); tb[:,0]=a0; tb[:,1]=a1; gt=a0>a1
    for k in range(2,8):
        tb[:,k]=np.where(gt,((8-k)*a0+(k-1)*a1)//7,(((6-k)*a0+(k-1)*a1)//5 if k<6 else (0 if k==6 else 255)))
    return np.take_along_axis(tb,sel,1)
def normal(i):
    # a 0x71 (Xenos DXN, BC5) normal map -> (h, w, 2) uint8 x and y, and its top mip as plain BC5 blocks. Same handedness as BeamNG's, see docs/formats.md
    t=bix(i); bw=(t['w']+3)//4; bh=(t['h']+3)//4; b=blocks(i)
    raw=np.frombuffer(b,'u1').reshape(-1,16)
    ch=[bc4(raw[:,8*c:8*c+8]).reshape(bh,bw,4,4).transpose(0,2,1,3).reshape(bh*4,bw*4) for c in range(2)]
    return np.dstack(ch)[:t['h'],:t['w']].astype('u1'),b
