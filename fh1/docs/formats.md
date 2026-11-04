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
- UVs are u16 fractions of a per-mesh box: `uv = lo + v / 65535 * size`, with `lo` from `fl[4:6]` and `size` from `fl[6:8]`. Tree cards (stride 16) and the waving flag cloth (`anim_flag_*`, stride 32) keep theirs at byte 12, the rest at byte 16. The older `s * scale / 65535` form only looked right on tiling textures. Two earlier readings were each half right. Reading `fl[6:8]` as the high corner squashed billboard text into two rows. Reading the values as s16 (`(s / 32767 + 1) / 2`) shifted every UV by half the box, which tiling textures hide and atlases don't: speed-limit signs showed a cropped, upside-down "55", parked cars and building facades were scrambled. Checked 2026-11-02 two ways. Rendering the source meshes, signs, cars and billboards read correctly only with u16 and the size. And because the box is fitted to each mesh's UVs, the raw values should run end to end: 1,311 of 3,291 sampled meshes span exactly 0 to 65535, against 34 for s16 (the rest share their box with other meshes in the file), and 42 of 138 tree-card meshes, against 0.
- The second UV set (byte 20) has no box and stays `(s / 32767 + 1) / 2`: no test favoured either reading, and the terrain blend masks it drives look right in-game.
- The flag cloth has no normal at bytes 12 to 15. Terrain blend strips also have 32-byte vertices but keep the usual layout.
- `pv.py` and `v2.py` are a small software renderer for comparing UV formulas against textures.

## Textures: `*.bix` plus `*_B.bix` (proven)

Read by `tex.py`. BIX1 header, mips, then the top mip as Xenos tiled DXT1, 3 or 5, byte swapped in 16-bit words. `*_B` holds the top mip. Format 0x52 is the one used for blend masks, and 0x71 textures are skipped as blend layers.

Format 0x71 is Xenos DXN, BC5 under the same tiling and byte swap: red is tangent x, green is y, centred on 127, 128 (`tex.normal`). Measured against their colour layers' brightness gradients, most have the handedness of stock BeamNG BC5 maps, whose red and green signs agree on 15 of 17 West Coast and Italy pairs. There are 41, and no DXT1 normal maps: a scan of all 7,098 DXT1 textures found one with a normal map's colour.

Slots by shader name (working: surveyed over every material in the catalog):

- Normal map: slot 3 in `h_road_*norm1*`, `h_blnd*_nm1*` and `h_vblnd_norm*_mask*` (slot 4 too there), slot 1 in `h_diff_spec_norm*`. It goes with slot 0's colour, except where gradients say otherwise, such as layer 1 in `h_blnd3_spec_nm1`.
- `_ao`: a small per-mesh map, 64 px on roads and terrain, in slot 4, 5 or 6. A road's AO is a top-down view of the road over the mesh's bounding box. A barn's is an atlas of islands that neither UV set at bytes 16 or 20 lands on.
- `_lm`: slot 7 in the 69 shaders named `*_lm*` (22,352 materials, 3,158 lightmaps, mostly 128 px DXT1). Night lighting, not baked daylight: black apart from light pools. 1,393 of them aren't in `bin.zip` at all. Of the 1,765 present, 477 have light, mostly around Carson, then Beaumont, the festival and Gladstone. On road and blend ground a lightmap is a top-down view, u along FH1 x and v along z, over the box of the LOD0 ground that shares it: a median 87% of lit texels land within 2 texels of that ground, against 22% by chance, and other orientations score at chance. No vertex stream fits (bytes 16 to 32, as s16 with or without the UV box, flipped or swapped). Building and sign lightmaps fit neither and stay unsolved (55% against 36%).
- `lake_anim_norm_opac_refl_3`: 0xD9D (not in the dump), 0x1294 (32 px white), and two ripple normal maps, 0x1295 and 0x1296. `materials()` can't read its record.

## `Colorado_00.pvs`, FPVS (proven)

Read by `p3.py`, `p5.py`, `p7.py`, `p8.py`.

