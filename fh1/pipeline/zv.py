import pickle,re,collections,json,time
import numpy as np
from zall import *
L=pickle.load(open('../memobj_all.pkl','rb'))
base=lambda n: re.sub(r'\d+$','',re.sub(r'_LOD.*$','',n)).rstrip('_')
tlive=collections.defaultdict(set)
for k,v in L.items(): tlive[(round(v[0],1),round(v[2],1))].add(base(nm.get(k[0],'')))
Z=[n for n,_ in json.load(open('../list.json')) if n.endswith('.pvsz') and not n.endswith('.lc')]
V=collections.defaultdict(collections.Counter); X={}
for n in Z:
    for did,x,y,z,M in zone(n,None,0):
        p=(round(x,1),round(z,1)); t=int(T[did]); V[p][t]+=1; X.setdefault(p,(x,y,z,M))
print('positions',len(V),'votes/pos',np.percentile([sum(c.values()) for c in V.values()],[10,50,90]))
acc=collections.Counter(); w=[]
OUT={}
for p,c in V.items():
    b=collections.Counter()
    for t,n in c.items(): b[base(nm.get(t,''))]+=n
    top,n=b.most_common(1)[0]; share=n/sum(b.values()); OUT[p]=(top,share,sum(b.values()),c.most_common(1)[0][0],X[p])
    if p in tlive:
        ok=top in tlive[p]; acc[ok]+=1
        if not ok and len(w)<10: w.append((p,tlive[p],b.most_common(3)))
print('held-out accuracy',acc, 'covered live positions',sum(acc.values()),'of',len(tlive)); print(w[:8])
for th in (.5,.7,.9):
    a=[(OUT[p][0] in tlive[p]) for p in OUT if p in tlive and OUT[p][1]>=th]; print(th,len(a),round(float(np.mean(a)),3) if a else None)
pickle.dump(OUT,open('../zonevote.pkl','wb'))
box=[v for p,v in OUT.items() if -8048<p[0]<-3952 and 2202<-p[1]<6298]
print('box',len(box),collections.Counter(v[0][:30] for v in box).most_common(30))
print([ (v[0][:28],round(v[1],2),v[2],[round(q) for q in v[4][:3]]) for v in box if re.search('HorizonOutpost_Main|Restroom|VisitorC|amphi',v[0])][:14])
