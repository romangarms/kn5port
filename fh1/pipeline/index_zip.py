import struct,os,collections,json,re
import gamepath
p=gamepath.MEDIA + 'tracks/colorado/bin.zip'
f=open(p,'rb'); sz=os.path.getsize(p)
f.seek(sz-22); e=f.read(22); _,dn,cdn,n1,n2,cs,co,cl=struct.unpack('<IHHHHIIH',e)
f.seek(co); b=f.read(cs); o=0; ents=[]
while b[o:o+4]==b'PK\x01\x02':
    v,vn,fl,m,tm,dt,crc,c,u,nl,el,cml,ds,ia,ea,lo=struct.unpack('<HHHHHHIIIHHHHHII',b[o+4:o+46])
    name=b[o+46:o+46+nl].decode('latin1'); x=b[o+46+nl:o+46+nl+el]
    ents.append(dict(n=name,m=m,fl=fl,c=c,u=u,lo=lo,x=x.hex(),crc=crc)); o+=46+nl+el+cml
print(len(ents),collections.Counter(e['m'] for e in ents))
ex=collections.Counter(); cz=collections.Counter(); uz=collections.Counter()
def k(n): return re.sub(r'0x[0-9A-Fa-f]+','#',n)
for e in ents: ex[k(e['n'])]+=1; cz[k(e['n'])]+=e['c']; uz[k(e['n'])]+=e['u']
for a,c in ex.most_common(25): print(a,c,cz[a]>>20,'MB c',uz[a]>>20,'MB u')
print(len(ex))
ee=sorted(ents,key=lambda e:-e['u'])
json.dump(ents,open('../ents.json','w'))
