"""Copies BeamNG's cooked shapes into the level, next to their .dae files, as stock levels ship them. That skips the COLLADA import on load.
Run after build3.py and one load of the level in BeamNG, with the game closed: python cookship.py [fh1_colorado|fh1_redrock]. A rebuild wipes them."""
import os, shutil, sys
from pathlib import Path
sys.path.insert(0, os.path.abspath('../..'))
from kn5port import beamng

LVL = sys.argv[1] if len(sys.argv) > 1 else 'fh1_colorado'
mods = Path(beamng.find_mods()); level = mods / 'unpacked' / LVL / 'levels' / LVL; cooked = mods.parent / 'temp' / 'levels' / LVL
n = stale = size = 0
for c in cooked.rglob('*.cdae'):
    d = level / c.relative_to(cooked).with_suffix('.dae')
    if not d.exists() or c.stat().st_mtime < d.stat().st_mtime:      # BeamNG would cook these again
        stale += 1
        continue
    shutil.copy2(c, d.with_suffix('.cdae')); n += 1; size += c.stat().st_size
print('done', n, 'shapes', size >> 20, 'MB', stale, 'stale ->', level)
