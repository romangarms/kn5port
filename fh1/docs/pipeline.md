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

## `build3.py` sections

One 900-line script run top to bottom:

1. **Material helpers**: `materials`, `material`, `blend_material`, `slot_tex`, `role_ground`.
2. **Shape writers**: `write_dae`, `write_lod`.
3. **World meshes** (`# ---- world meshes`): sorts sections into `col` (ground, roads, rock), `pro` (props, barriers), `vis` (decals, no collision). Also rasters heights, roads and coverage. Far terrain tiers fill in where nothing better covers.
4. **Placed models** (`# ---- placed models`): `read_pgeo`, then plants go to the forest, solid models to shapes.
5. **Static objects**: from `zonerec.pkl`, see `free-roam.md`.
6. **Heightfield floor** (`# ---- heightfield safety floor`).
7. **Shapes, materials, scene** (`# ---- shapes, materials, scene`), spawn, `info.json`.

Output categories are in the `statics` and `sections by road surface` lines. `pgeo files` reports parser skips.

## Checks

```
python lodcheck.py <level folder>                    # shape XML, detail nodes, collision
python -m kn5port validate <level folder>            # run from the repo root
python t5.py                                         # missing shapes and textures
```

`t5.py` still has its level path hard-coded at the top: edit it first. Red Rock's expected counts are 464 solid objects, 69,069 plants, 23 plant items, 370 shapes. Run that build after parser changes as a regression test.

Whole-map results as of the last build: 4,771 shapes, 12,536 solid objects, 387,174 plants, 60 plant items, 4,028 materials, 2,055 textures, no missing shapes or textures, and all checks passing.

## Not for version control

`fh1/.gitignore` is a whitelist: only `pipeline/*.py`, `pipeline/lzx.c`, `docs/*.md` and the README are tracked. Never commit or share game-derived data.

A fresh git worktree holds none of the data, so a worktree agent can't run the pipeline. Point it at the main checkout's data, or use the main checkout.

## Windows shell pitfalls

- Bash heredocs mangle backslashes: `\0` became a real null byte in `build3.py` once. Use the Write tool for Python with backslashes, or `chr(92)`.
- Read the README with `encoding='utf8'`. Windows defaults to cp1252.
- `x/` holds 57k files, so avoid listing it.
