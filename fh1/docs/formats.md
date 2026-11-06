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
- Material records (proven, 2026-11-04), read by `materials()`: `3, shader, 0, 1, 0, 1, n`, then `n` big-endian float4s, the shader's constants `c360_0` to `c360_n-1` in register order, then `1, slot count, slots`. All 21,488 `h_road` and `h_vblnd` records have their slot list right after the constants. The constants give each layer its UV tiling and blend slopes (see `.fxobj` shaders).
- `pv.py` and `v2.py` are a small software renderer for comparing UV formulas against textures.

## Textures: `*.bix` plus `*_B.bix` (proven)

Read by `tex.py`. BIX1 header, mips, then the top mip as Xenos tiled DXT1, 3 or 5, byte swapped in 16-bit words. `*_B` holds the top mip. Format 0x52 is the one used for blend masks, and 0x71 textures are skipped as blend layers.

Format 0x71 is Xenos DXN, BC5 under the same tiling and byte swap: red is tangent x, green is y, centred on 127, 128 (`tex.normal`). Measured against their colour layers' brightness gradients, most have the handedness of stock BeamNG BC5 maps, whose red and green signs agree on 15 of 17 West Coast and Italy pairs. There are 41, and no DXT1 normal maps: a scan of all 7,098 DXT1 textures found one with a normal map's colour.

Slots by shader name (working: surveyed over every material in the catalog):

- Normal map: slot 3 in `h_road_*norm1*`, `h_blnd*_nm1*` and `h_vblnd_norm*_mask*` (slot 4 too there), slot 1 in `h_diff_spec_norm*`. It goes with slot 0's colour, except where gradients say otherwise, such as layer 1 in `h_blnd3_spec_nm1`.
- `_ao`: a small per-mesh map, 64 px on roads and terrain, in slot 4, 5 or 6. A road's AO is a top-down view of the road over the mesh's bounding box. A barn's is an atlas of islands that neither UV set at bytes 16 or 20 lands on.
- `_lm`: slot 7 in the 69 shaders named `*_lm*` (22,352 materials, 3,158 lightmaps, mostly 128 px DXT1). Night lighting, not baked daylight: black apart from light pools. 1,393 of them aren't in `bin.zip` at all. Of the 1,765 present, 477 have light, mostly around Carson, then Beaumont, the festival and Gladstone. On road and blend ground a lightmap is a top-down view, u along FH1 x and v along z, over the box of the LOD0 ground that shares it: a median 87% of lit texels land within 2 texels of that ground, against 22% by chance, and other orientations score at chance. No vertex stream fits (bytes 16 to 32, as s16 with or without the UV box, flipped or swapped). Building and sign lightmaps fit neither and stay unsolved (55% against 36%). **Per-placement lightmaps.** In many building materials (the `BLDG_MainTown_Resi-*` houses, for example), slot 7 is `0xFFFFFFFF` and slots 1, 3 and 6 are `0xFFFFFFFE`. The lightmap then comes from the draw, not the mesh: the draw table's `s[1]` (`mt.npy`) is a texture-list key, one per placement. House 007 (mesh 1615) has 44 draws, with `s[1]` running 5657 to 5700, in a gap inside its own run of the texture list. The first, 5657, is the one entry in its mesh record that no material slot uses. That's why about 375 lit facade textures looked unused: they hang off draws. **Lightmap and AO UVs are TEXCOORD1** (from the shaders, see `.fxobj` shaders below): in the 24-byte vertex, bytes 20-23 as two big-endian u16 / 65535, through the same UV box as UV0 (`fl[4:6] + t * fl[6:8]`). On house 007 that gives a clean atlas of wall, gable and roof charts in 0..1, and its 28 lit lightmaps fall on it (98-100% of lit texels within a texel of a chart). Over 51 sampled object lightmaps, the median is 86% against 65% for the same charts moved by half the map. Most misses are meshes whose UV1 is one value on every vertex, such as `MT_Area02_Pavements*`, `RailingWall_*` and stone walls: FH1 reads a single lightmap texel for the whole object. 36-byte road vertices weren't checked. The texture table's second field looks like a type: 1 for 11,409 entries, of which only 539 exist as files. 9,555 of the missing ones exist only as 16 px stand-ins in the `_0x100000xx.bundle` files. A bundle is a u32 count, then entries of (key, w, h, mips, format, -1, size) and `size` bytes, keyed by texture-list key.
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

