import struct,os,re,zlib
import lzxdec
from PIL import Image
Image.MAX_IMAGE_PIXELS=None
import gamepath
p=gamepath.MEDIA + "UI.zip"
f=open(p,'rb'); sz=os.path.getsize(p); f.seek(sz-22); e=f.read(22)
_,dn,cdn,n1,n2,cs,co,cl=struct.unpack('<IHHHHIIH',e); f.seek(co); b=f.read(cs); o=0
os.makedirs('../ui',exist_ok=True)
while b[o:o+4]==b'PK\x01\x02':
    v,vn,fl,m,tm,dt,crc,c,u,nl,el,cml,ds,ia,ea,lo=struct.unpack('<HHHHHHIIIHHHHHII',b[o+4:o+46])
    n=b[o+46:o+46+nl].decode('latin1'); o+=46+nl+el+cml
    if n.startswith('New Map/') and n.lower().endswith(('.jpg',)) :
        f.seek(lo); h=f.read(30); ln,lx=struct.unpack('<HH',h[26:30]); f.seek(lo+30+ln+lx); dd=f.read(c)
        if m==21:
            try: dd=lzxdec.decode(dd,u,17)
            except IOError as x:      # nine Map Ref images don't decode (lzx.dll fails on them too); only MapGameReady is used
                print(n,'skipped:',x); continue
        print(n,len(dd),zlib.crc32(dd)&0xffffffff==crc)
        open('../ui/'+n.replace('/','_').replace(' ','_'),'wb').write(dd)
im=Image.open('../ui/New_Map_MapGameReady.jpg'); print(im.size); im.thumbnail((1600,1600)); im.convert('RGB').save('../ui/map_small.jpg',quality=85)
