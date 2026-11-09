import struct,collections,pickle,re,json
import numpy as np,rmb
import gamepath
b=open(gamepath.RIBBON + "Colorado_00.pvs",'rb').read()
R=pickle.load(open('../pvsrec.pkl','rb')); idx={r[4]:(r[3],r[2]) for r in R}
o=0x1990db+4; T=[]
for i in range(14560):
    n=struct.unpack_from('>I',b,o)[0]
    if n>4096: print('bad n',i,n,hex(o)); break
    ids=struct.unpack_from('>%dI'%n,b,o+4); o+=4+4*n
    m=struct.unpack_from('>I',b,o)[0]
    if m>64: print('bad m',i,m,hex(o)); break
    sh=struct.unpack_from('>%dI'%m,b,o+4); o+=4+4*m
    fl=struct.unpack_from('>15f',b,o); o+=60
    T.append((ids,sh,fl))
print('parsed',len(T),'end',hex(o),'file',hex(len(b)),b[o:o+64].hex(' ',4))
print('mesh12588',T[12588][0],T[12588][1])
pickle.dump(T,open('../meshtab.pkl','wb'))
