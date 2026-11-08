import numpy as np,json
from PIL import Image
from scipy import ndimage as ni
Image.MAX_IMAGE_PIXELS=None
x0,z0,S=-9000,-7000,10
R=np.load('../roads10.npy')
A=np.asarray(Image.open('../ui/New_Map_MapGameReady.jpg').convert('RGB'))
im=A.astype('f4').mean(2)
G=np.abs(ni.gaussian_filter(im,10)-ni.gaussian_filter(im,1.2))
G=ni.gaussian_filter(G,1.0)
ys,xs=np.nonzero(R); wx=(x0+xs*S).astype('f8'); wz=(z0+ys*S).astype('f8')
sub=np.random.default_rng(0).choice(len(wx),30000,replace=False)
def score(sx,sz,ox,oz):
    u=(wx[sub]/sx+ox).astype(int); v=(wz[sub]/sz+oz).astype(int); ok=(u>=0)&(u<G.shape[1])&(v>=0)&(v<G.shape[0])
    return G[v[ok],u[ok]].mean()
res=[]
for sc in np.arange(3.45,3.85,0.01):
  for dx in np.arange(-90,91,4):
    for dz in np.arange(-90,91,4):
      ox=915+6330/sc+dx; oz=810+1810/sc+dz
      res.append((score(sc,sc,ox,oz),sc,ox,oz))
res.sort(reverse=True); print(res[:3], 'median',np.median([r[0] for r in res]))
s,sc,ox,oz=res[0]
best=res[0]
for sx in np.arange(sc-.03,sc+.03,.005):
  for sz in np.arange(sc-.03,sc+.03,.005):
    for a in np.arange(ox-6,ox+6,1):
      for b in np.arange(oz-6,oz+6,1):
        t=score(sx,sz,a,b)
        if t>best[0]: best=(t,sx,a,b,sz)
print(best)
b=best; sx=b[1];ox=b[2];oz=b[3];sz=b[4] if len(b)>4 else b[1]
json.dump(dict(sx=sx,sz=sz,ox=ox,oz=oz),open('../reg.json','w'))
a=A[::3,::3].copy()
u=((wx/sx+ox)/3).astype(int); v=((wz/sz+oz)/3).astype(int); ok=(u>=0)&(u<a.shape[1])&(v>=0)&(v<a.shape[0]); a[v[ok],u[ok]]=(255,0,0)
Image.fromarray(a).save('../overlay.jpg',quality=88)