**A zone's id list loads (bit 31) or unloads its ids.** Hexes near a mesh load it, a ring farther out unloads it (93% of ground meshes listed both ways have their unload hexes farther out, and no hex does both), and hexes in between list it neither way, with 62 unlisted hexes between on average. The lists are changes, not states, so an unlisted hex says nothing: counting unlisted hexes nearer the load ring as loaded drew the Uber shell (`TERR_UberLOD_Patch21_LOD00`, loaded from 1.6 km, listed by 6 of the 105 hexes inside its box) 2 m over Clear Springs' fine tiles. An unload doesn't settle it either: `Reservoir_Area6_PATCH_10` is unloaded by a hex inside its own box with loading hexes all round. `hex_selection` takes a group from the nearest of 8 hexes that loads it, short of the record's cull distance. The second section (`k` u16 mesh numbers) lists the mesh files of the zone's placed props, not its draw ids. A fine tile, `Mountains_Area01A_Terrain_LOD00_10`, is listed by every hex within 300 m of it and none beyond 600 m. `UberLOD_04B` is listed by none of the 149 hexes within 1 km of it, including the 65 inside its bounds. `TERR_CLRD_ZONE02_AREA6_MIDDIST_02` (mesh 12873) isn't listed within 300 m. So a mesh's nearest listing hex gives the closest distance FH1 ever shows it from. Overlapping ground meshes can be listed by the same hex, so the lists alone don't settle overlaps.

## Draw table (`mt.npy`) flags (working)

Fields `t` (mesh file), `p` (offset to the next record of the LOD ring), `f`, `g`, `s[5]`.

