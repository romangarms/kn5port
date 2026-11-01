"""Reader for Xenia GPU traces (.xtr): walks commands, keeps guest memory reads, lists draws with constants."""
import struct,collections,sys
def unsnappy(s):
    o=0;n=0;sh=0
    while True:
        c=s[o];o+=1;n|=(c&127)<<sh;sh+=7
        if c<128: break
    out=bytearray(n);p=0;L=len(s)
    while o<L:
        t=s[o];o+=1;k=t&3
        if k==0:
            l=t>>2
            if l>=60:
                e=l-59;l=int.from_bytes(s[o:o+e],'little');o+=e
            l+=1;out[p:p+l]=s[o:o+l];o+=l;p+=l
        else:
            if k==1: l=4+((t>>2)&7);d=((t>>5)<<8)|s[o];o+=1
            elif k==2: l=(t>>2)+1;d=s[o]|s[o+1]<<8;o+=2
            else: l=(t>>2)+1;d=int.from_bytes(s[o:o+4],'little');o+=4
            for i in range(l): out[p+i]=out[p+i-d]
            p+=l
    return bytes(out)
def walk(p):
    b=open(p,'rb').read(); ver=struct.unpack_from('<I',b,0)[0]; title=struct.unpack_from('<I',b,44)[0]
    o=48; c=collections.Counter(); pk=[]; mem={}; regs=None
    while o+4<=len(b):
        t=struct.unpack_from('<I',b,o)[0]; c[t]+=1
        if t in(0,2): o+=12
        elif t in(1,3,5): o+=4
        elif t==4:
            base,count=struct.unpack_from('<II',b,o+4); o+=12; pk.append((base,b[o:o+count*4])); o+=count*4
        elif t in(6,7):
            base,enc,el,dl=struct.unpack_from('<IIII',b,o+4); o+=20
            mem[base]=(enc,b[o:o+el],dl,len(pk)); o+=el
        elif t==8:
            enc,el=struct.unpack_from('<II',b,o+4); o+=12+el
        elif t==9: o+=8
        elif t==10:
            first,cnt,cb,enc,el=struct.unpack_from('<IIIII',b,o+4); o+=24
            regs=(first,cnt,enc,b[o:o+el]); o+=el
        elif t==11:
            rw,enc,el=struct.unpack_from('<III',b,o+4); o+=16+el
        else:
            print('stop at',o,t); break
    return dict(ver=ver,title=title,cmd=c,pk=pk,mem=mem,regs=regs,end=o,size=len(b))
if __name__=='__main__':
    r=walk(sys.argv[1] if len(sys.argv)>1 else r'C:\Users\roman\Documents\Xenia\scratch\gpu\4D5309C9_1490.xtr')
    print(r['ver'],hex(r['title']),r['cmd'],'end',r['end'],r['size'],'packets',len(r['pk']),'mem blocks',len(r['mem']))
    tc=collections.Counter(); draws=0; kinds=collections.Counter(e for e,_,_,_ in r['mem'].values())
    for base,d in r['pk']:
        i=0
        while i+4<=len(d):
            h=struct.unpack_from('<I',d,i)[0]; ty=h>>30
            if ty==3:
                op=(h>>8)&0x7f; n=((h>>16)&0x3fff)+2; tc[op]+=1; i+=4*n
            elif ty==0: i+=4*(((h>>16)&0x3fff)+2)
            elif ty==1: i+=12
            else: i+=4
    print('pm4',tc.most_common(10),'mem encodings',kinds,'draw packets',tc[0x22]+tc[0x36])
    enc,data,dl,_=next(iter(r['mem'].values())); d=unsnappy(data) if enc==1 else data; print('first block ok',len(d),dl)
