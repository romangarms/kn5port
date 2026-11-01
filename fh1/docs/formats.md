# FH1 file formats

Everything is big endian unless noted. Each section says which script reads the format. Confidence: **proven** means checked against live game data or in-game output; **working** means it parses the whole map but has known exceptions.

## Coordinates (proven)

- Meshes use x east, y up, z south, matching the developer map art.
- `GameObjs.xml`, `CollObjs.xml`, routes, `.pgeo` and zone records use the opposite z sign.
- A game matrix with negative determinant already carries that flip. `game_rot` in `build3.py` handles both cases: `S@Rg` if det < 0, otherwise `S@Rg@S`.
- BeamNG output is `(x - CX, -(z - CZ), y)`, see `g2b` and `n2b`.

## `bin.zip` (proven)

Read by `index_zip.py`, `extract.py`, `zr.py`.

- There are no local headers. The data offset is in extra field 0x1123.
- Method 21 is XMem LZX with window 17, decoded by our own `lzx.c`.
- Entries repeat per zone: there are 57k unique names.
- Many files exist twice, as `__R00G05946.pgeo` and `__r00g05946.pgeo.lc`. Scans should skip `.lc`.

## `*.rmb.bin` meshes (proven)

Read by `rmb.py`, catalogued by `r4.py` into `cat2.pkl`.

- Positions are world space or origin-local. The header holds the mesh count (`hdr[4]`). Each file can hold several meshes.
- Each mesh: name, vertex count, stride, vertices, then sections (name, 8 ints, 8 floats, index width and count, strips with 0xFFFF restart). Materials follow the last mesh.
- Vertices: 3 float position, 10:10:10 normal, s16 UV. Stride 16 means tree cards (position plus UV), not normal meshes.
- UV is `s * scale / 65535`, with scale from the section's `fl[6:8]`. There is no offset. An older formula with an offset only looked right on tiling textures, so don't bring it back.
- `pv.py` and `v2.py` are a small software renderer for comparing UV formulas against textures.

## Textures: `*.bix` plus `*_B.bix` (proven)

Read by `tex.py`. BIX1 header, mips, then the top mip as Xenos tiled DXT1, 3 or 5, byte swapped in 16-bit words. `*_B` holds the top mip. Format 0x52 is the one used for blend masks, and 0x71 textures are skipped as blend layers.

## `Colorado_00.pvs`, FPVS (proven)

Read by `p3.py`, `p5.py`, `p7.py`, `p8.py`.

- At 0xAC, a 28-byte texture table: index to `_0x` texture id (`pvsrec.pkl`).
- 173 shader names.
- A 62,173-record, 18-byte draw table, `mt.npy`: `t` mesh number, `p`, `f` flag, `g`, `s[5]`. Draw ids throughout the game index this table.
- 14,560 per-mesh records (`meshtab.pkl`): texture index list, shader list, OBB.
- A material's slot N is the Nth texture in its mesh's record.

## `*.pvsz` zones (proven)

Read by `zones.py`. This is the source of every static object.

Layout, in order:

1. `u32 n`, then n u32 ids. Bit 31 means load or unload, the low bits are a draw id.
2. `u32 k`, then k u16 mesh numbers.
3. A typed 16-byte chain `(a, b, drawid, type)`. Type 0 outhouses, 2 smashables, 3 armco, 9 static models.
4. Gameplay nodes.
5. One placement record for every id after the first, in the same order, running exactly to the end of the file.

Placement record:

| Offset | Size | Field |
|---|---|---|
| 0 | 6 | 3 half-float LOD distances (e.g. 100, 250, 700; 0x7fff is infinite) |
| 6 | 12 | 3 float position |
| 18 | 18 | 9 half-float rotation, which carries any scale |
| 36 | 4 | float, 0 for most records. Not a per-object scale: live scale doesn't follow it |
| 40 | 12 | zero |
| 52 | 1 | flag. 0 ends the record at 53 bytes |
| 53 | 4 | owner block: u32 ref |
| 57 | 1 | u8 n |
| 58 | n | list bytes |
| 58 + n | 32 | owner name, total 90 + n bytes |

Evidence: all 1,434 zones parsed with no failures. 2,972 of 2,979 checks agreed with the live capture, using no live data. Rotation rows matched live on 97% of 2,831 compared records. Zones are deltas and together cover every object: 59,597 draw placements at 27,293 positions.

The earlier bit-0x800 theory was wrong. Rotation rows are not unit length, so don't use them to detect records.

The 7 failed checks sit at 4 spots: two barn placements where the record gives `CO_CLRD_Outhouse`, two gantries whose ids have no named mesh, and a water tower where the record gives `OBJ_FEST_BarrierMetal`. Unresolved. The barn may simply share a position with an outhouse.

## `*.pgeo` plant and model sets (working)

Read by `read_pgeo` in `build3.py`. Magic `OEGP`, version 42. Bounding box at 0x10 (8 floats). At 0x50: `u32 size, u32 M` (byte size of the instance block, model count).

**Packed sets** (`size > 0`), for `Models_*`, `AModels_*` and some `models_proc_*`:

- The instance block is 40 bytes per instance at the end of the file. Word 0 is 11:11:10 position inside the file's bounding box, then 11:11:10 up and side axes, a half-float size pair, tint.
- Model records `(flag, k, a, n, lods)` give n instances per model, in order.
- Model entries are 64 bytes, starting at 0x80. Files with `models_proc` at 0x98 start them at 0xd0. All 26 of these found are `[freeroam]` tree sets.
- Draw ids follow the last entry. Each model lists 1 or 2 ids, as its entry says (`e[1]` plus `e[3]` when under 8).

**Placed sets** (`size == 0`, `models_proc_*`):

- 96-byte full transforms, with a 32-byte trailer after each model's block. Blocks sit at the end of the file.
- The header start varies. Don't assume 0xd0, or a name-length rule (that theory failed). Find `tga\0` after 0x98: the first draw ids follow it, and the entries are the 64 × M bytes before that point.

The set name carries layer tags such as `[freeroam]`, which appears on 55 sets, the trees plus Red Rock fencing.

Known failures, whole map: 37 packed files fail `groups_of`, all `Models_`, `AModels_` or `pAModels_Ungrouped`, 13,578 instances, about 3%. 9 ungrouped files have an id step that goes wrong partway. A placed set (`__R00G06349.pgeo`, M=2, groups [3, 2]) is unreadable. These are counted in the build's `pgeo files` line.

## `FilenameMap_00.dat`

In `media/tracks/colorado/Ribbon_00`. Starts with offsets into a string table. `prepare.py` turns it into `fnmap.pkl`.

## Live records (proven)

Read by `memscan.py`. In Xenia's memory: a 64-byte big-endian matrix with the draw id at +132, in 224-byte records. Gives exact positions and ids for whatever was loaded while driving.

## Not decoded

`.fiz` collision, normal maps and lightmaps, water, and the owner-block list bytes (see `free-roam.md`).