- `f`: the low six bits are always `0x3f`. Above them: `0x100` on all 1,103 `TERR_CUBE` records (occluder boxes, never drawn: `NODRAW`), `0x80` on `*_ShadowCaster_*` meshes, `0x1000` on road markings and tyre marks, `0x200` on some road pieces, `0x800` on most props and some terrain, `0x2000` on records with no mesh.
- `g` bits 12-13: LOD level, bit 11 the ring's top member (see `lod_rings`). Bit 3 marks close-range ground: 64% of those records are listed from hexes in or touching the mesh's box, 3% only from 500 m or more. Bit 4 marks background (27% and 51%), set on every Uber shell piece, `TERR_CUBE` and tree. Bit 9 is clear on every `TERR_CUBE`. Bits 0, 1, 10 and 14 are not understood.
- `s[1]`: a placement's own lightmap. `s[0]` is always 0, `s[2]` 0 or 65535, `s[3]` small counts, `s[4]` mostly 65535: not decoded.

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
- Textures are 256 px DXT5 atlases: 20483 to 20488 and 20490 grass, reeds and flowers, 20489 corn (119 sets), 20491 festival crowd people (113 sets; 256 x 512, 6 rows of 8 people; each set's 48 sprites are the 24 neighbouring pairs, 1 m wide, and the left person of each, 0.5 m). Whole map: 417k triangles, 2.5 km2, about 10.4M sprites without the crowds, at 0.2 to 15 per m2.

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

The radio banks in `media/audio/radio` (`Radio_Music.fsb`, `Radio_VO_<language>.fsb`) are FSB4 holding MP3 instead (sample mode 0x200, bank mode 0x40, MPEG padded to 4 bytes). Each sample's data starts on a 32-byte boundary, so offsets are rounded up between samples. The raw data plays as an `.mp3` file. Sample names are cut to 29 characters.

## Radio (working)

Read by `radio.py`, which builds the separate `fh1_radio` mod.

- `RadioSystem.xml` defines nine stations. `Radio1` is Horizon Bass Arena (DJ Scott Tyler), `Radio2` Horizon Pulse (Holly Cruz), `Radio3` Horizon Rocks (Phoenix Fox), and `Radio4_Silent` is the off position (`isOffStation`). `Radio5` to `Radio9` are music played from a place: pre-race, autoshow, workshop, paint shop and street race. Each station has a `Playlist` of `MusicTrack`s (name, artist, `fmodname`, a weight for each time of day, all 1), plus `Idents`, `DJRegularDialogue`, `DJBookendDialogueIn` and `Out` (all `DJ_Blank`), and `DJFestivalUpdateLeadIn` and `Out`. Lines in regular-dialogue group 0 are general; groups 1 to 9 and 20 to 22 are tutorial and street-race-hub hints. Station names are in `Livery_Decals.str` and `Tips.str`.
- `RadioSoundbankInfo_Music.xml` and `_VO_<language>.xml` give each sample's `SoundBankIndex` in its bank and cue points in ms. For a song, `OffsetSongStart` ends the intro the DJ may talk over, and `OffsetIdentStart` is near the end, where the next jingle or line starts. For a jingle, `OffsetStartNextTrack` is where the next song comes in. `OffsetEventStart` seems to be the drop used at race starts and isn't used.
- `System` sets the levels: music 0.85, dialogue 1, jingles 0.3, ducking to 0.3 under the DJ, and at most one song between DJ breaks.
- The HUD logos are in `ui/textures/Horizon.zip`, `Hud/Radio/`: `RS_BassArena`, `RS_Pulse`, `RS_2Eleven` (Horizon Rocks under an earlier name) and `RS_Off` (a mute icon). They are 210 x 100 tiled DXT5 `.xds` files with format byte 0x75 at 0x23, and the mute icon is 32 x 32. `Radio1` to `Radio3.xds` there aren't logos.
- `RadioDJVoxPops.xml` lists filler interviews for festival updates. Only `AnecdoteInterview4` has English audio.

## `realtimesky.zip` (partly decoded)

Cloud maps are tiled BC5 `.xds`, read by `sky.py`. `Moon0` to `Moon4.xds` are DXT5 (format 0x54) 256 x 256: 0 is a bare disc, 2 the full moon with glow, 1, 3 and 4 crescents. `Sun.xds` and `SkyDither.xds` are A8R8G8B8 (0x86) 128 x 128. `Stars.bin` is the Yale Bright Star Catalogue, brightest first: 9,096 big-endian float quads (x, y, z, w), y towards the celestial pole, RA = atan2(x, z), w the visual magnitude / 7.8, with the four stars brighter than 0 set to 0. Checked on Vega, Rigel, Betelgeuse and Polaris. `CloudDefs.xml` isn't used. In `TimeOfDayA.xml`, `SunPos` stays at least 17 degrees up all day: night is drawn by colours, `StarFade` and `SunObjectMoon`, not by the sun setting.

## `animatedobjects.zip` (partly decoded)

288 of 290 `.pgeo` files read through `gamezip`, with 361 `.xds` textures. Two fail their checksum or decode: `ANIM_Showcase_Event_Helicopter_Race`, `ANIM_Timed_Event_Biplane`. These are `OEGP` version 44, unlike the version 42 sets above: the payload is a RAD Granny file without its usual magic, holding type definitions by name (`Skeletons`, `Bones`, `VertexDatas`, `TriTopologies`, `TrackGroups`). Curve types seen: `D4nK16uC15u`, `D4nK8uC7u`, `D3K16uC16u`, `D3K8uC8u`, `DaK32fC32f`, `D3Constant32f`, `D4Constant32f`, `DaConstant32f`, `DaIdentity`, `DaKeyframes32f`, and the `D3I1`, `D9I1` and `D9I3` families.

Models sit around a local origin. The track has its own copies, `__R00G00000` to `__R00G00032`, named `Anim_<model><n>` at 0x60, such as `Anim_ANIM_WaterSprinkler17`. Their bounding box at 0x10 covers the whole animation, route included. Six festival rides, the hot air balloon, the inflatable ship and spray gun and the tube men (`ANIM_Airwalker`) exist only in these, not in `animatedobjects.zip` or as `.rmb.bin` meshes.

Vertices are 12 bytes in most models and 16 in the Ferris wheel (half-float position, then 4 zero bytes and 4 more). The Granny type list has no `Position` or `TextureCoordinates` member names: the vertices are Forza's own format, and Granny holds only the skeleton and animation. In the 12-byte vertex, bytes 6-7 are U as a big-endian half-float, used as is, and bytes 8-9 are V as a big-endian s16, `v = 0.4977 + s * 1.5395e-5`, about `0.5 + s / 65000`. Bytes 10-11 are constant within a triangle, so they're probably a packed normal. Found by matching the vertices of `ANIM_FARM_WindmillWood` and `WindmillMetal` to their catalog meshes (6627, 6625; Granny z flipped, 81% within 2 cm) and fitting against the catalog UVs with their box applied: U fits with a scale of 1.000 and an error of 0.0002, V within 0.0025 on both windmills. The crop duster's UVs then land on the islands of its texture `4FA4`. U runs to 2 on the crop duster; wrapped, it still lands on the texture. Skinned vertices are below. Textures aren't in `animatedobjects.zip`. They're in the Colorado track pool, in a block of IDs no world mesh uses, about `0x4F99` to `0x5027`. Each track copy (`__R00G000xx.pgeo`) lists its own in its header as 8-byte entries ending in a u16 texture-list index that `texshader.pkl` turns into a texture ID: the count is the word at 0x40, and the first u16 sits 2 bytes past the name's end rounded up to 4 (bytes before it can be left over from a longer name). Ferris `4FA8`-`4FB5` (`4FAE` and `4FAF` are the Horizon Wheel sign, by day and by night), balloon `4FB6`-`4FB7`, inflatable ship `4FB8`-`4FB9` (spray gun `4FB8`), Zipper `4FBA`-`4FBF`, Booster `4FC0`-`4FC8`, Fan `4FC9`-`4FD1`, NoFear `4FD2`-`4FDC`, Roundup `4FDD`-`4FE6`, crop duster `4FA4`-`4FA5`, combine `4FA2`-`4FA3`, stage lasers and lights `4FE7`-`4FEF`, sprinklers `4FF1`. `4FB4` (shared by several rides) and `0DE1` turn up in several lists. Draw nodes say which entry each part of a mesh uses (below). Most of the other unused track textures are 64 to 512 px night lightmaps of buildings.

Placement is in 202 `anim_proc_*` version 44 sets, also `__R00G*.pgeo`, a few hundred bytes each. The name gives the object group, such as `fx_windmillswood`. The word at 0x44 is the record count. Records are 128 bytes and run to the end of the file. The last record's position is at `len - 0x50`: 12 rotation floats, 3 position floats, 1.0, a model index, 3 LOD distances. Positions use the `.pgeo` z sign. The model index is local to the set, and the set doesn't name its models. But a set's box at 0x10 is exactly the union of its records' model boxes, rotated and moved, so `placed` in `animsets.py` finds each model by fitting them: all 202 sets fit to within 1 mm. Models with the same box can't be told apart this way, such as firework colours, and nor can a small model in a set with a large one: the main stage's 30 extra records read as tube men. One set (`clrd_golf_watersprinklers_001_11`) also holds a stray row that reads as a record at the origin, so records must lie near the set's box. By model, Colorado places about 190 tube men, 38 sprinklers, 8 combines, 6 tractors, 4 balloons, 6 rides, 4 inflatables, 11 wood and 8 metal windmills, 8 turbines, 1 crop duster, 1 geyser, 12 fireworks, stage and autoshow lights and 117 birds. Sets named `heli` and `windymen` hold tube men, not helicopters. No set places a helicopter, banner plane, cable car or camera crane.

Track copies, worked out 2026-11-04 (`granny2.py`): each file is the `.pgeo` header, then a whole Granny 2 file, then the geometry. The Granny file starts with its 16-byte magic (`b595110e 4bb5a56a 502828eb 04b37825` on the balloon), header size, then file info: version 7, total size, CRC, section array at 0x48, 8 sections of 11 words. Section 0 holds the objects, section 6 the type definitions, and both have real pointer fixups (12 bytes each: slot, target section, target offset), so `granny2.File(b).tree()` walks the root by member name. Its `Meshes`, `VertexDatas` and `TriTopologies` are empty and the model's `MeshBindings` are null: Granny carries only `Skeletons` (named bones with parent and transform), `Animations` and `TrackGroups`. The 16-bit region of section 0 is animation keys (`KnotsControls` of the `D9I1K16uC16u` curves), not geometry. The geometry is the tail after the Granny file, its size the word at 0x50: every mesh's u16 indices, each padded to 4 bytes, then every mesh's vertices, in the same mesh order. (With one mesh that reads as indices then vertices, as first found on the balloon.)

The `.pgeo` header, decoded 2026-11-04 (`animcopy.py`), holds a 17-word record per mesh (0x44 apart), then each mesh's draw nodes. Draw nodes are 11 words: four texture slots numbering the texture list (-1 for none), first triangle, triangle count, three zeros, then a switch distance and a fraction such as 10.0 and 0.1 (75.0 and 0.01 on some). The slots are colour, unused, glow and specular: the Booster's third slot is its lamp texture, black with coloured bulbs, and the fourth textures are grey. A mesh's nodes run consecutively and start at triangle 0; one extra node may follow out of order. In the mesh record, the vertex count sits two words before the index count, then the draw count (the node count, or one fewer) and two flags. The word before the vertex count is the bone in the mesh's model's skeleton, or -1 for a skinned mesh, and the word before that is 5, 3 or 0 (not decoded). Runtime pointers fill the rest, and strings from other header blocks sometimes overwrite the first words. Records can only be found by matching the index count against 3 times a tree's triangle count, with the draw count after it: a loose match on small meshes (the blimp's 4-triangle boxes) finds false ones.

Vertices are 12 bytes for rigid meshes (above) and 16 or 20 for skinned ones, whichever makes the tail add up. All have the position as 3 half-floats and U as a half-float at byte 6, and V as the s16 4 bytes from the end. 16-byte vertices (Ferris wheel, Fan, Booster) hold four bone numbers at bytes 8-11, all equal in the rides, so each vertex follows one bone. 20-byte vertices (the inflatables and tube men) hold four u8 weights at 8-11 and four bone numbers at 12-15, and blend. Bone numbers count within the mesh's model's skeleton. These files have several skeletons (`Skeletons`, one per `Models` entry, each with a `TrackGroup` of the same name), and the sound markers (`AUDIO_Trigger`, `TRG_*`) are skeletons of their own. Meshes are stored model by model, so the split of the mesh list into models is the one that keeps every bone number inside its skeleton and puts the most rigid meshes on bones named for a LOD (`..._LOD00`). That puts every mesh of all nine models on a bone whose name and position match it: the Ferris wheel's 16 gondolas (478 vertices each) on 16 bones, the Roundup's 14 meshes on `ANIM_ITLY_Ride_Roundup_00n_LOD0m`, the Zipper's 54 on its cars' bones. Detail levels come from the bone names, or for skinned meshes from their size.

Rigid meshes are stored in model space, in the bind pose, which `InverseWorldTransform` gives. A part's motion is its bone's world transform times the inverse of that. World transforms are each bone's local transform (position, then orientation, then scale-shear, for column vectors) times its parent's. Built from each bone's `Transform`, they give back the inverse bind transforms on the Fan and balloon. On other models the rest transform is another frame than the bind pose, which doesn't matter as only the bind pose is used. Quantised quaternions (`D4nK16uC15u`, `D4nK8uC7u`) decode as Granny's own: each nibble of `ScaleOffsetTableEntries` picks a component's scale and offset from fixed tables of multiples of 1/sqrt 2, the top bits of the second and third u16 say which component was dropped, and the first u16's top bit gives its sign. Checked: the Ferris wheel turns exactly 360 degrees about its axle per 133 s loop, and the Zipper's arm 720. The gondolas stay within 10 degrees of upright, and every loop closes. Curves are clamped B-splines of the header's degree: each control has a knot, with the end knots repeated. Read with straight lines between controls, the Ferris wheel's degree-2 curve (controls 4 s apart) speeds up and slows down at each one. Curves open with a second knot a few milliseconds after the first, a small step at the loop's seam.

These files barely use pointers, so they can't be walked as normal Granny files. `DaK32fC32f` curves are stored as raw big-endian floats: rising knots, then the controls, which `route` in `animated.py` finds by scanning. Granny axes are x, up, z, and z maps to BeamNG local y unflipped. Curve headers start with a format byte, such as 10 for `D3K16uC16u`.

A `D3K16uC16u` header is: format 10, degree, the top 16 bits of a float 1 / knot scale, 3 float scales, 3 float offsets, then the count of u16 values (4 per knot). The values sit elsewhere in the file: knots rising from 0, then 3 controls per knot, each `u16 * scale + offset`. Other u16 runs rise from 0 too, so `quantised_route` takes the run whose controls make the smoothest path. The combine's is 436 knots over 84 s (1 / knot scale 780): rows across a field, x -8.8 to 56.8 m and z 0 to 22.9 m, y constant. With the same quarter turn as the tractor, the route and the turning body fill the model's box to within 0.4 m, which fixes its offset.

People models, worked out 2026-11-06 (`animcopy.meshes(b, flags=True)`, `people.py`): the Fest_*, Street_*, Driver_*, Nemesis_*, Barn_find_* files differ from the rides in four ways. The three words before a draw node's switch distance hold small flags, such as (0, 0, 2) or (1, 0, 0), not zeros. A node's fraction may be 0.0, on nodes that carry on the one before. Skinned vertices may be 24 bytes: the 20-byte layout (weights at 8-11, summing to 255, bones at 12-15, V at 16) and 4 zero bytes. And one file mixes sizes per mesh (a 24-byte body, a 20-byte hair mesh, 16-byte rigid props), so sizes are solved to fill the tail, skinned 16, 20 or 24 and rigid 12 or 16, keeping the one whose skinned vertices read as valid weights. The Granny file's `VertexDatas` and `Meshes` are empty, but each `Models` entry's `MeshBindings` count says how many meshes, in order, belong to that model's skeleton: heads (`*_Bone_head`), phones and cameras are rigid meshes on their bone. Bone numbers count in the model's own skeleton. Textures are `<name>_D` (colour, the first slot), `_N`, `_S` in `animatedobjects.zip/Textures`, named without regard to case (`male05` for `Male05`). FH1 places these only through cutscene `CCutsceneAnimationTrigger` events (`gamemodes.zip`, `Colorado/Cutscenes/*.xml`): `filename`, `animname`, `x`, `y`, `z` (`.pgeo` z sign), `rotation` in degrees, `loop`, `duration`.

Spectators (`Spectators.zip`, read by `spectators.py`): `spectators.xml` ties 19 `.skinbin` models (`<model>_Sit`, `_Stand`) to 20 textures and 56 `.anim.bin` cheer and idle animations, in crowd classes, and bakes `sprites.xds` at 10 angles. Not Granny. A `.skinbin` is big-endian: u32 pairs (1, value), the second the LOD count (4), then a (1, first << 16 | count) pair per LOD, (1, 1) and the vertex count. Vertices are 36 bytes: u32 1, position and normal as 3 floats (y up, front +z, feet at 0), U and V as u16 / 65535, two bone bytes and two weights summing to 255. Then (1, index count) and a u16 triangle list, which the LOD pairs cut up (LOD0 about 1,300 to 1,750 triangles, LOD3 about 40). No skeleton: the vertices already hold the pose, standing or seated. Textures are 256 px tiled DXT1 `.xds` (format byte 0x52), read by `tex.decode`. `.anim.bin` isn't decoded. `tracks/colorado/Ribbon_00/Colorado_track_00.crowd` is empty (version 2, no entries). They're race-day spectators: the soundscape tiles' `Crowd` points carry a `routeID`. Of the 6,381 `Crowd` points (soundscape z sign, as `Object`s: BeamNG (x - CX, z + CZ), height y), 615 lie on the festival ground with an empty `routeID`, about 11 m apart; the rest line race routes. They sit on the ground (97% of a sample within half a metre), so `spectators.py` stands a spectator on each.

Some of the same objects also have static zone records, such as `PLA_FARM_WindmillMetal`, `GLOB_STFN_WindTurbine_02`, `Tractor` and `Gondola`. `ANIM_Cablecars` isn't among the track's model sets.

## `.fxobj` shaders (working)

`shaders_track_*.fxobj` in the track's `bin.zip`, one per shader name, read by `xenos.py`. Each holds several compiled shaders (passes: plain, shadowed, depth), each with a header (`0x102A1101` vertex, `0x102A1100` pixel). A D3DX constant table follows the header at +0x28. Header word 6 points to an info block, whose first two words are the ucode's offset past header word 1, and its size. In a vertex shader the info block's word at +0x1c counts the vertex fetches, and from +0x28 one word each gives the fetch's instruction address (low 12 bits), D3D usage (4 bits) and usage index (4 bits). The ucode is Xenos, as Xenia's `ucode.h` lays it out. Vertex fetch formats, offsets and strides are blank, because the game patches them from the vertex declaration.

What `H_DIFF_SPEC_AO_2_LM` does, which the other `_lm` object shaders share:

- Positions are scaled and offset: `positionScale` c1, `positionOffset` c2.
- Both UV sets go through `uvOffsetScale` (c0, xy offset, zw scale), the section's UV box: `o2 = (uv0, uv1) * c0.zwzw + c0.xyxy`.
- In the pixel shader, the diffuse (`tf0`) reads `o2.xy`, tiled by c0. The AO map (`tf5`) and lightmap (`tf7`) read `o2.zw`. Sampler numbers match material slots.
- Lighting: sun colour x shadow mask, plus `ambColor` (c221) x AO, plus lightmap x max(`EmissiveSwitchOnThreshold`.y (c213), c6.z), plus headlights. Colour is albedo x lighting plus specular, then fog. The lightmap is light falling on the surface, times its albedo, not a glow added over it.
- Output is `sqrt` of the result: lighting happens in squared space.
- `H_DIFF_SPEC_REFL_EMISSIVE_AO_4_LM` (443 materials) has no emissive sampler: it adds a window mask from the specular map's green (tf4) over a 0.5 threshold, in its c254 colour (1, 0.5, 0.32), times `EmissiveSwitchOnThreshold.z`, switched per object by `EmissiveSwitchOnThreshold.x >= ModelData.x` (c148), so buildings light at different times. Literals c252-c255 sit just before the ucode: (8, 0, 1/256, 0.05), (-0.5, -0.25, 0.5, 0.95), (0.5, 0.32, 0.81, 1), (0.3, 0.59, 0.11, 1.5). The threshold and colour run through one scalar-constant op whose register reading is likely but not checked. Other `*_emissive*` shaders have `EmissiveMapSampler` at s3.
- Road shaders (`H_ROAD_DIFF2_NORM1_NOISE_MODULATE_AO_LM`): the colour vertex (color0, last 4 bytes) reads x, y, z, w = bytes 1, 2, 3, 0. Layer blends use x with the noise; the colour is times w squared, times lerp(1, modulate, z) with the modulate map (s6) at UV1 x c360_2, times 0.05 + 0.95 AO (s5) on both sun and ambient. AO and lightmap read TEXCOORD2 through the UV box (`o3 = uv2 * c0.zw + c0.xy`); that's why roads' lightmaps fit a top-down projection.
- Layer blends, read from `H_ROAD_DIFF3_NOISE_AO_LM`, `H_ROAD_DIFF2_NOISE_BLEND_AO_LM` and `H_VBLND_AO_LM` (proven from the ucode, 2026-11-04). Layers tile UV0 (after the box) at their own scales from the material's constants: A (s0) at `c360_0.xy`, B (s1) at `c360_0.zw`, C (s5) at `c360_1.xy`, the noise (s2) at `c360_1.zw`. B's weight is `saturate(c4.x (x - 1/2) + c4.y (noise.r - 1/2) + 1/2)` and C's `saturate(c4.z (y - 1/2) + c4.w (noise.g - 1/2) + 1/2)`, c4 = `c360_4`; colour = lerp(lerp(A, B, tB), C, tC), then times w squared and AO as above. `h_vblnd` has no noise: `saturate(c4.x (x - 1/2) + 1/2)`, and the same w squared and 0.05 + 0.95 AO, AO at TEXCOORD2. Skirts (`*_blend`) output alpha `(1 - z)` times an interpolator that is likely a distance fade. Common values: road `c360_0` (1, 1, 1, 1) or (0.75, 1, 1, 1), noise scale 0.1 to 4, `c360_4` (2, 1, 2, 1) or as steep as (50, 40); `h_vblnd` layers often tile 2x or 4x.
- Literals c252 to c255 are the 16 floats before each shader's ucode, and differ per shader: c253 = (-0.5, 0.05, 0.33, 0), c254 = (0.5, 0.95, 0.1, 1) in the diff3 road shader, but c254 = (1/256, 0.05, 0, 0.33), c255 = (0.5, 0.95, 0.1, 1) in the skirt and vblend ones.
- Scalar constant ops (`mulsc`, `addsc`, `subsc`) read as Xenia does: the constant's component from swizzle bits 6-7 less one, the temp's from bits 0-1, one negate flag for both. `xenos.py` read them wrongly before 2026-11-04. Checked: the skirt shader's noise operand lands on the register its noise was fetched into, and the shadow lookup's y becomes 1 - y.
- Ground `h_blnd*` shaders read their AO map (s5 or s6), lightmap (s7) and splat mask (s2) at the same coordinates.
- The night colour grade (`ColorGradingLookup_Night.dds`, a 16^3 RGBA cube, `NightColorGradeAmount` 1 from 20:02) doesn't brighten: grey 119 becomes (99, 126, 135). FH1's night light is its sun at SunColorMult 0.15 in (0.094, 0.136, 0.268) and a sky ambient near 4% of midday.

Every `_lm` shader's pixel shader declares `Light_MapSampler`. None declares the `projTrackLightmap` constants named in `reference.fxobj`. Ground and road vertex shaders fetch texcoord1 as well, and roads also texcoord2 and a colour.

## `*.fiz` collision (working)

3,201 tiles of about 100 m, 2.0M triangles, read by `fiz.py`. Offsets count from byte 0x20: reading them from byte 0 shifts every index by two vertices, which made triangles spike to the origin and looked like mixed winding. Read correctly, every stored normal agrees with counter-clockwise winding in game axes. Contents: road surfaces, shoulders, car parks and town streets (about 7% of the Clear Springs square), no terrain, and FH1's invisible walls along every road edge and around the golf course, 50 to 65 m tall (flag groups `0x803`, `0xc03`, `0xc09`, `0xc0f`, `0xc2c`, all steep). Group `0x800` is steep and about 13 m tall, along medians and in towns: not identified. The flags' high 16 bits group triangles; the rest isn't decoded. Header words 16 to 22 describe a 10 m lookup grid after the triangles.

## Not decoded

Normal maps and lightmaps, water, and the owner-block list bytes (see `free-roam.md`).

## CAFF textures

About 2,400 live textures are `_0x<id>.bin` files with no `.bix`, read by `tex.caff`. Most are 64 by 64 AO bakes, and `0x1EE4` is the 768 by 128 trim atlas that armco and barriers use as their diffuse. Layout: a 0x190 byte header, `.data`, then `.gpu`. Word 0x60 is the `.data` size. In `.data`, 0x18 is the same format word as a BIX, 0x1C the kind (2 is a cube map), and 0x24 the width and height as u16. `.gpu` starts with the top mip, tiled as in a BIX.

## Primitive types

A section's `ints[1]` is the Xenos primitive type: 6 is a triangle strip with restarts, and 4 is a plain triangle list. 174 sections are lists, mostly road blends, road pieces and bridges, plus terrain such as `Mountains_Area04A_Terrain_LOD00_03`. `rmb.tris` takes it as its third argument.
