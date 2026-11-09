import zr,os,collections,json,shutil,time
out='../x'
if os.path.isdir(out): shutil.rmtree(out)   # start empty: the '.lc' rule below must only see this run's files
os.makedirs(out)
seen={}; dif=collections.Counter(); bad=[]; t=time.time(); n=0; tot=0
for e in sorted(zr.E0,key=lambda e:zr.offs(e)):
    k=e['n']
    if k in seen:
        if seen[k]!=e['crc']: dif[k]+=1
        continue
    seen[k]=e['crc']
    if e['n'].endswith('.fsb') or e['n'].endswith('.fev'): continue
    d=zr.read(e)
    if not zr.ok(e,d): bad.append(k)
    p=os.path.join(out,k.replace('/','_'))
    if os.path.exists(p): p+='.lc'   # case-insensitive filesystem collision
    open(p,'wb').write(d); n+=1; tot+=len(d)
print(n,tot>>20,'MB',time.time()-t,'bad',len(bad),bad[:5],'name-with-different-crc',len(dif),list(dif.items())[:8])
