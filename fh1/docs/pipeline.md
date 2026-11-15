# Pipeline and data files

Run everything from `fh1/pipeline`. Scripts read and write `../`.

## Chunks

`CHUNKS` at the top of `build3.py`. Pick with `FH1_CHUNK`:

| Chunk | Level | Grid | Centre | Size | Build |
|---|---|---|---|---|---|
| `redrock` (default) | `fh1_redrock` | 2048 at 2 m | -6000, 4250 | 275 MB | 1 minute |
| `colorado` | `fh1_colorado` | 4096 at 4 m | -300, 500 | 3.2 GB | 7 minutes, peak 4 GB RAM |
| `clearsprings` | `fh1_clearsprings` | 2048 at 2 m | 1700, 2000 | | |
| `coloradodirt` | `fh1_coloradodirt` | 4096 at 4 m | -300, 500 | | Rally Expansion; builds in `fh1/dirt/`, see Other tracks |

The drivable map spans about x -8000 to 7400, z -5200 to 6200. Far backdrop meshes reach further and overhang the square.

Output goes to `mods/unpacked/<level>`, or to a folder passed as the first argument. An unpacked mod is `mods/unpacked/<level>/levels/<level>/`. BeamNG needs a full restart to see changes. Close it before building: it holds enough RAM to get in the way.

Every script finds the game through `gamepath.py`: `FH1_DUMP`, else the path in `fh1/dump_path.txt` (the app writes it). `KN5PORT_MODS` overrides the BeamNG mods folder for every script, through `beamng.find_mods()`. `zones.py` checks placements against the live capture (`memrec.pkl`, `memobj_all.pkl`) only when those files exist.

The app runs the scripts from its own copy, `<work folder>/app_pipeline/`, the same way the build queue uses `build_snapshot_<chunk>/`. With this checkout's data prepared, its work folder defaults to `fh1/` itself.

## Other tracks: the Rally Expansion

FH1's Rally Expansion is its own track, `ColoradoDirt`: the game loads it in place of Colorado. It is a rebuild of Colorado in the same frame with 614 of Colorado's 1,434 zones (Red Rock, Redstone and the mountains mostly; no festival), about 40% of its meshes new or re-cut, and every mesh renumbered. Its 34 routes are `Tracks` rows in the DLC's database patch, with no `Events` or `Races` rows: `events.dlc_races()` makes each a one-lap rally stage.

Each track's data lives in its own work folder, the folder above the scripts: `fh1/` for Colorado, `fh1/dirt/` for the Rally Expansion. A `track.json` there names the track and the DLC package (`FH1_TRACK` and `FH1_DLC` override it), and `gamepath.py` gives every script the track's files: `BINZIP`, `RIBBON`, `PVS`, `HEX`, `NAV`, `AUDIO`, `MESH` (the mesh file stem), `NAME` (its `MediaName`) and `db()`, `gamedb.slt` with the DLC patch attached as `patch`. Code that names a mesh looks it up by name in `cat2.pkl`, as each track numbers its meshes differently.

```
python prepare_dlc.py "<Xenia>/content/4D5309C9/00000002/6F6992766050D818245ADD408031E280FB5F4E634D/Media"
python buildq.py submit --chunk coloradodirt -m "..." --by <name>
```

**Unloaded areas.** The DLC ships ground and props for all of Colorado, but its zones stream only the corridors round the stages. So FH1 Rally never shows Carson's ring road, the lake, the middle of the map or the festival up close. `build3.py`'s `FILL` builds those areas from the DLC's own meshes. It is on for tracks other than Colorado, and `FH1_FILLUNLOADED` overrides it. World meshes with no zone record enter the hex ground and fill only cells no hex draws. Props no zone loads are kept only where no hex draws ground. Carson's town streets aren't in the DLC at all, so they stay empty; the user chose DLC geometry only, nothing from Colorado.

`prepare_dlc.py` writes `fh1/dirt/track.json` and `dump_path.txt`, unpacks the track's ribbon, nav and settings from `DLCZips/1600_pri_65.zip` into `fh1/dirt/track/`, then runs `prepare.py` from a copy of `pipeline/` in `fh1/dirt/prep_snapshot/`. The `coloradodirt` lane builds from `fh1/dirt/build_snapshot_coloradodirt/` (`WORKS` in `buildq.py`), and `build3.py` stops if a chunk's track doesn't match its work folder's. The bin zip has every entry once, unlike Colorado's, and the DLC's other 12 packages are cars and the Car Challenge pack.

