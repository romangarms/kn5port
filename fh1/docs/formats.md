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
- s16 UVs are quantised to a per-mesh box: `uv = lo + (s / 32767 + 1) / 2 * (hi - lo)`, with `lo` from `fl[4:6]` and `hi` from `fl[6:8]`. Stored values span almost the full s16 range (median 0.97), and signs and poles land inside 0 to 1. Tree cards use the same box. The older `s * scale / 65535` form only looked right on tiling textures.
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

**Packed files also hold full-transform instances.** A model record is `(flag, k, a, n, lods)`: `n` packed instances and `a` full-transform ones. The word before it is 0 or 4 (`model_table`). Full-transform rows are 96 bytes, in model order, just before the packed block. Word 15 is 1.0 and words 3, 7 and 11 are 0, and plant rows keep a tint in words 16 to 18. The gaps between blocks vary, so `placed_blocks` finds rows by those words and takes the file only when their count equals the sum of `a`. Barriers and fences only exist here. Colorado: 114,926 read, 240 files mismatched. Words 1, 3 and 5 of a model entry are its LOD id counts.

Known failures, whole map: 37 packed files fail `groups_of`, all `Models_`, `AModels_` or `pAModels_Ungrouped`, 13,578 instances, about 3%. 9 ungrouped files have an id step that goes wrong partway. A placed set (`__R00G06349.pgeo`, M=2, groups [3, 2]) is unreadable. These are counted in the build's `pgeo files` line.

## `FilenameMap_00.dat`

In `media/tracks/colorado/Ribbon_00`. Starts with offsets into a string table. `prepare.py` turns it into `fnmap.pkl`.

## Live records (proven)

Read by `memscan.py`. In Xenia's memory: a 64-byte big-endian matrix with the draw id at +132, in 224-byte records. Gives exact positions and ids for whatever was loaded while driving.

## `colorado.nav` (working)

Read by `nav.py`. Big endian. Header words: magic, nodes, roads, tags, slots, slots, keys, values, then 3 more. All counts below come from it.

1. 32-byte nodes from 44: float x, height, z, then link slot count, first slot, tag count, first tag, and one unknown. Z is the opposite sign to mesh z.
2. Road table of 16-byte records, ending one word short: unknown, tag count, first tag, node count. Roads and map rows share it.
3. A u32 per slot, which isn't used.
4. A `(road, index)` pair per slot. Each slot belongs to a node, so sorting by road then index gives a road's nodes in order. Indices skip numbers.
5. `(key, value)` tag pairs, for roads and nodes.
6. String offsets for keys then values, each list with its own base, then the strings.

Node spacing is about 20 m. 95% of nodes land on the road raster once z is flipped, and neighbours along a road have a median spacing of 20.4 m.

- One-way roads are stored in driving order: 3,734 of 3,734 opposing-lane checks had the other lane on the driver's left.
- Rows tagged `text` are map labels and rows tagged `area_type` are region outlines, at height 0. `nav.is_road` filters both.
- Useful tags: `display_name`, `road_type`, `oneway`, `traffic_disabled`, `ai_disabled`, `deadend`, `uturn`, `traffic_density`. Most `display_name` values are ids such as `road_278`, and shortcuts have race names.

## FH1's location names

`Environments.str` lists zones and names in matching order, one list reversed: Main Town is Carson, Redstone is Gladstone, Reservoir is Clear Springs, Foothills is Kettle Hills, Mountains is The Rockies, Plains is Montano Plains. Beaumont isn't tied to the Brekenridge zone by any file, so that one is a guess.

## `*.soundscape` (working)

Plain XML in `bin.zip`, 1,450 tiles, 21 MB in total. Read by `soundscape.py`. Holds FH1's whole sound layout:

