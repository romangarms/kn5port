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
| FH1 races as BeamNG time trials | `TrackRoute*.xml`, 243 files | Later step, after mesh and collision work. Routes hold checkpoints, up to 50, start grids and finish points. Event names are in `Events.str`. `colorado.nav` also tags race shortcuts |
| Animated rigid objects | `animatedobjects.zip`, `formats.md` | Windmills, turbines, rides and vehicles. Placement is found in `anim_proc_*` sets. `animated.py` covers metal and wood windmills and wind turbines. It cuts each fan out of the built shape using the Granny bone pivot, and writes it with node keyframes that BeamNG plays as `ambient`, like West Coast USA's pump jacks. A runtime Lua version is in commit fe31f92. Tractors follow FH1's route from a raw float curve, with heading taken from travel, and a `bounds` node stops culling. FH1's route is flat, so each placement gets its own shape, with height, pitch and roll sampled from the ground meshes. Scoring every orientation against trees and buildings left the placement rows as used, with no lap point near anything. Combines, rides and the crop duster have no catalog mesh, so they need a Granny mesh reader. `granny.py` reads the crop duster, which flies in flat paint with heading, pitch and bank taken from its route. Its layout: section 1 (`len - size`) holds u16 index tables, then 12-byte vertices to the end of the file, as 3 big-endian half-floats, then a half-float and 4 bytes. Positions match the windmill's catalog mesh with z flipped. Triangle lists are found by scoring each offset for short edges, not read from the file. UVs aren't solved: the last 6 bytes don't map onto the catalog UVs. `upright` sets the pose from the mesh's shape and symmetry, as bind poses aren't read. Bone parts such as the propeller aren't split, so it doesn't spin. The route's frame is tilted about 34 degrees, so `level_route` removes the tilt, which leaves the compass direction approximate. Combine routes are `D3K16uC16u` curves whose data sits apart from the header. Cable car routes are readable but have no placement set. FH1's curves aren't decoded, so speed is hand-set. The user has ruled out skinned animals and people, and moving objects need no collision |
| More particles | `particles.py`, `effects.zip` | Water and smoke are in. Falling leaves, road dust and campfires are next, then birds, which the user ranked last. Emitter orientation and FH1's particle wind aren't used |
| Canyon and urban reverb | `*.soundscape` | `soundscape.py` only uses tunnel-like zones. Canyon and urban ones have no stock BeamNG snapshot |
| Ambience zones for loops | `*.soundscape` | FH1's 475 zones could replace the region outlines for choosing loops |

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
