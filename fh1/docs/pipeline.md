# Pipeline and data files

Run everything from `fh1/pipeline`. Scripts read and write `../`.

## Chunks

`CHUNKS` at the top of `build3.py`. Pick with `FH1_CHUNK`:

| Chunk | Level | Grid | Centre | Size | Build |
|---|---|---|---|---|---|
| `redrock` (default) | `fh1_redrock` | 2048 at 2 m | -6000, 4250 | 275 MB | 1 minute |
| `colorado` | `fh1_colorado` | 4096 at 4 m | -300, 500 | 3.2 GB | 7 minutes, peak 4 GB RAM |

The drivable map spans about x -8000 to 7400, z -5200 to 6200. Far backdrop meshes reach further and overhang the square.

Output goes to `mods/unpacked/<level>`, or to a folder passed as the first argument. An unpacked mod is `mods/unpacked/<level>/levels/<level>/`. BeamNG needs a full restart to see changes. Close it before building: it holds enough RAM to get in the way.

Use `FH1_DUMP` to override the dump path in `prepare.py`. Note that `build3.py` has the route folder path hard-coded in `RT`.

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

Without an output folder argument, `build3.py` then runs the post-build scripts, several at once. `AFTER` near the end of `build3.py` lists what each one must wait for: `animated.py` reads `fh1_nav` and `grass.py`'s forest files, `levelinfo.py` and `navmap.py` both edit `info.json`, and `grade.py` grades `grass.py`'s textures. Items files that several scripts touch go through `mission.py`, which holds a lock while reading and writing and replaces the file whole, so a reader never sees half of it: the top-level `MissionGroup/items.level.json`, which each adds its group to, and `fh1`'s, which `water.py` and `animated.py` edit and `nightlights.py` reads for lamp posts. A new script that edits a shared file should do the same, and gets an entry in `AFTER` for anything it reads from another script. Their output lines start with the script's name.

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
