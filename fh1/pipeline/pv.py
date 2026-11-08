"""small software renderer: pv.render(groups) with groups=[(pos Nx3 (x,y up,z), uv Nx2, tris, rgba image)]"""
import numpy as np
from numba import njit
@njit(cache=True)
def _draw(Z,C,P,U,T,tex,alpha):
    h,w=Z.shape; th,tw=tex.shape[:2]
    for t in range(T.shape[0]):
        a,b,c=T[t]
        ax,ay,az=P[a];bx,by,bz=P[b];cx,cy,cz=P[c]
        d=(by-cy)*(ax-cx)+(cx-bx)*(ay-cy)
        if abs(d)<1e-9: continue
        for j in range(max(int(min(ay,by,cy)),0),min(int(max(ay,by,cy))+2,h)):
            for i in range(max(int(min(ax,bx,cx)),0),min(int(max(ax,bx,cx))+2,w)):
                l0=((by-cy)*(i-cx)+(cx-bx)*(j-cy))/d;l1=((cy-ay)*(i-cx)+(ax-cx)*(j-cy))/d;l2=1-l0-l1
                if l0<0 or l1<0 or l2<0: continue
                z=l0*az+l1*bz+l2*cz
                if z<=Z[j,i]: continue
                u=l0*U[a,0]+l1*U[b,0]+l2*U[c,0];v=l0*U[a,1]+l1*U[b,1]+l2*U[c,1]
                x=int((u-np.floor(u))*tw)%tw;y=int((v-np.floor(v))*th)%th
                px=tex[y,x]
                if alpha and px[3]<128: continue
                Z[j,i]=z;C[j,i,0]=px[0];C[j,i,1]=px[1];C[j,i,2]=px[2]
def render(groups,yaw=.7,pitch=.25,size=420):
    allp=np.concatenate([g[0] for g in groups]); c=(allp.min(0)+allp.max(0))/2; ext=max((allp.max(0)-allp.min(0)).max(),.01)
    cy,sy=np.cos(yaw),np.sin(yaw);cp,sp=np.cos(pitch),np.sin(pitch)
    Ry=np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]]);Rx=np.array([[1,0,0],[0,cp,-sp],[0,sp,cp]])
    Z=np.full((size,size),-1e18);C=np.full((size,size,3),255,'u1')
    for P,U,T,tex,al in groups:
        q=(P-c)@(Rx@Ry).T; s=size*.92/ext/1.3
        S=np.stack([size/2+q[:,0]*s,size/2-q[:,1]*s,q[:,2]],1)
        _draw(Z,C,S,U.astype('f8'),T.astype(np.int64),tex,al)
    return C
