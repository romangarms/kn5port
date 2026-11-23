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

## Ground blending (proven from the shaders)

Read from the ucode with `xenos.py`. In a pixel shader, register rN is the vertex shader's output oN. Every texcoord goes through the section's UV box (`uvOffsetScale`, c0) in the vertex shader, as in FH1. Layers mix by the **green** of the `Blend_Value` mask:

- `road_blnd_*`: colour = B(world x, z × `c360_0.x`) × mask.g(texcoord2 × `c360_0.yz`). B tiles in world space, 2 m at Tsukuba's 0.5. The mask is wear and tyre marks over the road piece, not a mix between layers. The normal map is at world x, z × `c360_1.x`.
- `terr_blnd_*`: colour = lerp(A(texcoord0 × `c360_1.xy`) × `c360_0.rgb`, B(texcoord0 × `c360_2.xy`), mask.g(texcoord2)).
- `shldr_blnd_spec_norm_4` and `_nocube_4`: colour = lerp(A(texcoord1 × `c360_0.xy`), B(texcoord1 × `c360_1.xy`), mask.g(texcoord0)) × vertex colour (bytes 1 to 3) × `c360_0.z`.
- `shldr_blnd_spec_decal_norm_5`: A at texcoord1 × `c360_0.yz`, B at texcoord1 × `c360_1.xy`, C at texcoord2 × `c360_2.xy`; colour = lerp(A, lerp(B, C, vertex alpha), mask.g(texcoord0)) × vertex colour × `c360_0.x`.

The lightmap (s7) reads the mask's UV set through `Light_Map_uvOS` and is tinted between `V2LightmapColor1` and `V2LightmapColor2`. Lighting is in squared space, as in FH1. In BeamNG (`fm4build.ground_material`): layer 0 is A on UV0 with its tiling baked in (B on roads); layer 1 is B, its opacity the mask's green (a BC5 data map) on UV1, through a half-colour detail map when B tiles at another scale (FH1's `half_color`); roads get a black layer at opacity 1 - mask.g. Shoulders carry the vertex colour as a grey `vertColor`. The decal shoulder's third layer is left out. `fm4ground.py` renders the ground from these formulas, top-down, for checking.

Most of Tsukuba's grass is not ground-blended: it is `grass_*` and `diff_*` object shaders. Only 2 sections use `terr_blnd`.

## Collision, `<track>_track_NN.col` (proven)

Read by `fm4col.py`. A 7 × 4 grid of 100 m cells on Tsukuba, with 19 occupied. The header gives the counts, bounds and cell size, then from 0x58 one 24-byte entry per occupied cell: index, cell, offset, size, 0, end. A cell block is a 0x40 header (vertex count, triangle count, vertex offset 0x40, triangle offset, a 12 × 12 sub-grid of 10 m with its origin and offset), then 32-byte vertices (float3 position, a packed word, padding) and 32-byte triangles (u16 surface id, three vertex byte offsets within the cell, float3 face normal). The 10 m sub-grid lists after the triangles and isn't read. Tsukuba: 28,907 vertices, 22,073 triangles. The stored normals agree with the winding on every triangle.

Positions are in the draw frame (mesh z flipped): matched that way, triangle centres sit a median 0.15 m from visual triangle centres, against 4.5 m unflipped. In BeamNG, (x, z, y). That mapping is a reflection, so `fm4build` reverses triangles that would face away from their stored normal.

The surface id indexes the children of `SurfaceTypes` in `Media/physics/surfaceTypes.xml`, 61 of them, each with friction and off-road settings. On Tsukuba: 0 Asphalt (road), 20 AsphaultNoPenalty (pit lane), 21 Asphault2NoPenalty, 6 RumbleStrip (kerbs), 1 Dirt and 2 Grass (runoff), 3 GuardRail, 4 ConcreteBarrier (pit wall), 11 RubberTire and 19 RubberTireSpringy (tyre walls), 33 FoamBarrier, and 9 Invisible: 3,620 walls about 117 m tall along the track limits, as FH1 has. Each id was checked against the visual family nearest its triangles.

`fm4build` writes each cell as a collision-only shape (`col<cell>.dae`), one material per surface carrying a BeamNG ground type (`COLGROUND`: asphalt and concrete ASPHALT, RumbleStrip RUMBLE_STRIP, Grass GRASS, Dirt DIRT, Gravel GRAVEL, Sand SAND, guardrails METAL, tyres and foam RUBBER). Invisible walls are left out unless `FM4_WALLS=1`. Where the `.col` exists, `road_`, `shldr_` and `rdedg_` visual meshes don't collide. Everything else (buildings, terrain, fences) keeps its own mesh collision. The `.col` covers only the circuit and runoff: 9 ha of flat surface on Tsukuba.

## Build

`fm4build.py` writes one shape per mesh file (`art/shapes/fm4/m<no>.dae`, through FH1's `daewrite.write_lod`), one TSStatic per draw, keeping each object's most detailed LOD. Object materials are the diffuse texture only (`Diffuse_TextureSampler`); ground shaders are layered, see Ground blending. They are alpha-tested on `*opac*` shaders whose texture has cut-out alpha, and double-sided on `*2sd*`. Textures are written as DXT1 `.color.dds`, keeping FM4's own DXT1 blocks, plus an `_o.data.dds` opacity map; format 0x7B (BC4) decodes through `tex.bc4`. Collision is FM4's `.col` (see Collision) plus each shape's own geometry, except vegetation, flags, road-line decals, cut-out `opac` shaders (barriers aside), sky, flares, and the ground families the `.col` covers. The spawn is grid slot 1 off the AI line.

The backdrop is the sky dome, `<TRACK>_SKY_001` (shader `sky_diff_1`, position and texcoord0 only, no normals): an 8 km dome round the origin, up to 2.7 km high, whose 8192 x 1024 panorama paints the distant scenery on its horizon (Mount Tsukuba, the treeline, fields) under the sky and clouds. FM4 has no other far geometry. It's built as an unlit shape: black base colour, the panorama as an emissive map at `FM4_SKY_NITS` (default 4000, not yet tuned in-game), no shadows, no collision, normals facing the centre.

Build through FH1's queue, from `fh1/pipeline`: `python buildq.py submit --chunk fm4_tsukuba -m "..." --by <name>`. An `fm4_<track>` lane runs `fm4build.py <track>` in place (no snapshot), waiting as the others do for BeamNG to close, since a build rewrites the level folder.

Tsukuba, 2026-11-22: 1,470 placed shapes plus 19 collision cells (18,453 `.col` triangles without the invisible walls), 444 materials (180 layered ground), 251 textures, 437k triangles, built in 31 s. Seen in-game: track, ground and collision good; the sky dome (added after) not yet seen.

## Not decoded yet

- **Ground details.** Normal maps on ground and objects (DXN 0x71, as FH1's `tex.normal`), the decal shoulder's third layer, and terrain's extra multiply (an interpolator whose source isn't certain).
- **Lightmaps and tints.** Slot 7 lightmaps are atlas tiles (texture table parent, scale, offset); `Paint_Mask` and `Palette` tint `diff_clr_*`.
- **Cloth flags** (44 meshes on Tsukuba) have no layout yet, as in FH1.
- **The `.col` vertex word** (`0x7FBF2801` and the like) and the 10 m sub-grid.
- **The FPVS draw record** beyond mesh, position and rotation: the u16 at 4 varies with LOD (0x0A14 LOD0, 0x7A14 LOD1) and isn't decoded, so neither are fade distances.
