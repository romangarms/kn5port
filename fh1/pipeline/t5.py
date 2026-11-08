import json,glob,os,numpy as np,pickle,math,sys
L='../out_z/levels/fh1_redrock'
H=None
b=open(L+'/theTerrain.ter','rb').read(); n=np.frombuffer(b[1:5],'<u4')[0]; q=np.frombuffer(b,'<u2',n*n,5).reshape(n,n)
mg=[json.loads(l) for l in open(L+'/main/MissionGroup/items.level.json')]; tb=next(o for o in mg if o['class']=='TerrainBlock'); sp=next(o for o in mg if o['class']=='SpawnSphere')
T=q/65535*tb['maxHeight']+tb['position'][2]
def ground(x,y):
    i=np.clip(((np.array(x)+2048)/2).astype(int),0,n-1); j=np.clip(((np.array(y)+2048)/2).astype(int),0,n-1); return T[j,i]
tot=0;rows=[]
for f in glob.glob(L+'/forest/*.forest4.json'):
    r=[json.loads(l) for l in open(f)]
    p=np.array([o['pos'] for o in r]); d=p[:,2]-ground(p[:,0],p[:,1]); s=np.array([o['scale'] for o in r]); R=np.array([o['rotationMatrix'] for o in r]).reshape(-1,3,3)
    up=R[:,2,2]; det=np.linalg.det(R); rows.append((len(r),os.path.basename(f)[:34],round(float(np.median(d)),1),round(float(np.percentile(np.abs(d),90)),1),round(float(s.min()),2),round(float(np.median(s)),2),round(float(s.max()),2),round(float(up.mean()),3),round(float(np.abs(det-1).max()),4)))
for x in sorted(rows,reverse=True)[:12]: print(x)
print('terrain under spawn',ground(np.array([sp['position'][0]]),np.array([sp['position'][1]])),'spawn z',sp['position'][2])
m=json.load(open(L+'/art/forest/managedItemData.json')); k=next(iter(m)); print(json.dumps(m[k])[:420])
miss=0
for o in [json.loads(l) for l in open(L+'/main/MissionGroup/fh1/items.level.json')]:
    p=L+o['shapeName'].split('fh1_redrock',1)[1]
    miss+= not os.path.exists(p)
ma=json.load(open(L+'/art/shapes/fh1_redrock.materials.json')); bad=0
for v in ma.values():
    for k,t in v['Stages'][0].items():
        if isinstance(t,str) and t.startswith('/levels') and not os.path.exists(L+t.split('fh1_redrock',1)[1]): bad+=1
print('missing shapes',miss,'missing tex files',bad,'materials',len(ma),'alpha',sum('alphaTest' in v for v in ma.values()))
