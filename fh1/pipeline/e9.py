import pickle,re,collections
import numpy as np
Z=pickle.load(open('../zones.pkl','rb')); U=pickle.load(open('../used.pkl','rb')); F=pickle.load(open('../cat2.pkl','rb'))
nm=collections.defaultdict(list); box={}
for c in F:
    k=int(c['f'].split('.')[1]); nm[k].append(c); 
world=set(U['world']); used=world|set(U['placed'])
# zones showing many red rock world meshes
rr={k for k in world if any(-6700<c['mn'][0]<-5200 and 4400<c['mn'][2]<6000 for c in nm[k])}
sc=[(len(rr&set(z['meshes'].tolist())),k) for k,z in Z.items()]; sc.sort(reverse=True); top=[k for s,k in sc[:40]]; print(sc[:3],sc[39])
vis=collections.Counter()
for k in top: vis.update(set(Z[k]['meshes'].tolist()))
miss=[(n,c) for n,c in vis.items() if n not in used]
print('visible',len(vis),'unplaced',len(miss))
g=collections.Counter()
for n,c in miss:
    if n in nm:
        g[re.sub(r'\d+','#',nm[n][0]['name'])[:34]]+=1
print(g.most_common(60))
print([ (nm[n][0]['name'],n,c,[round(v) for v in nm[n][0]['mn'][::2]]) for n,c in sorted(miss,key=lambda x:-x[1]) if n in nm][:30])
