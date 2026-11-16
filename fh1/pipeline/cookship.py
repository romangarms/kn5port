"""Copies BeamNG's cooked shapes into the level, next to their .dae files, as stock levels ship them. That skips the COLLADA import on load.
Then packs the mod into fh1/release/<level>.zip, ready to upload.
Run after build3.py and one load of the level in BeamNG, with the game closed: python cookship.py [fh1_colorado|fh1_redrock|...] [--no-zip].
A rebuild wipes the cooked shapes.

The zip holds the mod folder's contents at its root (gameplay/, levels/, lua/, scripts/), as BeamNG reads a mod zip. README_kn5port.txt
stays out, as in the releases packed by hand. Files that are already compressed (.jpg, .png, .ogg) are stored, everything else deflated.
It is written beside the target and renamed at the end, so a failed run never leaves a half-written zip with the release's name."""
import json, os, shutil, sys, time, zipfile
from pathlib import Path
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng
import buildq

args = [a for a in sys.argv[1:] if not a.startswith('--')]
LVL = args[0] if args else 'fh1_colorado'
ZIP = '--no-zip' not in sys.argv
mods = Path(beamng.find_mods()); mod = mods / 'unpacked' / LVL; level = mod / 'levels' / LVL; cooked = mods.parent / 'temp' / 'levels' / LVL
STORED = {'.jpg', '.jpeg', '.png', '.ogg'}
SKIP = {beamng.NOTICE_FILE}
RELEASE = Path(__file__).resolve().parent.parent / 'release'

if not (level / 'info.json').exists():
    sys.exit(f'{level} has no level: build it first')
# A level mid-build is half-written, and BeamNG holds files of the level it has open
cur = buildq.read_json(buildq.current(LVL.removeprefix('fh1_')))
if cur and cur.get('state') == 'running':
    sys.exit(f'a {LVL} build is running (python buildq.py status): run this after it ends')
if buildq.beamng_open():
    sys.exit('close BeamNG first: it may still be writing cooked shapes')

n = stale = size = 0
for c in cooked.rglob('*.cdae'):
    d = level / c.relative_to(cooked).with_suffix('.dae')
    if not d.exists() or c.stat().st_mtime < d.stat().st_mtime:      # BeamNG would cook these again
        stale += 1
        continue
    shutil.copy2(c, d.with_suffix('.cdae')); n += 1; size += c.stat().st_size
dae = sum(1 for _ in level.rglob('*.dae'))
print('done', n, 'shapes', size >> 20, 'MB', stale, 'stale ->', level)
if n < dae:
    print(f'warning: {dae - n} of {dae} shapes have no fresh cooked copy, so players cook them on first load. Load the level once, close BeamNG, run again')

if ZIP:
    RELEASE.mkdir(exist_ok=True)
    out = RELEASE / f'{LVL}.zip'; tmp = out.with_suffix('.zip.part')
    files = sorted(p for p in mod.rglob('*') if p.is_file() and p.name not in SKIP)
    t0 = time.time(); raw = 0
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, p in enumerate(files):
            z.write(p, p.relative_to(mod).as_posix(), zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
            raw += p.stat().st_size
            if i % 2000 == 0:
                print(f'  {i}/{len(files)} files', flush=True)
    # Check before replacing the old release: every file there, and the level's own info.json reads back
    with zipfile.ZipFile(tmp) as z:
        names = set(z.namelist())
        assert len(names) == len(files), 'zip lost files'
        json.loads(z.read(f'levels/{LVL}/info.json'))
    os.replace(tmp, out)
    tops = sorted({x.split('/')[0] for x in names})
    print(f'zip {out}: {len(files)} files, {raw >> 20} MB -> {out.stat().st_size >> 20} MB, {time.time() - t0:.0f} s, top level {tops}')