## `prepare.py` order

| Script | Output |
|---|---|
| `index_zip.py` | `ents.json`, zip index |
| `extract.py` | `x/`, everything except audio |
| (built in) | `list.json`, `fnmap.pkl` |
| `r4.py` | `cat2.pkl`, mesh catalog |
| `p3.py` | `pvsrec.pkl`, texture table |
| `p5.py` | `mt.npy`, draw table |
| `p7.py` | `meshtab.pkl`, per-mesh table |
| `p8.py` | `texshader.pkl`, texture and shader lookups |
| `u2.py` | `ui/`, developer map art |
| `roads.py` | `roads10.npy`, road raster |
| `reg3.py` | `reg.json`, map art fit |
| `zones.py` | `zonerec.pkl`, every placement |

`zones.py` needs `memobj_all.pkl` and `memrec.pkl` for its accuracy check, so it fails without a capture. Back up those two files: they took a real drive to collect.

`zonevote.pkl` and `zoneobj.pkl` are obsolete. `memmat.pkl` and `memrows.pkl` are capture leftovers. `build3.py` writes `used.pkl` and `surf.pkl` as side outputs.

## Build queue

Several sessions edit the pipeline at once, so builds go through `buildq.py` rather than running `build3.py` by hand:

```
python buildq.py submit -m "UV box fix" --by uv-session --chunk colorado [--env FH1_COLBOX=0] [--beamng-ok]
python buildq.py status [-n 10]      # running, queued, recent: notes, files changed since the chunk's last build, result, log
python buildq.py wait <request id>   # until the build that covers it ends; exit code 1 if it failed. Good with run_in_background
python buildq.py cancel <request id> # drop a queued request
python buildq.py stop [chunk]        # kill a chunk's running build, or every running build
```

- Each chunk has its own lane: a runner that builds that chunk's requests one at a time, beside the other lanes, as each chunk writes its own level folder. `submit` starts the lane's runner if none is alive, and it exits when its lane is empty. State is in `fh1/buildq/`: `pending/`, `current.<chunk>.json`, `runner.<chunk>.lock`, `history.jsonl`, and `runs/<build>-<chunk>.out` with each build's full output. Shared caches are written whole through a temporary file (`dds.write`, `keep_pickle`), as two lanes can write one at once.
- Pending requests with the same chunk and `--env` flags fold into one build of the newest code, which covers them all. The oldest request's kind goes first. A request made during a build waits for the next one, and a running build is never restarted for it.
- Before starting, the runner waits for 15 s with no change in `pipeline/` (so it doesn't catch a multi-file edit halfway), for any `build3.py` started by hand to finish, and for BeamNG to close, as a build wipes the level it may have loaded. `--beamng-ok` skips the BeamNG wait: use it only when the game has another level loaded, as files of the open level can't be replaced (`grade.py` fails on them).
- It copies `pipeline/` to `fh1/build_snapshot_<chunk>/` and builds there. That folder sits one level under `fh1/` like `pipeline/`, so the scripts' `../` paths still reach the data, and edits made during a build can't leak into it. Don't edit the snapshot.
- `BUILDQ_DIR`, `BUILDQ_SNAP`, `BUILDQ_CMD` and `BUILDQ_SETTLE` point the queue at a scratch folder and a fake build, for testing the queue itself.

## `build3.py` sections

One 900-line script run top to bottom:

1. **Material helpers**: `materials`, `material`, `blend_material`, `slot_tex`, `role_ground`.
2. **Shape writers**: `write_dae`, `write_lod`.
3. **World meshes** (`# ---- world meshes`): sorts sections into `col` (ground, roads, rock), `pro` (props), `bar` (barriers, box collision), `vis` (decals, no collision). Also rasters heights, roads and coverage. Far terrain tiers fill in where nothing better covers.
4. **Placed models** (`# ---- placed models`): `read_pgeo`, then plants go to the forest, solid models to shapes.
5. **Static objects**: from `zonerec.pkl`, see `free-roam.md`.
6. **Heightfield floor** (`# ---- heightfield safety floor`).
7. **Shapes, materials, scene** (`# ---- shapes, materials, scene`), spawn, `info.json`.

Output categories are in the `statics` and `sections by road surface` lines. `pgeo files` reports parser skips.

Shapes are written by worker processes (`daewrite.py`), and cooked textures are cached in `../ddscache`. Delete that folder after changing `dds.py` or `TEXMAX`.

Without an output folder argument, `build3.py` then runs the post-build scripts, several at once. `AFTER` near the end of `build3.py` lists what each one must wait for: `animated.py` reads `fh1_nav` and `grass.py`'s forest files, `levelinfo.py` and `navmap.py` both edit `info.json`, and `grade.py` grades `grass.py`'s, `animated.py`'s and `people.py`'s textures. Items files that several scripts touch go through `mission.py`, which holds a lock while reading and writing and replaces the file whole, so a reader never sees half of it: the top-level `MissionGroup/items.level.json`, which each adds its group to, and `fh1`'s, which `water.py` and `animated.py` edit and `nightlights.py` reads for lamp posts. A new script that edits a shared file should do the same, and gets an entry in `AFTER` for anything it reads from another script. Their output lines start with the script's name.

Distant trees are BeamNG imposters, captured into `temp/levels/<level>/art/forest/models/` the first time a level loads, from the `.dae.asset.json` settings `build3.py` writes beside each forest shape. BeamNG's own mips of a thin tree come out empty (the lodgepole pines' 64 px to 8 px levels), and an empty level draws as a dark square. After loading a level once, `python impfix.py <level>` rebuilds the captured imposters' mips into `../impcache/`, and the next build ships them beside the shapes, where BeamNG reads them instead of capturing. A shape that changes needs its cached imposter deleted and captured again.

