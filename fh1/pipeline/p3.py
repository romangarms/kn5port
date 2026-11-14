import struct,collections,json,re,pickle
import numpy as np
import gamepath
b=open(gamepath.PVS,'rb').read()
ids=set(); names={n for n,_ in json.load(open('../list.json'))}
for n in names:
    m=re.match(r'_0x([0-9A-Fa-f]{8})\.(bix|bin)',n)
    if m: ids.add(int(m.group(1),16))
pos=0xac+7-4   # try to sync: find first '3f800000 3f800000 00000000 00000000'
sig=bytes.fromhex('3f8000003f8000000000000000000000')
hits=[m.start() for m in re.finditer(re.escape(sig),b)]
print(len(hits),hits[:6],collections.Counter(np.diff(hits).tolist()).most_common(8),hex(hits[-1]))
R=[]
for h in hits:
    if h<13: continue
    u=b[h-13:h]; a,kind,x,y=struct.unpack('>IBII',u); R.append((h-13,a,kind,x,y))
print(R[:6]); print(R[-4:])
print('field a',collections.Counter(r[1] for r in R).most_common(5),'kind',collections.Counter(r[2] for r in R).most_common(10))
xs=[r[3] for r in R]; ys=[r[4] for r in R]
print('x max',max(xs),'y max',max(ys),'x in tex ids',sum(x in ids for x in xs)/len(xs),'y in tex',sum(y in ids for y in ys)/len(ys))
print('unique x',len(set(xs)),'unique y',len(set(ys)))
pickle.dump(R,open('../pvsrec.pkl','wb'))
e=R[-1][0]+29; print('after last',hex(e),b[e:e+200].hex(' ',4))
