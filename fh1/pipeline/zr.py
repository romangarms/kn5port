import ctypes,json,zlib,os,struct
P=os.environ.get('FH1_DUMP', 'D:/Roms/Xbox 360/Xenia/FH1XeniaFix') + '/media/tracks/colorado/bin.zip'
_d=ctypes.CDLL(os.path.join(os.path.dirname(os.path.abspath(__file__)),'lzx.dll'))
_f=open(P,'rb')
_work=ctypes.create_string_buffer((1<<21)+16384)
E0=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','ents.json')))
def offs(e):
    return struct.unpack('<I',bytes.fromhex(e['x'])[4:8])[0]
def read(e,w=17,raw=False):
    _f.seek(offs(e)); c=_f.read(e['c'])
    if e['m']==0 or raw: return c
    o=ctypes.create_string_buffer(e['u']+8)
    r=_d.lzx_decode(c,len(c),o,e['u'],w,_work)
    if r<0: raise IOError(f"lzx {r} {e['n']}")
    return o.raw[:e['u']]
def ok(e,d): return zlib.crc32(d)&0xffffffff==e['crc']&0xffffffff