- At 0xAC, a 28-byte texture table: index to `_0x` texture id (`pvsrec.pkl`).
- 173 shader names.
- A 62,173-record, 18-byte draw table, `mt.npy`: `t` mesh number, `p`, `f` flag, `g`, `s[5]`. Draw ids throughout the game index this table.
  - **LOD groups (proven).** `p` is a signed i16 offset to the next record of the same LOD group, and following it always comes back round: 6,511 rings of 3 (LOD0, 1, 2), 6,153 rings of 2, 17,774 of 1. `g` bits 12-13 are the level: 0, 1, 2 in rings of 3, and 0 and 3 in rings of 2. Lone meshes are 3, or 0. Bit 11 is set on the top member (LOD0, or a lone mesh). Every mesh file is in exactly one ring at one level. Names agree for 14,071 of 14,939 world meshes. Where they don't, the ring is right: names join unrelated meshes (every `Object025`) and exporters misnamed some LODs (`ROAD_CLRD_Mountains_Area03_53_LOD00` with `_49_LOD01`, `racehub_LOD00` with `hubgrass_LOD01`). 254 records with `g` under 0x4000 point outside any ring, into some other hierarchy. They have no catalog names. `build3.py` reads this in `lod_rings`.
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

The 7 failed checks sit at 4 spots: two barn placements where the record gives `CO_CLRD_Outhouse`, two gantries whose ids have no named mesh, and a water tower where the record gives `OBJ_FEST_BarrierMetal`. Barns: each barnfind barn has a second record owned by `<event>C`, pointing at draw ids 208 and 218, which resolve to meshes 0 and 1 named `CO_CLRD_Outhouse`, probably a null entry. Gantries: the third record is LOD02, mesh 6619, which has no `.rmb.bin`, and Xenia only had LOD00 and LOD01 loaded. Neither is a reader bug. The water tower isn't real. Both live `PLA_FARM_WaterTower2` records near there, at (-4313, -2096) and (-5419, -3183), carry the exact position and rotation of a plane race's `OBJ_FEST_BarrierMetal` (`PLANE_RACE_006_04` and `PLANE_RACE_001_07`), and the nearest zone-placed tower is 3.9 km away. The capture paired a tower's draw id with a reused event-barrier slot. So all 7 failures are explained and none is a reader bug.

## `Colorado_00.hex`, zone hexes (proven)

In `Ribbon_00`. Says where each `.pvsz` zone is: zones are 100 m hexes.

- Header, 32 bytes: magic `HEXY`, u32 101, float hex radius (100, centre to corner), float x0 (-6700), float z0 (-6495.19, 75 rows of 86.6), u32 zone count (1434), u32 width (91), u32 height (60).
- Then a 91 by 60 grid of u32 zone numbers, row-major, `ffffffff` for no zone. 1,434 cells are filled, one per zone.
- Flat-topped hexes, odd columns shifted half a row: centre `x = x0 + (c + .5) * 1.5 * R`, `z = z0 + (r + .5 + .5 * (c % 2)) * sqrt(3) * R`, with zone z signs (opposite to mesh z, the same as `colorado.nav` north).
- Then bytes that look like 6-bit masks, one per cell (`3f` is common), probably neighbours. Not decoded.

Evidence: 99.7% of `colorado.nav` road nodes fall in a filled hex with this layout. The next best layout gets 92%.

Filled hexes follow the drivable area, so open country far from roads has no zone. `PVSZLookup_00.dat` is 1,434 `(key, zone number)` pairs whose keys look like hashes. `PVSZoneSpeeds.dat` is one float per zone, 55 to 111, probably a streaming speed.

**A zone's id list is what FH1 draws from that hex**, not a delta. Bit 31 set means drawn. A fine tile, `Mountains_Area01A_Terrain_LOD00_10`, is listed by every hex within 300 m of it and none beyond 600 m. `UberLOD_04B` is listed by none of the 149 hexes within 1 km of it, including the 65 inside its bounds. `TERR_CLRD_ZONE02_AREA6_MIDDIST_02` (mesh 12873) isn't listed within 300 m. So a mesh's nearest listing hex gives the closest distance FH1 ever shows it from. Overlapping ground meshes can be listed by the same hex, so the lists alone don't settle overlaps.

## `*.pgeo` plant and model sets (working)

Read by `read_pgeo` in `build3.py`. Magic `OEGP`, version 42. Bounding box at 0x10 (8 floats). At 0x50: `u32 size, u32 M` (byte size of the instance block, model count).

**Packed sets** (`size > 0`), for `Models_*`, `AModels_*` and some `models_proc_*`:

- The instance block is 40 bytes per instance at the end of the file. Word 0 is 11:11:10 position inside the file's bounding box, then 11:11:10 up and side axes, a half-float size pair, tint.
- Model records `(flag, k, a, n, lods)` give n instances per model, in order.
- Model entries are 64 bytes, starting at 0x80. Files with `models_proc` at 0x98 start them at 0xd0. All 26 of these found are `[freeroam]` tree sets.
- Draw ids follow the last entry. Each model lists 1 or 2 ids, as its entry says (`e[1]` plus `e[3]` when under 8). An entry whose word 8 isn't 0 has no id: that word is the byte length of a `.max` path, and the paths follow the ids, padded to 4 bytes (`model_ids`). These are mostly festival canopy tents with no catalogue mesh.

