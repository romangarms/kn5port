import json,rmb,collections,pickle,numpy as np,math,struct,re
L=[n for n,s in json.load(open('../list.json')) if n.endswith('.rmb.bin') and not n.endswith('.lc')]
err=collections.Counter();ex={};nm=0;sec=0;tri=0;oob=0;nonf=0;tails=collections.Counter(); F=[]
for n in L:
    b=open('../x/'+n,'rb').read()
    try: p=rmb.parse(b)
    except Exception as e:
        k=str(e)[:10]; err[k]+=1; ex.setdefault(k,n+' '+str(e)); continue
    tails[struct.unpack_from('>III',b,p['end'])[:2]]+=1
    for mi,m in enumerate(p['meshes']):
        nm+=1; sec+=len(m['secs'])
        pos=rmb.verts(m)[0] if m['sd']>=16 else None
        if pos is None or not np.isfinite(pos).all(): nonf+=1; continue
        nt=0
        for s in m['secs']:
            ib=s['ib']; v=ib[(ib<0xffff)]
            if len(v) and v.max()>=m['nv']: oob+=1
            nt+=len(rmb.tris(ib,m['nv']))
        tri+=nt
        F.append(dict(f=n,mi=mi,name=m['name'],sd=m['sd'],nv=m['nv'],nt=nt,mn=pos.min(0).tolist(),mx=pos.max(0).tolist()))
print('files',len(L),'errors',err,ex); print('meshes',nm,'sections',sec,'tris',tri,'oob',oob,'nonfinite',nonf,'material-block lead',tails.most_common(4))
pickle.dump(F,open('../cat2.pkl','wb'))
print('sanity huge',sum(1 for c in F if max(map(abs,c['mn']+c['mx']))>30000))
