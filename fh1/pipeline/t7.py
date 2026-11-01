import pickle,re,json,numpy as np
Road=np.load('../Road.npy'); F=pickle.load(open('../cat2.pkl','rb')); nm={}
for c in F: nm.setdefault(int(c['f'].split('.')[1]),c['name'])
L=pickle.load(open('../memobj_all.pkl','rb')); print(len(L))
X0,Z1,SQ=-8048.,6298.,2.
for k,v in L.items():
    if re.search('Outpost',nm[k[0]]):
        x,z=v[0],-v[2]; i=int((x-X0)/SQ); j=int((Z1-z)/SQ); print(nm[k[0]][:36],round(x),round(z),'on-road',bool(np.isfinite(Road[j,i])))