**Placed sets** (`size == 0`, `models_proc_*`):

- 96-byte full transforms, followed by 32-byte trailers. In the `(1, k, count, 0, t)` record form, `t` is the number of trailers after that model's rows, usually 1 but 2 in `__R00G06349` and `__R00G06371`. Blocks sit at the end of the file.
- The header start varies. Don't assume 0xd0, or a name-length rule (that theory failed). Find `tga\0` after 0x98: the first draw ids follow it, and the entries are the 64 × M bytes before that point.

The set name carries layer tags such as `[freeroam]`, which appears on 55 sets, the trees plus Red Rock fencing.

**Packed files also hold full-transform instances.** A model record is `(flag, k, a, n, lods)`: `n` packed instances and `a` full-transform ones. The word before it is 0, 1 or 4 (`model_table`). Full-transform rows are 96 bytes, in model order, just before the packed block. Word 15 is 1.0 and words 3, 7 and 11 are 0, and plant rows keep a tint in words 16 to 18. The gaps between blocks vary, so `placed_blocks` finds rows by those words and matches runs of rows to the records in order, skipping stray matches. Barriers and fences only exist here. Colorado: 114,926 read, 240 files mismatched. Words 1, 3 and 5 of a model entry are its LOD id counts.

Packed files with size 0 hold only full-transform rows, such as the chain-link fence sets `__R00G06531` and `__R00G06839`.

Known failures, whole map: none known in `read_pgeo` after `model_ids`, trailer counts and size-0 files were fixed (2,318 files, 530,952 instances). Failures are still counted in the build's `pgeo files` line.

## `Grass_Ungrouped` sets (working)

Read by `pgrass.py`, placed by `grass.py`. 3,199 `.pgeo` files, version 42, named `Grass_Ungrouped_<n>_0` at 0x60. `read_pgeo` skips them silently: they hold no instances. Each is a patch of ground triangles plus the sprite layers FH1 scatters over it at runtime. All 3,199 parse.

- Word 0x40 is the texture count. After the name, padded to 4 bytes, come that many 8-byte entries whose first word indexes the PVS texture table (`IDX[i][0]` in `texshader.pkl`). Then the patch's own box (8 floats), within metres of the box at 0x10. Some files keep 8 more bytes before it, such as two little-endian floats, 30 and 90 degrees. Then `nv`, `ni` and 24 zero bytes.
- Vertices, 12 bytes: 3 u16 positions inside the patch box, a density weight / 65535 (usually 0.9), a group id, 0xffff. Then `ni` u16 indices (a triangle list), a u16 per triangle (1 to n), padding to 4, then two 16.16 words per triangle: its area, capped at 200 m2, and its slope in degrees.
- Tables: `ptr, sprites, ptr, layers, ptr, groups, textures`, 4 pointers, 4 floats, then per group `(ptr, layer count)`, then the groups' layer indices. Sprites, 44 bytes: width min and max, height min and max, `u0, u1, v_bottom, v_top`, then 3 words, the last the texture slot. Layers, 64 bytes: pointer, density per m2, 40, 50 or 60, 1.0, two more floats, sprite count, then fixed-point copies of the same values. Then each layer's sprite indices, in layer order. A vertex's group (word 4) picks the layers that grow on its triangles.
- After that: three disjoint instance counts per texture with sorted index lists, not decoded. On `__R00G02296` they sum to 277 and 831, and density x area x weight gives 282 and 848. They look like distance subsets of 52%, 36% and 12%.
- The 4 floats are (26, 75, 150, 20) on 2,967 sets, (100, 300, 500, 50) on corn and (200, 400, 800, 50) on crowds: probably those subsets' distance bands. Layers' 40 and 50 or 60 look like a fade in metres. `TrackSettings.xml` adds `GrassHeightScale` 1.3, `GrassFadeStartDistance` 250 and `GrassFadeEndDistance` 280.
- Positions use the `.pgeo` z sign. Vertices sit exactly on FH1's LOD0 terrain and road meshes: median error under 0.2 mm on 40 random sets.
- Textures are 256 px DXT5 atlases: 20483 to 20488 and 20490 grass, reeds and flowers, 20489 corn (119 sets), 20491 festival crowd people (113 sets). Whole map: 417k triangles, 2.5 km2, about 10.4M sprites without the crowds, at 0.2 to 15 per m2.

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