- `ReverbZone`: `Template0`, `Template1`, `MinHeight`, `MaxHeight`, then triangles of `ReverbTrianglePoint positionX positionZ value`. 377 zones, 4,076 triangles. Templates index `colorado_reverb.xml`. `value` is 0 or 1 per point, which looks like a blend between the two templates. Height limits keep an underpass zone off the road above it.
- `AmbienceZone`: `Template`, indexing `colorado_ambience.xml`, with triangles. 475 zones.
- `Object`: `eventName`, `Position1`, `Position2`. 33,356 placed sounds, 30,427 of them `Default_Reflections_*`.
- `Tree` and `Crowd`: 25,845 and 6,400 placed points.

Positions use the same z sign as `colorado.nav`.

## `*.fsb` audio

FSB4 holding XMA (mode 0x1000000). Read by `ambience.py`, which wraps each sample in an XMA2 RIFF header for ffmpeg. FFmpeg can't open the banks directly. Bank `fev` event data isn't decoded.

## `animatedobjects.zip` (not decoded)

288 of 290 `.pgeo` files read through `gamezip`, with 361 `.xds` textures. Two fail their checksum or decode: `ANIM_Showcase_Event_Helicopter_Race`, `ANIM_Timed_Event_Biplane`. These are `OEGP` version 44, unlike the version 42 sets above: the payload is a RAD Granny file without its usual magic, holding type definitions by name (`Skeletons`, `Bones`, `VertexDatas`, `TriTopologies`, `TrackGroups`). Curve types seen: `D4nK16uC15u`, `D4nK8uC7u`, `D3K16uC16u`, `D3K8uC8u`, `DaK32fC32f`, `D3Constant32f`, `D4Constant32f`, `DaConstant32f`, `DaIdentity`, `DaKeyframes32f`, and the `D3I1`, `D9I1` and `D9I3` families.

Models sit around a local origin. Track copies exist as `__R00G000xx.pgeo`, for example `__R00G00007` windmill and `__R00G00014` Ferris wheel.

Placement is in 202 `anim_proc_*` version 44 sets, also `__R00G*.pgeo`, a few hundred bytes each. The name gives the object group, such as `fx_windmillswood`. The word at 0x44 is the record count. Records are 128 bytes and run to the end of the file. The last record's position is at `len - 0x50`: 12 rotation floats, 3 position floats, 1.0, a model index, 3 LOD distances. Positions use the `.pgeo` z sign. How a set picks its animation file isn't decoded: the names only suggest it.

These files barely use pointers, so they can't be walked as normal Granny files. `DaK32fC32f` curves are stored as raw big-endian floats: rising knots, then the controls, which `route` in `animated.py` finds by scanning. Granny axes are x, up, z, and z maps to BeamNG local y unflipped. Curve headers start with a format byte, such as 10 for `D3K16uC16u`.

Some of the same objects also have static zone records, such as `PLA_FARM_WindmillMetal`, `GLOB_STFN_WindTurbine_02`, `Tractor` and `Gondola`. `ANIM_Cablecars` isn't among the track's model sets.

## Not decoded

`.fiz` collision, normal maps and lightmaps, water, and the owner-block list bytes (see `free-roam.md`).

## CAFF textures

About 2,400 live textures are `_0x<id>.bin` files with no `.bix`, read by `tex.caff`. Most are 64 by 64 AO bakes, and `0x1EE4` is the 768 by 128 trim atlas that armco and barriers use as their diffuse. Layout: a 0x190 byte header, `.data`, then `.gpu`. Word 0x60 is the `.data` size. In `.data`, 0x18 is the same format word as a BIX, 0x1C the kind (2 is a cube map), and 0x24 the width and height as u16. `.gpu` starts with the top mip, tiled as in a BIX.

## Primitive types

A section's `ints[1]` is the Xenos primitive type: 6 is a triangle strip with restarts, and 4 is a plain triangle list. 174 sections are lists, mostly road blends, road pieces and bridges, plus terrain such as `Mountains_Area04A_Terrain_LOD00_03`. `rmb.tris` takes it as its third argument.
