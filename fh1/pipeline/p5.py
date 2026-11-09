import struct,collections,pickle,re
import numpy as np,rmb
import gamepath
b=open(gamepath.RIBBON + "Colorado_00.pvs",'rb').read()
o=0x87d4d+4; N=62173
print('tail after 18B records',hex(o+18*N),b[o+18*N:o+18*N+80].hex(' ',4))
a=np.frombuffer(b,dtype=np.dtype([('t','>u2'),('p','>u2'),('f','>u2'),('g','>u2'),('s','>u2',(5,))]),count=N,offset=o)
print(a[:6]); print(a[-4:])
print('t max',a['t'].max(),'unique',len(set(a['t'].tolist())))
print('f',collections.Counter(a['f'].tolist()).most_common(8)); print('g',collections.Counter(a['g'].tolist()).most_common(8)); print('p',collections.Counter(a['p'].tolist()).most_common(8))
print('slots0',collections.Counter(a['s'][:,0].tolist()).most_common(6),'s2',collections.Counter(a['s'][:,2].tolist()).most_common(6))
np.save('../mt.npy',a)
