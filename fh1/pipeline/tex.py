import struct,os,numpy as np
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
def bix(i):
    n=f'_0x{i:08X}'
    p=f'{X}/{n}.bix'
    if not os.path.exists(p): return None
    h=open(p,'rb').read(); mag,w,hh,mips,fw,tot,top=struct.unpack('>4sIIIIII',h[:28])
    return dict(w=w,h=hh,mips=mips,fw=fw,fmt=fw&0xff,top=top,tot=tot,path=p,bpath=f'{X}/{n}_B.bix')
def blocks(i):
    # the top mip as plain little-endian DXT blocks, ready for a .dds
    t=bix(i); bpb={0x52:8,0x53:16,0x54:16,0x71:16}[t['fmt']]; bw=(t['w']+3)//4; bh=(t['h']+3)//4
    return np.frombuffer(untile(open(t['bpath'],'rb').read(),bw,bh,bpb),'>u2').astype('<u2').tobytes()
def load(i):
    t=bix(i)
    if not t: return None
    d=open(t['bpath'],'rb').read()
    return decode(d,t['w'],t['h'],t['fmt']),t
