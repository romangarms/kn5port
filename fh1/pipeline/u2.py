import struct,os,re,ctypes,zlib
from PIL import Image
Image.MAX_IMAGE_PIXELS=None
d=ctypes.CDLL(os.path.abspath('lzx.dll')); work=ctypes.create_string_buffer((1<<21)+16384)
p="D:/Roms/Xbox 360/Xenia/FH1XeniaFix/media/UI.zip"
f=open(p,'rb'); sz=os.path.getsize(p); f.seek(sz-22); e=f.read(22)
_,dn,cdn,n1,n2,cs,co,cl=struct.unpack('<IHHHHIIH',e); f.seek(co); b=f.read(cs); o=0
os.makedirs('../ui',exist_ok=True)
while b[o:o+4]==b'PK\x01\x02':
    v,vn,fl,m,tm,dt,crc,c,u,nl,el,cml,ds,ia,ea,lo=struct.unpack('<HHHHHHIIIHHHHHII',b[o+4:o+46])
    n=b[o+46:o+46+nl].decode('latin1'); o+=46+nl+el+cml
    if n.startswith('New Map/') and n.lower().endswith(('.jpg',)) :
        f.seek(lo); h=f.read(30); ln,lx=struct.unpack('<HH',h[26:30]); f.seek(lo+30+ln+lx); dd=f.read(c)
        if m==21:
            out=ctypes.create_string_buffer(u+8); r=d.lzx_decode(dd,c,out,u,17,work); dd=out.raw[:u]
        print(n,len(dd),zlib.crc32(dd)&0xffffffff==crc)
        open('../ui/'+n.replace('/','_').replace(' ','_'),'wb').write(dd)
im=Image.open('../ui/New_Map_MapGameReady.jpg'); print(im.size); im.thumbnail((1600,1600)); im.convert('RGB').save('../ui/map_small.jpg',quality=85)
