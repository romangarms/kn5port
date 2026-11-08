import struct,collections,pickle,re,os
import numpy as np,rmb,tex
from PIL import Image,ImageDraw
T=pickle.load(open('../meshtab.pkl','rb')); R=pickle.load(open('../pvsrec.pkl','rb')); idx={r[4]:(r[3],r[2]) for r in R}
b=open("D:/Roms/Xbox 360/Xenia/FH1XeniaFix/media/tracks/colorado/Ribbon_00/Colorado_00.pvs",'rb').read()
o=0x861db+8; S=[]
for i in range(173):
    n=struct.unpack_from('>I',b,o)[0]; S.append(b[o+4:o+4+n].decode('latin1').split(chr(92))[-1]); o+=4+n
pickle.dump((idx,S),open('../texshader.pkl','wb'))
def mats(bb,m):
    """per-material (shader_i, [slots]) from the mesh tail"""
    e=m['end']; q=bb.find(b'shaders',e); t=bb[e:q-4] if q>0 else b''
    w=struct.unpack('>%dI'%(len(t)//4),t[:len(t)//4*4]); out=[]
    for i in range(len(w)-7):
        if w[i]==3 and w[i+2:i+7]==(0,1,0,1,6) :
            # slot list follows the 22 float block: '1 n slots'
            j=i+7+23
            for k in range(i+7,min(i+60,len(w)-1)):
                if w[k]==1 and 3<=w[k+1]<=12 and all((x<64 or x>=0xfffffff0) for x in w[k+2:k+2+w[k+1]]):
                    j=k;break
            n=w[j+1]; out.append((w[i+1],list(w[j+2:j+2+n])))
    return out
