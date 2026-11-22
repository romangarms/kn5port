# Forza Motorsport 4 tracks

Porting FM4's circuits with the FH1 pipeline's readers. `fm4build.py` builds a first, plain level (see Build); the rest is how FM4's files read and what differs from FH1. Confidence words are as in `formats.md`. Readers are in `fh1/fm4/`, and extracted data goes in `fh1/fm4/<track>/` (gitignored).

The dump is `D:\Roms\Xbox 360\Xenia\FM4XeniaFix` (read-only), with 24 track folders and 3 GB of tracks in `Media/Tracks/`. Checked on Tsukuba and Laguna Seca, 2026-11-22.

```
python fm4build.py Tsukuba [ribbon]                                 extract if needed, then build mods/unpacked/fm4_tsukuba
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

## Materials (proven)

The block after a file's meshes: six words, the sixth the material count, then one record per material: `3, shader, 0`, then three groups, each a count c and c arrays of (n, n items). They hold vertex shader float4 constants, pixel shader float4 constants, and texture slots. FH1's records are the case with no vertex constants. `shader` indexes the file's `shaders	rack\<name>.fx` list, and a section's `ints[2]` is its material. Read by `fm4mesh.materials`, checked on all 1,088 Tsukuba files.

Slot k is sampler register k of the shader's pixel shader, whose constant table names it (`xenos.shaders`, register set 3). `Diffuse_TextureSampler` (s0 in most, s2 in `*_refl_*`), `Light_MapSampler` s7, `NormalMapSampler`, `SPECSampler`, `CubeMapSampler`; ground shaders (`road_blnd_*`, `shldr_blnd_*`, `terr_blnd_*`) blend `Blend_A`, `Blend_B`, `Blend_C` by `Blend_Value`; `diff_clr_*` tint through `Paint_Mask` and `Palette`. A slot value indexes the mesh record's texture list; 0xFFFFFFFE is empty. In a pit building, slot 7 holds key 1, its lightmap atlas.

## Draw transform (working)

Meshes stored in world space have draws with rotation diag(1, 1, -1) and position 0: a draw's frame has z flipped. The rotation applies as a row vector, `v @ R + p`, as Direct3D does. Rotated props sit closer to world-space geometry that way on 80% of Tsukuba's 599 rotated draws and 57% of Laguna Seca's 3,778. From draw space, BeamNG is (x, z, y). For a TSStatic, the shape's vertices are written as (x, -z, y) of the game's, and its matrix is `P @ R.T @ B.T`: P swaps y and z, B is FH1's. Column norms of that matrix are its scale. LODs are separate draws at one spot, one per `_LODnn` mesh.

## AI line and grid (proven)

`Media/AI/Tracks/<track>_<ribbon>.geo` is an MLP file (`fm4ai.py`): a text header lists each field as `name:type:count:?:offset`, with absolute offsets and big-endian data. `fWaypointX/Y/Z` is the centre line every 2 m (1,056 points on Tsukuba), in BeamNG's own axes (x east, y north, z up). Checked: with mesh z = -Y the waypoints sit on the road at a median height difference of 0.00 m. `fNormalX/Y` is a horizontal vector pointing left, about the half-width long; the `*Chi` fields are lateral positions in its units (walls, kerbs, the racing line). `gamedb.slt` `Tracks` (by `MediaName`, `RibbonIndex`) gives `StartLine` as a lap fraction and `DefaultStartGridPositionsId`, and `StartGridPositions` gives each slot's metres back from the start line, metres right of the centre line, and yaw.

## Build

`fm4build.py` writes one shape per mesh file (`art/shapes/fm4/m<no>.dae`, through FH1's `daewrite.write_lod`), one TSStatic per draw, keeping each object's most detailed LOD. Materials are the diffuse texture only: `Diffuse_TextureSampler`, or `Blend_A`/`Blend_B` on ground shaders. They are alpha-tested on `*opac*` shaders whose texture has cut-out alpha, and double-sided on `*2sd*`. Textures are written as DXT1 `.color.dds`, keeping FM4's own DXT1 blocks, plus an `_o.data.dds` opacity map; format 0x7B (BC4) decodes through `tex.bc4`. Collision is each shape's own geometry, except vegetation, flags, road-line decals, cut-out `opac` shaders (barriers aside), sky and flares. Ground types: `road_*` and `shldr_*` asphalt, `terr_*` grass, `rdedg_*` dirt. The spawn is grid slot 1 off the AI line.

Tsukuba, 2026-11-22: 1,470 TSStatics (766 collide), 1,019 shapes, 260 materials, 251 textures, 437k triangles, 95 MB, built in 31 s. Not yet seen in-game.

## Not decoded yet

- **Blend layers.** Ground shows one layer. The constants that tile each layer, and the `Blend_Value` mask, are unread. FH1's `h_road`/`h_blnd` analysis (`formats.md`) is the place to start.
- **Lightmaps and tints.** Slot 7 lightmaps are atlas tiles (texture table parent, scale, offset); `Paint_Mask` and `Palette` tint `diff_clr_*`.
- **Cloth flags** (44 meshes on Tsukuba) have no layout yet, as in FH1.
- **`<track>_track_NN.col`** (1.7 MB on Tsukuba, against FH1's 78 KB): a header with two 100 m grid sizes and a 0x1AB000 table, likely the physics surface. The visual meshes collide for now.
- **The FPVS draw record** beyond mesh, position and rotation: the u16 at 4 varies with LOD (0x0A14 LOD0, 0x7A14 LOD1) and isn't decoded, so neither are fade distances.
