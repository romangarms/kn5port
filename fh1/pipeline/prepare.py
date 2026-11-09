"""Rebuild every derived file from the FH1 dump. Run from fh1/pipeline: python prepare.py

Uses lzx.dll if built (see the README), else lzxdec.py's numba decoder. Everything it writes goes to fh1/, which is gitignored.
"""
import json, os, pickle, re, struct, subprocess, sys

from gamepath import DUMP


def run(script):
    print('==', script, flush=True)
    subprocess.check_call([sys.executable, script])


def file_list():
    L = [(e.name, e.stat().st_size) for e in os.scandir('../x')]
    json.dump(L, open('../list.json', 'w'))
    print('list.json', len(L))


def filename_map():
    b = open(DUMP + '/media/tracks/colorado/Ribbon_00/FilenameMap_00.dat', 'rb').read()
    n = struct.unpack('>I', b[:4])[0] // 4
    of = struct.unpack('>%dI' % n, b[:n * 4])
    names = [b[of[i]:(of[i + 1] if i + 1 < n else len(b))].split(b'\0')[0].decode('latin1') for i in range(n)]
    pickle.dump(names, open('../fnmap.pkl', 'wb'))
    print('fnmap.pkl', n)


if __name__ == '__main__':
    os.makedirs('../x', exist_ok=True)
    run('index_zip.py')      # bin.zip central directory -> ents.json
    run('extract.py')        # everything except audio -> x/
    file_list()
    filename_map()
    run('r4.py')             # mesh catalog -> cat2.pkl
    run('p3.py')             # FPVS texture table -> pvsrec.pkl
    run('p5.py')             # FPVS draw table -> mt.npy
    run('p7.py')             # FPVS per-mesh table -> meshtab.pkl
    run('p8.py')             # texture and shader lookups -> texshader.pkl
    run('u2.py')             # developer map art -> ui/
    run('roads.py')          # road raster for map registration
    run('reg3.py')           # map art to world fit -> reg.json
    run('zones.py')          # every static placement -> zonerec.pkl
    print('done. Next: python build3.py')
