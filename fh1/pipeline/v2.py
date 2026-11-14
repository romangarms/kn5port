import pickle,re,struct,numpy as np,sys
import rmb,tex,pv,gamepath
from matlib import materials
from PIL import Image,ImageDraw
MT=pickle.load(open('../meshtab.pkl','rb')); IDX,SH=pickle.load(open('../texshader.pkl','rb'))
def load(no):
    b=open(f'../x/{gamepath.MESH}.{no:05d}.rmb.bin','rb').read(); return b,rmb.parse(b)
def groups(no,form):
    b,P=load(no); ids=MT[no][0]; out=[]; M=materials(b,P)
    for m in P['meshes']:
        a=np.frombuffer(m['vb'],'u1').reshape(-1,m['sd'])
        p=np.frombuffer(a[:,:12].reshape(-1,3,4)[:,:,::-1].tobytes(),'<f4').reshape(-1,3).astype('f8')
        o=12 if m['sd']<20 else 16
        r=np.frombuffer(a[:,o:o+4].tobytes(),'>i2').reshape(-1,2).astype('f8')
        for s in m['secs']:
            T=rmb.tris(s['ib'],m['nv']); mi=s['ints'][2]
            t=None
            cand=[IDX[ids[x]][0] for x in (M[mi][1] if mi<len(M) else []) if x<len(ids)]+[IDX[ids[min(mi,len(ids)-1)]][0]]
            for q in cand:
                r_=tex.load(q) if tex.bix(q) and tex.bix(q)['fmt']!=0x71 else None
                if r_ is not None and r_[0] is not None: t=r_[0]; break
            if t is None or not len(T): continue
            f=s['fl'][4:8]
            uv={'A':np.c_[r[:,0]*f[2]/65535,r[:,1]*f[3]/65535],'B':np.c_[f[0]+f[2]*(r[:,0]+32768)/65535,f[1]+f[3]*(r[:,1]+32768)/65535]}[form]
            out.append((p,uv,T,t,True))
    return out
F=pickle.load(open('../cat2.pkl','rb'))
def mk(no):
    g=[]
    for form in 'AB':
        x=groups(no,form); g.append(pv.render(x,.7,.2) if x else np.zeros((420,420,3),'u1'))
    return np.concatenate(g,1)
nos=[int(a) for a in sys.argv[1:]]
Image.fromarray(np.concatenate([mk(n) for n in nos],0)).save('../v2.png')
