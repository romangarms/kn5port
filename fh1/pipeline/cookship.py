"""Copies BeamNG's cooked shapes into the level, next to their .dae files, as stock levels ship them. That skips the COLLADA import on load.
Then packs the mod into fh1/release/<level>.zip, ready to upload.
Run after build3.py and one load of the level in BeamNG, with the game closed: python cookship.py [fh1_colorado|fh1_redrock|...] [--no-zip].
A rebuild wipes the cooked shapes.

The zip holds the mod folder's contents at its root (gameplay/, levels/, lua/, scripts/), as BeamNG reads a mod zip. README_kn5port.txt
is replaced by README_<level>.txt with credits, and info.json's description and authors by credits, in the zip only. Files that are already compressed (.jpg, .png, .ogg) are stored, everything else deflated.
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
# One load cooks about 95% of the shapes (6,840 of 7,229 on Colorado); BeamNG cooks the rest only when it first meets them. Much
# less than that means the level wasn't loaded since its build
if n < dae * .9:
    print(f'warning: only {n} of {dae} shapes have a fresh cooked copy. Load the level once, close BeamNG, run again')

def credits(title):
    """README_<level>.txt at the zip root: who made the original and who converted it. Named per level, as every mod's root shares one folder in game"""
    from kn5port import __version__
    return '\n'.join([
        f'{title}',
        '=' * len(title),
        '',
        'An unofficial fan conversion of Colorado from Forza Horizon (2012) for BeamNG.drive.',
        'Not affiliated with or endorsed by Microsoft, Playground Games or Turn 10 Studios.',
        '',
        'ORIGINAL GAME',
        '  Forza Horizon (2012), developed by Playground Games with Turn 10 Studios, published by Microsoft Studios.',
        '  The map, models, textures and sounds are theirs. All rights belong to their owners.',
        '',
        'CONVERSION',
        f'  Converted by Roman Garms with kn5port {__version__}, built with Claude Code (Anthropic).',
        f'  kn5port: {beamng.REPO}',
        '',
        'INSTALL',
        '  Put this .zip, unopened, in BeamNG.drive\'s mods folder (in the game: Mods > Open mods folder),',
        '  then pick the level from Freeroam. The FH1 radio is a separate mod, fh1_radio.',
        '',
        'RIGHTS HOLDERS',
        f'  To have this mod taken down, open an issue at {beamng.REPO}/issues.',
        '',
        f'Packed {time.strftime("%Y-%m-%d")}.',
        ''])


def release_info(raw):
    """The level's info.json as the release shows it in the level picker: credits instead of the personal-copy notice"""
    from kn5port import __version__
    info = json.loads(raw)
    info['description'] = 'Colorado from Forza Horizon (2012), converted to BeamNG.drive. Unofficial fan conversion, not affiliated with Microsoft or Playground Games.'
    info['authors'] = f'Playground Games, Turn 10 Studios, Microsoft Studios (Forza Horizon). Converted by Roman Garms with kn5port {__version__}'
    return json.dumps(info, indent=1)


if ZIP:
    RELEASE.mkdir(exist_ok=True)
    out = RELEASE / f'{LVL}.zip'; tmp = out.with_suffix('.zip.part')
    files = sorted(p for p in mod.rglob('*') if p.is_file() and p.name not in SKIP)
    info_path = level / 'info.json'; title = json.loads(info_path.read_text())['title']
    t0 = time.time(); raw = 0
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr(f'README_{LVL}.txt', credits(title))
        for i, p in enumerate(files):
            if p == info_path:
                z.writestr(p.relative_to(mod).as_posix(), release_info(p.read_text()))
                raw += p.stat().st_size
                continue
            z.write(p, p.relative_to(mod).as_posix(), zipfile.ZIP_STORED if p.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
            raw += p.stat().st_size
            if i % 2000 == 0:
                print(f'  {i}/{len(files)} files', flush=True)
    # Check before replacing the old release: every file there, and the level's own info.json reads back
    with zipfile.ZipFile(tmp) as z:
        names = set(z.namelist())
        assert len(names) == len(files) + 1, 'zip lost files'
        json.loads(z.read(f'levels/{LVL}/info.json'))
    os.replace(tmp, out)
    tops = sorted({x.split('/')[0] for x in names})
    print(f'zip {out}: {len(files)} files, {raw >> 20} MB -> {out.stat().st_size >> 20} MB, {time.time() - t0:.0f} s, top level {tops}')
