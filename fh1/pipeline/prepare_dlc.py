"""Set up the work folder for FH1's Rally Expansion (the ColoradoDirt track), then run prepare.py in it.

    python prepare_dlc.py <the DLC package's Media folder> [work folder, default fh1/dirt]

The package is the 1.1 GB one holding DLCZips/ColoradoDirt_pri_65.zip, e.g. Xenia's content/4D5309C9/00000002/6F6992766050D818245ADD408031E280FB5F4E634D/Media.
The work folder gets track.json and dump_path.txt, which gamepath.py reads, and track/: the track's ribbon, nav and settings from
DLCZips/1600_pri_65.zip, plus its database patch. Then a copy of pipeline/ in <work>/prep_snapshot runs prepare.py, which writes
x/, the tables and the map fit into the work folder, as it does fh1/ for Colorado. Build with: buildq.py submit --chunk coloradodirt.
"""
import json, os, shutil, struct, subprocess, sys, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRACK = 'media/tracks/coloradodirt/'
PATCH = 'media/db/patch/1600000_merge.slt'


def unpack(zp, out):
    """the track's files and the database patch from 1600_pri_65.zip, a zip with local headers whose entries are LZX (method 21)"""
    sys.path.insert(0, str(HERE)); import lzxdec
    b = open(zp, 'rb').read()
    i = b.rfind(b'PK\x05\x06'); n = struct.unpack('<H', b[i + 10:i + 12])[0]; o = struct.unpack('<I', b[i + 16:i + 20])[0]; k = 0
    for _ in range(n):
        m = struct.unpack('<H', b[o + 10:o + 12])[0]
        crc, cs, us = struct.unpack('<III', b[o + 16:o + 28]); nl, xl, cl = struct.unpack('<HHH', b[o + 28:o + 34])
        lo = struct.unpack('<I', b[o + 42:o + 46])[0]; nm = b[o + 46:o + 46 + nl].decode('latin1').replace('\\', '/'); o += 46 + nl + xl + cl
        low = nm.lower()
        if low.startswith(TRACK) and not low.endswith('/'):
            dst = out / nm[len(TRACK):]
        elif low == PATCH:
            dst = out / '1600000_merge.slt'
        else:
            continue
        ln, lx = struct.unpack('<HH', b[lo + 26:lo + 30]); c = b[lo + 30 + ln + lx:lo + 30 + ln + lx + cs]
        d = c if m == 0 else lzxdec.decode(c, us, 17)
        if zlib.crc32(d) & 0xffffffff != crc:
            raise IOError(f'{zp}: {nm} fails its CRC')
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(d); k += 1
    return k


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    dlc = Path(sys.argv[1]).resolve(); work = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else HERE.parent / 'dirt'
    for f in ('DLCZips/ColoradoDirt_pri_65.zip', 'DLCZips/1600_pri_65.zip'):
        if not (dlc / f).exists():
            sys.exit(f'{dlc} is not the Rally Expansion package: {f} is missing')
    sys.path.insert(0, str(HERE)); os.environ.setdefault('FH1_TRACK', 'colorado')
    import gamepath                                 # the base game, from FH1_DUMP or fh1/dump_path.txt
    work.mkdir(parents=True, exist_ok=True)
    (work / 'dump_path.txt').write_text(gamepath.DUMP, encoding='utf8')
    (work / 'track.json').write_text(json.dumps({'track': 'coloradodirt', 'dlc': str(dlc).replace('\\', '/')}, indent=1), encoding='utf8')
    shutil.rmtree(work / 'track', ignore_errors=True)
    print('track/', unpack(dlc / 'DLCZips/1600_pri_65.zip', work / 'track'), 'files', flush=True)
    snap = work / 'prep_snapshot'                   # one level under the work folder, so prepare.py's ../ is the work folder
    shutil.rmtree(snap, ignore_errors=True); shutil.copytree(HERE, snap, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'fh1radio_app'))
    env = dict(os.environ); env.pop('FH1_TRACK', None)
    subprocess.check_call([sys.executable, 'prepare.py'], cwd=snap, env=env)
