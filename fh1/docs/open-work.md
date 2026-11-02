# Open work and user feedback

Written for parallel agents. `build3.py` is one file that most tasks touch, so give parallel agents separate tasks and merge carefully. Investigation scripts can live in new files.

## State

The whole map is built and installed as `fh1_colorado`, next to `fh1_redrock`. File checks pass. **It has not been loaded in-game.** That result decides the priorities below, so ask the user for it first.

## Tasks

Only the user can test in BeamNG, and only one of BeamNG or Xenia can run at a time.

| Task | Where to start | Notes |
|---|---|---|
| Whole-map fps, load time | `beamng-output.md`, Performance | Waiting on the user. 3.2 GB may be too large |
| 37 ungrouped `.pgeo` files fail | `groups_of` | About 3% of plants. Survey first, 13,578 instances |
| 9 ungrouped files, bad id step | `read_pgeo`, packed branch | Plus one placed set, `__R00G06349.pgeo` |
| `Grass_Ungrouped` sets | `read_pgeo` | Not placed yet |
| Decode owner list bytes | `free-roam.md` | Would allow choosing event layouts |
| Replace `.pgeo` name filter with data | `EVENT` | Weak spot of the free-roam logic |
| Barnfind default state | `free-roam.md` | Barns are dropped by the owner rule |
| 7 failed live checks | `formats.md`, zones | Gantries, water tower, barn |
| Normal maps | `tex.py`, `dds.py`, `material` | Not used yet. 41 BC5 textures (format 0x71) on road and some terrain shaders, centred on 127, 128. `h_blnd*_norm` and `h_vblnd_norm` meshes often have no 0x71 slot, so their normals may be stored as DXT1 |
| Terrain texture resolution | `TEXMAX`, `blend_material` | User noticed low-res terrain |
| Lightmaps, water | Not started | |
| `.fiz` collision | Tried and reverted, `fiz.py` reads it | Mixed winding made cars crumple: see `beamng-output.md`. Flags are not decoded |
| Semi wheels | `model_groups` | Wheels wrong, rest of the semi fine |

## What the user has said

Keep these decisions. They came from in-game testing.

- Textures must be FH1's real ones. Close substitutes aren't good enough.
- Race assets must go: concrete blocks, bleachers, cranes, food truck trailers, RVs in odd places.
- Terrain blending: "perfect". Stretchy cliff textures are acceptable.
- Load the terrain from FH1's meshes rather than recreating it. Terrain must meet the roads, with no panels over them.
- Don't render far-away content needlessly. Performance is a stated concern.
- Don't make the user drive every road. Prefer data from the files.
- Dump files are read-only. Game data never gets committed.

## Environment

Python needs numpy, scipy, numba and pillow. `psutil` isn't installed. Windows 11, 32 GB RAM, 2070 Super, Git Bash and PowerShell.
