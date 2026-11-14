import ctypes,json,zlib,os,struct
import gamepath
P=gamepath.BINZIP
import lzxdec
_f=open(P,'rb')
E0=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','ents.json')))
def offs(e):
    return struct.unpack('<I',bytes.fromhex(e['x'])[4:8])[0]
def read(e,w=17,raw=False):
    _f.seek(offs(e)); c=_f.read(e['c'])
    if e['m']==0 or raw: return c
    try: return lzxdec.decode(c,e['u'],w)
    except IOError as x: raise IOError(f"{x} {e['n']}")
def ok(e,d): return zlib.crc32(d)&0xffffffff==e['crc']&0xffffffff