`--env FH1_PROFILE=1` writes cProfile stats for the build and each post-build script to `../logs/prof-<stamp>/` (read with `python -m pstats`); it slows Python-heavy code by up to 2x. Queued builds keep numba's compiled code in `../numbacache` (`NUMBA_CACHE_DIR`), as the snapshot is copied without `__pycache__`. `sky.py` keeps its baked faces in `../ddscache/sky-<hash of sky.py, dds.py, tex.py>`. `build3.py` keeps what decoding each texture told it (name, mean colour, alpha cut) and each normal map's colour-layer pairing in `../ddscache/texinfo-<key>.pkl` and `pairs-<key>.pkl`, so it decodes a texture only when it needs its pixels. The key covers `tex.py` and `TEXMAX`; bump `INFO_V` after changing `Textures.get` or `section_normals`. Two Red Rock builds, one filling these caches and one reading them, gave identical files apart from the `.ogg` sounds, whose stream serials are random.

Each build logs to `fh1/logs/<level>-<date>-<time>.log`, post-build scripts included, and ends with a `[time]` summary, slowest first. A post-build script's time there is its own; they overlap, so the wall-clock line is what they cost the build.

## Checks

```
python lodcheck.py <level folder>                    # shape XML, detail nodes, collision
python -m kn5port validate <level folder>            # run from the repo root
python t5.py                                         # missing shapes and textures
```

`t5.py` still has its level path hard-coded at the top: edit it first. Red Rock's expected counts are 571 solid objects, 69,069 plants, 23 plant items. Run that build after parser changes as a regression test.

Whole-map results as of the last build: 4,771 shapes, 12,536 solid objects, 387,174 plants, 60 plant items, 4,028 materials, 2,055 textures, no missing shapes or textures, and all checks passing.

## Not for version control

`fh1/.gitignore` is a whitelist: only `pipeline/*.py`, `pipeline/lzx.c`, `docs/*.md` and the README are tracked. Never commit or share game-derived data.

A fresh git worktree holds none of the data, so a worktree agent can't run the pipeline. Point it at the main checkout's data, or use the main checkout.

## Windows shell pitfalls

- Bash heredocs mangle backslashes: `\0` became a real null byte in `build3.py` once. Use the Write tool for Python with backslashes, or `chr(92)`.
- Read the README with `encoding='utf8'`. Windows defaults to cp1252.
- `x/` holds 57k files, so avoid listing it.