## `realtimesky.zip` (partly decoded)

Cloud maps are tiled BC5 `.xds`, read by `sky.py`. `Moon0` to `Moon4.xds` are DXT5 (format 0x54) 256 x 256: 0 is a bare disc, 2 the full moon with glow, 1, 3 and 4 crescents. `Sun.xds` and `SkyDither.xds` are A8R8G8B8 (0x86) 128 x 128. `Stars.bin` is the Yale Bright Star Catalogue, brightest first: 9,096 big-endian float quads (x, y, z, w), y towards the celestial pole, RA = atan2(x, z), w the visual magnitude / 7.8, with the four stars brighter than 0 set to 0. Checked on Vega, Rigel, Betelgeuse and Polaris. `CloudDefs.xml` isn't used. In `TimeOfDayA.xml`, `SunPos` stays at least 17 degrees up all day: night is drawn by colours, `StarFade` and `SunObjectMoon`, not by the sun setting.

## `animatedobjects.zip` (partly decoded)

288 of 290 `.pgeo` files read through `gamezip`, with 361 `.xds` textures. Two fail their checksum or decode: `ANIM_Showcase_Event_Helicopter_Race`, `ANIM_Timed_Event_Biplane`. These are `OEGP` version 44, unlike the version 42 sets above: the payload is a RAD Granny file without its usual magic, holding type definitions by name (`Skeletons`, `Bones`, `VertexDatas`, `TriTopologies`, `TrackGroups`). Curve types seen: `D4nK16uC15u`, `D4nK8uC7u`, `D3K16uC16u`, `D3K8uC8u`, `DaK32fC32f`, `D3Constant32f`, `D4Constant32f`, `DaConstant32f`, `DaIdentity`, `DaKeyframes32f`, and the `D3I1`, `D9I1` and `D9I3` families.

Models sit around a local origin. The track has its own copies, `__R00G00000` to `__R00G00032`, named `Anim_<model><n>` at 0x60, such as `Anim_ANIM_WaterSprinkler17`. Their bounding box at 0x10 covers the whole animation, route included. Six festival rides, the hot air balloon, the inflatable ship and spray gun and the tube men (`ANIM_Airwalker`) exist only in these, not in `animatedobjects.zip` or as `.rmb.bin` meshes.

Vertices are 12 bytes in most models and 16 in the Ferris wheel (half-float position, then 4 zero bytes and 4 more). The Granny type list has no `Position` or `TextureCoordinates` member names: the vertices are Forza's own format, and Granny holds only the skeleton and animation. In the 12-byte vertex, bytes 6-7 are U as a big-endian half-float, used as is, and bytes 8-9 are V as a big-endian s16, `v = 0.4977 + s * 1.5395e-5`, about `0.5 + s / 65000`. Bytes 10-11 are constant within a triangle, so they're probably a packed normal. Found by matching the vertices of `ANIM_FARM_WindmillWood` and `WindmillMetal` to their catalog meshes (6627, 6625; Granny z flipped, 81% within 2 cm) and fitting against the catalog UVs with their box applied: U fits with a scale of 1.000 and an error of 0.0002, V within 0.0025 on both windmills. The crop duster's UVs then land on the islands of its texture `4FA4`. U runs to 2 on the crop duster; wrapped, it still lands on the texture. The 16-byte Ferris vertex isn't checked yet. Textures aren't in `animatedobjects.zip`. They're in the Colorado track pool, in a block of IDs no world mesh uses, about `0x4F99` to `0x5027`. Each track copy (`__R00G000xx.pgeo`) lists its own in its header, from about 0x7a, as 8-byte entries ending in a u16 texture-list index that `texshader.pkl` turns into a texture ID. Ferris `4FA8`-`4FB5` (`4FAE` and `4FAF` are the Horizon Wheel sign, by day and by night), balloon `4FB6`-`4FB7`, inflatable ship `4FB8`-`4FB9` (spray gun `4FB8`), Zipper `4FBA`-`4FBF`, Booster `4FC0`-`4FC8`, Fan `4FC9`-`4FD1`, NoFear `4FD2`-`4FDC`, Roundup `4FDD`-`4FE6`, crop duster `4FA4`-`4FA5`, combine `4FA2`-`4FA3`, stage lasers and lights `4FE7`-`4FEF`, sprinklers `4FF1`. `4FB4` (shared by several rides) and `0DE1` turn up in several lists. Which list entry each Granny mesh uses isn't decoded. Most of the other unused track textures are 64 to 512 px night lightmaps of buildings.

