# Forza Motorsport 4 tracks

Research toward porting FM4's circuits with the FH1 pipeline. Nothing is built yet: what's below is how far FM4's files read with FH1's code, and what differs. Confidence words are as in `formats.md`. Readers are in `fh1/fm4/`, and extracted data goes in `fh1/fm4/<track>/` (gitignored).

The dump is `D:\Roms\Xbox 360\Xenia\FM4XeniaFix` (read-only), with 24 track folders and 3 GB of tracks in `Media/Tracks/`. Checked on Tsukuba and Laguna Seca, 2026-11-22.

```
python fm4zip.py "<FM4>/Media/Tracks/Tsukuba/bin.zip" tsukuba      extract to tsukuba/x
python fm4mesh.py tsukuba                                           check vertex layouts over every mesh
python fm4top.py tsukuba "<FM4>/Media/Tracks/Tsukuba/Ribbon_00/tsukuba_00.pvs" top.png
```

## Track folder

`bin.zip` holds the meshes, textures and shaders for every layout. Each `Ribbon_NN/` is one layout, holding its own FPVS (`<track>_NN.pvs`), `<track>_track_NN.col`, `.crowd`, `collobjs.xml` and `collobjsplacement*.xml` (cones and the like, `PhysicsType` plus position and axes), and camera XMLs. `<Track>_ribbonNN.sh` and `TrackSettings.xml` sit beside them. There is no `.hex`, nav or zone streaming: a ribbon's FPVS places everything at once. AI files are in `Media/AI/Tracks/<Track>_NN.{geo,irt,seg,pit}`.

## `bin.zip` (proven)

FH1's format: the data offset is in extra field 0x1123, method 21 is LZX window 17, and `lzxdec` decodes it. Every entry appears once, unlike Colorado's per-zone repeats. Tsukuba has 3,302 entries and Laguna Seca 4,280, with CRCs all good. Contents: `<Track>out.NNNNN.rmb.bin` meshes, `_0x<id>.bix` plus `_B.bix` textures, `_0x<id>.bin`, and `shaders/track/*.fxobj`.

## Meshes, `*.rmb.bin` (proven)

FH1's version 6 format (`rmb.py`) with two differences, read by `fm4rmb.py`:

- No pad word after the vertex stride: the header is name, `t` (2, against FH1's 3), vertex count, stride, then the vertices.
- A section's index header is `3, count, index size`, against FH1's `4, 0, count, index size`.

All 1,088 Tsukuba files (1,386 meshes) and 1,342 Laguna Seca files (1,643 meshes) parse. Positions are float3 at byte 0 and fall inside each mesh's box (`c + lo` to `c + hi`) in every mesh. A section's `fl[4:8]` looks like FH1's UV box (lo, size); this isn't checked yet.

**Vertex layout (proven).** FH1 has one layout per stride. FM4 strides run from 12 to 40 bytes, and one stride can hold different layouts: grass at 28 bytes is position, uv0, uv2, normal, colour, but a cone at 28 is position, normal, uv0, tangent. The layout is the shader's vertex fetches (`xenos.shaders`, bindings) packed in fetch order. Position takes 12 bytes, tangent 8 (a second packed vector follows it, likely the binormal), and everything else 4. Normals and tangents are FH1's 10:10:10, UVs u16 / 65535. A mesh drawn by several shaders uses the layout of one whose sizes sum to its stride. The material block names each file's shaders as `shaders\track\<name>.fx` strings, so no lookup table is needed. Where two fit in different orders (21 meshes on Tsukuba), the one whose normals read as unit vectors wins. Checked: on both tracks every mesh with a layout has unit normals and tangents, median |N·T| under 0.001. No layout fits 21 meshes on Tsukuba and 24 on Laguna Seca, all cloth flags (`anim_flag_*`), as in FH1.

## Textures, `.bix` (proven)

`tex.py` reads them unchanged: 913 of Tsukuba's 954. The other 41 are format 0x7B, Xenos DXT5A (BC4, one channel; low 6 bits 0x3B), which FH1 never used. `tex.bc4` decodes its blocks, but `tex.decode` and `blocks` don't list the format yet.

## FPVS, version 27 (working)

FH1's is version 50. Read by `fm4pvs.py`. The tables come in order, found from the first shader path:

- **Textures:** u32 count, then 28-byte records: id, parent, scale u, scale v, offset u, offset v, kind. The id is the `_0x<id>.bix` file. Ids with no file (19% on Tsukuba) are sub-rectangles of `parent`, for example id 1476 is a 1/16 tile of 613: lightmaps are packed into atlases.
- **Shaders:** u32 count, then length-prefixed `shaders\track\<name>` paths (56 on Tsukuba).
- **Draws:** u32 count, then 68-byte records: u16 mesh at 0 (the `<Track>out.<mesh>` file), float3 position at 38, and a 3×3 rotation as nine float16s at 50. Rotations have determinant -1 (FH1's axis flip). A few dozen have |det| under 1, scaled placements. The u16 at 4 takes a few values (0x0004, 0x0214, 0x7a14) and isn't decoded. A top-down render of Tsukuba's 1,718 draws and Laguna Seca's 5,237 shows each circuit's layout.
- **Meshes:** u32 count, one record per mesh file: u32 n, n texture keys, u32 m, m keys, then 80 bytes holding a matrix. The first list is the mesh's texture list: a key indexes the texture table, and the material slots index this list (checked by eye: the cone gets its orange texture, the coach its livery, the pit building its Bridgestone facade). The second list (1 to 7 keys) points at atlas tiles, likely lightmaps. All 1,091 Tsukuba and 1,343 Laguna Seca records parse to a consistent end. About 70 KB follows on Tsukuba and isn't read.

## Not decoded yet

- **Material records.** A file's material block differs from FH1's. On the cone: `5, 1, 1, 1, 1, 1, 3, 0, 0, 1, 0, 1, 2`, two float4 constants, `1, 3, 0, 1, 2` (likely the slot list), `1, 1`, then the length-prefixed shader path, then per-pass shader parameter tables. Which section uses which material isn't known.
- **`<track>_track_NN.col`** (1.7 MB on Tsukuba, against FH1's 78 KB): a header with two 100 m grid sizes and a 0x1AB000 table. It's probably the track surface for physics. Visual meshes can stand in, as for FH1.
- **AI and grid.** `Media/AI/Tracks/*.geo`, `.seg`, `.irt`, `.pit` per ribbon; grids are said to be in `gamedb.slt`.
- **Coordinates** are assumed to match FH1's (x east, y up, z south); not checked in-game.

## Toward a build

FH1's `build3.py` is built around zones and hexes, which FM4 lacks. An FM4 track is closer to a small, fully loaded FH1 chunk. Next steps, roughly in order: material records and section-to-material mapping; the slot meanings per shader family (`diff_spec_*`, `road_blnd_*`, `shldr_*`, `terr_blnd_*`, mostly like FH1's `h_*`); a minimal exporter (meshes and diffuse only) into a test level; then collision, spawn points and AI lines.
