"""Check the built level's shapes: XML, detail nodes, collision geometry against the LOD0 meshes."""
import glob, json, re, os, collections, sys
import xml.etree.ElementTree as ET
L = sys.argv[1]
items = [json.loads(l) for l in open(L + '/main/MissionGroup/fh1/items.level.json')]
print('statics', len(items), collections.Counter(o['collisionType'] for o in items))
bad = 0; col = det = 0; sizes = collections.Counter(); noshape = 0; empty = []
for o in items:
    p = L + '/' + o['shapeName'].split('/', 3)[3]
    if not os.path.exists(p):
        noshape += 1
        continue
    t = open(p, encoding='utf8').read(); r = ET.fromstring(t)
    ns = '{http://www.collada.org/2005/11/COLLADASchema}'
    geo = {g.get('id'): sum(int(q.get('count')) for q in g.iter(ns + 'triangles')) for g in r.iter(ns + 'geometry')}
    base = r.find(f'.//{ns}node[@name="base00"]'); start = base.find(f'{ns}node[@name="start01"]')
    d = collections.Counter(); c = 0
    for n in start.findall(ns + 'node'):
        g = n.find(ns + 'instance_geometry').get('url')[1:]
        if n.get('name').startswith('Colmesh_'):
            c += geo[g]
        else:
            d[int(n.get('name').rsplit('_a', 1)[1])] += geo[g]
    if not d:
        empty.append(os.path.basename(p)); continue
    top = max(d); col += c
    if o['collisionType'] != 'None' and c != d[top]:
        bad += 1
    if (o['collisionType'] != 'None') != (base.find(f'{ns}node[@name="collision-1"]') is not None):
        bad += 1
    det += len(d); sizes[len(d)] += 1
print('missing', noshape, 'empty', len(empty), empty[:5], 'mismatch', bad, 'collision tris', col, 'details per shape', sizes)
print('sample details', sorted(d.items(), reverse=True))
f = glob.glob(L + '/art/forest/models/*.dae'); print(len(f))
for p in f[:3]:
    print(os.path.basename(p), re.findall(r'<node id="([^"]+)"', open(p, encoding='utf8').read())[:9])