Placement is in 202 `anim_proc_*` version 44 sets, also `__R00G*.pgeo`, a few hundred bytes each. The name gives the object group, such as `fx_windmillswood`. The word at 0x44 is the record count. Records are 128 bytes and run to the end of the file. The last record's position is at `len - 0x50`: 12 rotation floats, 3 position floats, 1.0, a model index, 3 LOD distances. Positions use the `.pgeo` z sign. The model index is local to the set, and the set doesn't name its models. But a set's box at 0x10 is exactly the union of its records' model boxes, rotated and moved, so `placed` in `animsets.py` finds each model by fitting them: all 202 sets fit to within 1 mm. Models with the same box can't be told apart this way, such as firework colours, and nor can a small model in a set with a large one: the main stage's 30 extra records read as tube men. One set (`clrd_golf_watersprinklers_001_11`) also holds a stray row that reads as a record at the origin, so records must lie near the set's box. By model, Colorado places about 190 tube men, 38 sprinklers, 8 combines, 6 tractors, 4 balloons, 6 rides, 4 inflatables, 11 wood and 8 metal windmills, 8 turbines, 1 crop duster, 1 geyser, 12 fireworks, stage and autoshow lights and 117 birds. Sets named `heli` and `windymen` hold tube men, not helicopters. No set places a helicopter, banner plane, cable car or camera crane.

These files barely use pointers, so they can't be walked as normal Granny files. `DaK32fC32f` curves are stored as raw big-endian floats: rising knots, then the controls, which `route` in `animated.py` finds by scanning. Granny axes are x, up, z, and z maps to BeamNG local y unflipped. Curve headers start with a format byte, such as 10 for `D3K16uC16u`.

A `D3K16uC16u` header is: format 10, degree, the top 16 bits of a float 1 / knot scale, 3 float scales, 3 float offsets, then the count of u16 values (4 per knot). The values sit elsewhere in the file: knots rising from 0, then 3 controls per knot, each `u16 * scale + offset`. Other u16 runs rise from 0 too, so `quantised_route` takes the run whose controls make the smoothest path. The combine's is 436 knots over 84 s (1 / knot scale 780): rows across a field, x -8.8 to 56.8 m and z 0 to 22.9 m, y constant. With the same quarter turn as the tractor, the route and the turning body fill the model's box to within 0.4 m, which fixes its offset.

Some of the same objects also have static zone records, such as `PLA_FARM_WindmillMetal`, `GLOB_STFN_WindTurbine_02`, `Tractor` and `Gondola`. `ANIM_Cablecars` isn't among the track's model sets.

## `*.fiz` collision (working)

3,201 tiles of about 100 m, 2.0M triangles, read by `fiz.py`. Offsets count from byte 0x20: reading them from byte 0 shifts every index by two vertices, which made triangles spike to the origin and looked like mixed winding. Read correctly, every stored normal agrees with counter-clockwise winding in game axes. Contents: road surfaces, shoulders, car parks and town streets (about 7% of the Clear Springs square), no terrain, and FH1's invisible walls along every road edge and around the golf course, 50 to 65 m tall (flag groups `0x803`, `0xc03`, `0xc09`, `0xc0f`, `0xc2c`, all steep). Group `0x800` is steep and about 13 m tall, along medians and in towns: not identified. The flags' high 16 bits group triangles; the rest isn't decoded. Header words 16 to 22 describe a 10 m lookup grid after the triangles.

## Not decoded

Normal maps and lightmaps, water, and the owner-block list bytes (see `free-roam.md`).

## CAFF textures

About 2,400 live textures are `_0x<id>.bin` files with no `.bix`, read by `tex.caff`. Most are 64 by 64 AO bakes, and `0x1EE4` is the 768 by 128 trim atlas that armco and barriers use as their diffuse. Layout: a 0x190 byte header, `.data`, then `.gpu`. Word 0x60 is the `.data` size. In `.data`, 0x18 is the same format word as a BIX, 0x1C the kind (2 is a cube map), and 0x24 the width and height as u16. `.gpu` starts with the top mip, tiled as in a BIX.

## Primitive types

A section's `ints[1]` is the Xenos primitive type: 6 is a triangle strip with restarts, and 4 is a plain triangle list. 174 sections are lists, mostly road blends, road pieces and bridges, plus terrain such as `Mountains_Area04A_Terrain_LOD00_03`. `rmb.tris` takes it as its third argument.
