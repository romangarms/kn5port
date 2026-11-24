# Forza Motorsport 4 tracks

Porting FM4's circuits with the FH1 pipeline's readers. `fm4build.py` builds a first, plain level (see Build); the rest is how FM4's files read and what differs from FH1. Confidence words are as in `formats.md`. Readers are in `fh1/fm4/`, and extracted data goes in `fh1/fm4/<track>/` (gitignored).

The dump is `D:\Roms\Xbox 360\Xenia\FM4XeniaFix` (read-only), with 24 track folders and 3 GB of tracks in `Media/Tracks/`. Checked on Tsukuba and Laguna Seca, 2026-11-22.

```
python fm4build.py Tsukuba                                          extract if needed, then build mods/unpacked/fm4_tsukuba (every layout)
python fm4ui.py Tsukuba                                             layout names, venue text, photo and layout maps to fm4/ui/
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

- **Textures:** u32 count, then 28-byte records (the count is found by looking back from the shader table for a word equal to the record count after it; other data can match, as a false 7,680 does in `maplevalley_00.pvs`, so the largest count whose records are all plausible wins: kind under 256, scales and offsets 0 or between 1e-6 and 1e6 in size. Over all 83 FPVS files this changes only that one, to 1,387): id, parent, scale u, scale v, offset u, offset v, kind. The id is the `_0x<id>.bix` file. Ids with no file (19% on Tsukuba) are sub-rectangles of `parent`, for example id 1476 is a 1/16 tile of 613: lightmaps are packed into atlases.
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

## Other colour samplers (working)

28 shaders on 17 tracks name their colour texture something other than `Diffuse_TextureSampler`, `DiffuseSampler` or `Blend_A`/`B`, and drew grey until 2026-11-24 (100 of the Alps' 1,719 materials: its rock, shoulders and road edges). Read from the ucode, interpolators traced to the vertex shader's fetches (each texcoord through the UV box). Built through `ground_plan` as one layer; the parts marked "not yet" are left out:
- `diff_spec_deposit_4`, `diff_spec_deposit_uv2_4`, `shldr_diff_spec_deposit_uv2_5`: colour = lerp(Base(tc0 × `c360_0.yz`), Deposit(tc0 × `c360_1.xy`), w) × vertex colour × `c360_0.x`, then the lightmap at tc2. w = 1 / (1 + 2^(-`c360_0.w` × h)), h from the vertex alpha and the height map (tc0 × `c360_2.xy`; one `adds_prev` operand isn't certain). Not yet: the Deposit layer. `c360_0.x` is 1 on all 758 materials of Alps, Crown Jewel, Maple Valley, Amalfi and Top Gear.
- `shldr_4clr_blnd_mask_2`: D = sat(Diff_Spec(world x, z × `c360_1.x`) + paint × `c360_2`), the paint mask at tc1 × `c360_3.xy`, channel x or y by `c360_1.y`; colour = lerp(D × `c360_0`, D + `c360_4`, Blend_Value.rgb(tc2 × `c360_5.xy`)). `c360_0` and `c360_4` are 0 on every Alps material, so D × Blend_Value. World x, z is the world matrix times position (o5). `c360_1.x` is 0 on some materials, a single texel: a flat colour in FM4 too. Not yet: the paint and the Blend_Value multiply.
- `rdedg_blnd_diff_spec_bump_4`/`_5`: the shoulder texture at tc0 × `c360_0.xy`, the road texture at world x, z × `c360_0.z`, blended through `BumpBlend` fetches and the skids layer. Not yet: the road layer and the blend; the shoulder alone.
- `sign_spec_refl_norm_4`: `DiffOpac` at tc0 × `c360_1.xy`.

Still grey: water (`lake_*`, `river_*`, `ocean_*`: normal maps and reflection, no colour texture), `rdedg_blnd_diff_opac_3`, `rdedg_blnd_diff_spec_ovly_blur_4`, `road_blnd_diff_*` with `RoadSampler`, `road_diff_spec_ovly*` and `shldr_diff_spec_ovly_vendor_2` (`Diff_Spec02Sampler`), `road_2blnd_uv3_3`, `sign_spec_opac_refl_norm_4`, `diff_spec_refl_nolm_vclr_2` (`TextureMap_4425Sampler`), `add_diff_opac_rgba`. After this, grey materials per track: Alps 5 (the lake, and one texture `bin.zip` lacks), Maple Valley 4, Top Gear 10, Crown Jewel 21, Amalfi 52, Tsukuba 25 (as before).

## Collision, `<track>_track_NN.col` (proven)

Read by `fm4col.py`. A 7 × 4 grid of 100 m cells on Tsukuba, with 19 occupied. The header gives the counts, bounds and cell size, then from 0x58 one 24-byte entry per occupied cell: index, cell, offset, size, 0, end. A cell block is a 0x40 header (vertex count, triangle count, vertex offset 0x40, triangle offset, a 12 × 12 sub-grid of 10 m with its origin and offset), then 32-byte vertices (float3 position, a packed word, padding) and 32-byte triangles (u16 surface id, three vertex byte offsets within the cell, float3 face normal). The 10 m sub-grid lists after the triangles and isn't read. Tsukuba: 28,907 vertices, 22,073 triangles. The stored normals agree with the winding on every triangle.

Positions are in the draw frame (mesh z flipped): matched that way, triangle centres sit a median 0.15 m from visual triangle centres, against 4.5 m unflipped. In BeamNG, (x, z, y). That mapping is a reflection, so `fm4build` reverses triangles that would face away from their stored normal.

The surface id indexes the children of `SurfaceTypes` in `Media/physics/surfaceTypes.xml`, 61 of them, each with friction and off-road settings. On Tsukuba: 0 Asphalt (road), 20 AsphaultNoPenalty (pit lane), 21 Asphault2NoPenalty, 6 RumbleStrip (kerbs), 1 Dirt and 2 Grass (runoff), 3 GuardRail, 4 ConcreteBarrier (pit wall), 11 RubberTire and 19 RubberTireSpringy (tyre walls), 33 FoamBarrier, and 9 Invisible: 3,620 walls about 117 m tall along the track limits, as FH1 has. Each id was checked against the visual family nearest its triangles.

`fm4build` writes each cell as a collision-only shape (`col<cell>.dae`), one material per surface carrying a BeamNG ground type (`COLGROUND`: asphalt and concrete ASPHALT, RumbleStrip RUMBLE_STRIP, Grass GRASS, Dirt DIRT, Gravel GRAVEL, Sand SAND, guardrails METAL, tyres and foam RUBBER). Invisible walls are left out unless `FM4_WALLS=1`. Where the `.col` exists, `road_`, `shldr_` and `rdedg_` visual meshes don't collide. Everything else (buildings, terrain, fences) keeps its own mesh collision. The `.col` covers only the circuit and runoff: 9 ha of flat surface on Tsukuba.

## Build

`fm4build.py` writes one shape per mesh file (`art/shapes/fm4/m<no>.dae`, through FH1's `daewrite.write_lod`), one TSStatic per draw, keeping each object's most detailed LOD. Object materials are the diffuse texture only (`Diffuse_TextureSampler`); ground shaders are layered, see Ground blending. They are alpha-tested on `*opac*` shaders whose texture has cut-out alpha, and double-sided on `*2sd*`. Textures are written as DXT1 `.color.dds`, keeping FM4's own DXT1 blocks, plus an `_o.data.dds` opacity map; format 0x7B (BC4) decodes through `tex.bc4`. Collision is FM4's `.col` (see Collision) plus each shape's own geometry, except vegetation, flags, road-line decals, cut-out `opac` shaders (barriers aside), sky, flares, and the ground families the `.col` covers. Each layout gets a spawn, see Layouts.

**Layouts.** One level holds every ribbon. A ribbon's FPVS places only what is visible from that layout: Suzuka East leaves out 1,560 of the full circuit's draws, Silverstone National 726. So the level places the union of every ribbon's draws, and of their `.col` triangles, keeping shared triangles once. Ribbons with equal texture, shader and mesh tables share shapes. Every track has one such group as shipped (shapes in another group would be named `m<no>_<group>.dae`). Alps' and Maple Valley's apparent second groups were a misread texture table and stale ribbons, both below.

**Stale ribbons.** A ribbon whose FPVS places mesh numbers `bin.zip` doesn't have was made against other data, and its FPVS and `.col` are left out: that layout takes its geometry from the other ribbons, as a layout with no FPVS does, and keeps its own spawn and AI line. Only Alps' reverse ribbons 3 and 4 (Stadtplatz Reverse, Club Circuit Reverse) are stale: 1,502 mesh records to `bin.zip`'s 1,301, 2,292 textures to 1,605, and 88 meshes placed (126 of 2,000 draws) that have no file, 86 of them above 1300. None of Alps' reverse ribbons has a `<Track>_ribbonNN.sh`, and ribbon 5 has no FPVS at all.

Some layout-specific objects close one layout off from another. Tsukuba's full circuit has two jersey barriers across the short circuit's link, and the short circuit has one across the full lap. The rule: a draw that some ribbons don't place is left out if it stands on one of those ribbons' roads. "On the road" means a vertex or triangle centre of its colliding, non-ground sections lies between chi -1 and 1 of that layout's AI line, from 1 m below the line to 2 m above it. The road runs over the layout's own `StartLine` to `FinishLine` span, since stages run along the full line. `.col` walls steeper than 60 degrees follow the same rule. This left out exactly those 3 barriers and 32 wall triangles on Tsukuba.

Spawns: one per gamedb `Tracks` row with an AI line. Each is grid slot 1 of the row's `DefaultStartGridPositionsId`, back from the waypoint at its `StartLine`. `StartLine` is a fraction of the layout's own line, by distance. Reverse layouts use `<track>_NN_R.geo`, which runs backwards. Some rows have no `Ribbon_NN` FPVS of their own: TopGear 8 to 10, and most of Fujimi Kaido's 14. They still get a spawn, and their geometry comes from the ribbons that have one.

## Menu art and text (`fm4ui.py`, proven)

- **Names.** gamedb `Tracks.DisplayName` is the layout name ("Full Circuit", "Stage B Reverse"). `Environments`, by `Tracks.EnvironmentId`, holds the venue: `DisplayName` ("Tsukuba Circuit"), `Location`, `Description` and `ThumbImageName`. CrownJewel's ribbons span three venues.
- **String tables.** A `_&<id>` value is a reference into `Media/StringTables/<lang>.zip`, in `Tracks.str` or `Environments.str` (LZX, bin.zip's layout). A `.str` file is LSB2: at 0x28 a big-endian count n, then n (u16 key, u32 offset) pairs sorted by key, a (0xFFFF, 0, u16) sentinel, then zero-terminated UTF-16BE strings. Offsets count UTF-16 units from the first string. The key is the id's low 16 bits; all 254 track references hit.
- **Images.** These are in `Media/UI/Textures.zip`:
  - `UI4/Track_Images/<ThumbImageName>1.xds`: the venue photo, 881 x 315.
  - `UI4/Logos/Track_Ribbons_LG/RIB_<name>.xds`: the layout map with its start marker, 880 x 313. Its name follows `Tracks.MiniRibbonPath`.
  
  An `.xds` is a 52-byte D3DBaseTexture header whose words from 0x1C are the Xenos fetch constant: format in word 1 (0x32, 8888), and size in word 2. The tiled top mip follows, in ARGB.
- **The level.** `info.json` gets the venue's name with "(FM4)" as the title, and its location and description. The photo, cropped to 4:3, is the level preview. Each layout's map, letterboxed on white, is its spawn preview.

The sky and backdrop are baked into a SkyBox cubemap, see Sky.

**Vertex-coloured shaders and the tree line.** `*_nolm_vclr_*` shaders light by vertex colour where others use a lightmap:
`tex * lerp(V2LightmapColor1, V2LightmapColor2, vertex colour)`, with alpha from the texture. Vertex colours are ARGB bytes.
- The circuit's surrounding forest is drawn this way: `VEG_<TRACK>_TreeWall_*` cards, up to 430 m long. They have no normals, because nothing in the shader uses them. They were dropped until 2026-11-22, which left grey below the horizon; they now get up-facing normals and `vertColor` (the grey mean of RGB), with both colours at their shader defaults, 0 and 1.
- `clr_0` draws only the material's pixel constant c0 plus fog. It's used for power lines and fence wires, built as a flat `baseColorFactor` with no collision.
 The sun is TrackSettings' `ShadowLightDirection`, and there's no TimeOfDay.

Build through FH1's queue, from `fh1/pipeline`: `python buildq.py submit --chunk fm4_tsukuba -m "..." --by <name>`. An `fm4_<track>` lane runs `fm4build.py <track>` in place (no snapshot), waiting as the others do for BeamNG to close, since a build rewrites the level folder.

Tsukuba, 2026-11-22: 1,470 placed shapes plus 19 collision cells (18,453 `.col` triangles without the invisible walls), 444 materials (180 layered ground), 251 textures, 437k triangles, built in 31 s. Seen in-game: track, ground and collision good; the sky dome (added after) not yet seen.

## In the app (`kn5port/ui/fm4.py`)

The Forza Motorsport 4 tab (beta) takes the game as the FH1 tab does (`xbox.detect(path, title=xbox.FM4)`): a disc image, a GOD package or a folder holding `Media/`. FM4 is recognised by `Media/db/gamedb.slt` plus `Media/Tracks/TopGear/bin.zip`, since FM3's disc has a gamedb and most of the same tracks but not Top Gear. FM4's first disc holds all 32 track folders. From an image, files are copied into `<work>/game` as needed: the database, string tables, `UI/Textures.zip` and `physics/` (0.43 GB) with the track list, then a track's `Media/Tracks/<Track>/` and `Media/AI/Tracks/<track>_*` before its first build. `<work>/game/.kn5port-source.json` records the image, the tracks on it and those copied. A Tsukuba level built from the ISO is byte-identical to one built from Xenia's folder. Then, with the converter's Python:
- `fm4ui.py --list <work>/catalogue [Track ...]` once per game copy: `tracks.json` (gamedb's active, non-homespace tracks with a `bin.zip`, or named on the command line as on the disc, with their venue, location and layout names) and a 240 x 180 photo per track, for the track cards.
- `fm4build.py <Track> <mods>/unpacked/fm4_<track>`, with `FM4_DUMP` the game folder and `FM4_WORK` the work folder (`<work>/<track>/x` holds the extracted `bin.zip`).

In a checkout the work folder defaults to `fh1/fm4` and the scripts run in place; otherwise (the exe bundles them as `fh1_fm4`) they're copied with the pipeline into `<work>/app_scripts/{fm4,pipeline}`, since they import `../pipeline`. Only tracks in `fm4.WORKING`, those `fm4build.py` builds to the end, can be picked; `CHECKED` marks the ones looked at in BeamNG. Update both as tracks are fixed.

## Executable (`default.xex`)

`fm4xex.py` writes `fm4/default.pe`, the plain image (gitignored). The xex is XEX2 with encryption 1 and basic compression: AES-128-CBC with a zero IV over the whole image, its key the security info's (+0x150) decrypted with the retail key, then (data size, zero size) blocks. The image is laid out at its virtual addresses, base 0x82000000. `fm4code.py refs <string>` finds the code that loads a string's address (lis/addi), and `fm4code.py dis <addr> [n]` disassembles PowerPC with capstone, annotating strings and float literals. VMX128 instructions don't decode.

FH1's `xshader.py` reads the image: 247 Xenos shaders, almost all unnamed, found by their constant tables. The post shaders are FH1's, line for line: `filmicParams*`, `tonemapAdaptiveLuminanceKeys`/`Exposure`, `colorGradingTex*`, `colorTransform[3]`.

**TrackSettings parsing** (around 0x82914000) stores each tag at a fixed offset of the settings object, for example `SkyboxHeightOffset` +0x70, `skyGain` +0x2C4, `skyScale` +0x2C8. A renderer object then sets the pixel shader constants through the D3D device's constant mirror: vertex constants at device +0x780, pixel constants at +0x1780, 16 bytes a register, with a dirty bit per four registers in qwords at +0 (vertex) and +8 (pixel). The dirty masks confirm the base: c214 is mask 0x400, c222 0x100, c242 and c240 8, c249 2. `c242` is `FogColor`.

## Textures: gamma

A `.bix` header's word at 0x1A8 is the Xbox 360 `D3DFORMAT`: format in bits 0 to 5, endian 6 to 7, tiled 8, then two sign bits each for X, Y, Z and W. `0x1A207F52` is tiled DXT1 with `GPUSIGN_GAMMA` on RGB, so the GPU degammas on fetch. That's every colour texture. `0x1A200152`-style words (signs 0) are data: every normal map, and masks. Xenos gamma is piecewise linear, with segments at 64, 96 and 192 of 255 (`fm4sky.xenos_degamma`).

## Sky (`fm4sky.py`)

FM4 has no far geometry: the backdrop (Mount Tsukuba, treeline, fields) is painted into the sky dome's 8192 x 1024 panorama.
- **Dome.** `<TRACK>_SKY_001`, shader `sky_diff_1`, position and boxed texcoord0 only. Radius 4.1 km, from the ground ring at y 0 to 2.7 km up, drawn lowered by `SkyboxHeightOffset` (-100.1). That's taken as camera-relative, since the engine sets the draw matrix. It covers down to 1.4 degrees below the horizon; below that FM4 shows its own ground, or else its clear colour.
- **Pixel shader.** `colour = tex.rgb * (tex.a * TimeGain.y + SkyScale.x)`, written as sqrt, as every FM4 pass is.
  - `SkyScale` (c222) is TrackSettings' `skyScale`: 0.950001 read live (see Live memory).
  - `TimeGain` (c249) is (seconds since start, 0, 0, 0) live, so the alpha term is 0.
- **Check.** The painted sun's peak is at azimuth 306 and elevation 20.5. `ShadowLightDirection` in the draw frame gives azimuth 307.5, elevation 40.7, so FM4 lights from higher than its painted sun. That fixes both the dome's mapping and the light's frame.
- **Bake.** Rays from the camera hit the dome exactly (ray against triangle plane, perspective-correct). The panorama is degammaed and scaled, then passed through FM4's post:
  - Exposure: `ToneMapAdaptiveExposure` keys / 255 against a key, plus `filmicTone.exposure`. The key is the sky's own mean linear luminance from the horizon to 30 degrees, FH1 `sky.py`'s assumption (0.22, so EV 0.31).
  - Tone map: Hable with `filmicTone`, over its value at white.
  - Grade: identity LUT (no track ships `colorgradinglookup.dds`), then FH1's colorTransform order: `DesaturateAmount` with `DesatLuminosity`, times `DesatPostMod`. The order and the use of all three B values are inferred.
  - Output: sqrt, stored as the face's bytes, as FH1's bake.
  - Directions the dome misses take `WorldClear`, squared, because a clear writes the sqrt-encoded framebuffer.
  - `WorldClear` is only in TrackSettings version 60 (12 tracks, all 0.26 grey). FM4's parser reads it only from version 60 on (`cmplwi r18, 0x3c` at 0x82914714, disc xex) and otherwise keeps the settings object's value at +0xB0..+0xB8, whose initialisation isn't found yet. The 11 tracks at versions 58 and 59 (Laguna Seca, Silverstone, Suzuka and others) take 0.26 too (`fm4sky.WORLD_CLEAR`), the user's call; a live read on one of them would settle FM4's own value.
- **Not reproduced.** Bloom (`bloomCutoff`, `bloomScale`, `bloomWeights`), `CameraExposureRange`, exposure adaptation.
- **Level.** SkyBox `fm4_sky`, also the level's `globalEnviromentMap`. ScatterSky stays for the light, its `azimuth` and `elevation` set from `ShadowLightDirection`, with `shadowDistance` = `ShadowEnds InGameShadowEnd` (202 m).

## Live memory (`fm4mem.py`)

`fm4mem.py` reads FM4's memory while it runs in Xenia Canary, read-only (`PROCESS_VM_READ`). Guest memory sits at host 0x100000000.
- **The running build is the title update's.** Its image strings sit 0x844 bytes later than the disc xex's, so code addresses from `fm4code.py` don't carry over; struct offsets do.
- **TrackSettings.** The parsed object is found by its values: `SkyboxHeightOffset` at +0x70, `DesaturateAmount` at +0x7C. On Tsukuba it sits at guest 0x40E5BD80.
- **Shader constants.** Xenia keeps the GPU's float constants in host memory as little-endian floats, in two 4 KB banks (vertex, then pixel), 16 bytes a register. The banks were found by searching for DarkColor followed by LightColor. They hold whatever the pass being drawn last wrote, so values are sampled many times. On Tsukuba the snapshot is in `fm4/tsukuba/live_consts.json`.

| Register | Name | Live value | Source |
|---|---|---|---|
| PS c222 | `SkyScale` | (0.950001, 0.65, 0, 1) | `skyScale`, `cubeSkySaturation` |
| PS c224 | | (24, 24, 0.408001, 0.85) | `RoadGlossiness`, `RoadGlossiness2`, `RoadLightmapDarkPoint`, `RoadLightmapLightPoint` |
| PS c247 | `V2LightmapColor1` | (0.01, 0.02, 0.03, 1) | `DarkColor` |
| PS c248 | `V2LightmapColor2` | (0.850001, 0.85, 0.750001, 1) | `LightColor` |
| PS c249 | `TimeGain` | (time in seconds, 0, 0, 0) | |
| PS c250 | `sunDir` | (-0.6018, 0.6519, 0.4614) | `ShadowLightDirection`, negated and normalised |
| PS c251 | `sunColor` | (0.6, 0.588, 0.564) | not traced |
| PS c214 | `SunGainScale` | (1.1e-06, 250000, 0, 0) | `sunGainScale` scale, plus an unknown x |
| VS c241 | `FogConsts` | (80, 3.45e-05, 0, 0.0002), in draws that fog | see Fog |
| PS c242 | `FogColor` | (0.56, 0.68, 0.8, 1), in draws that fog | Settings0 `Color` |

## Lightmaps

Lit object shaders have no sun term. In `DIFF_1`, `colour = diffuse * lerp(V2LightmapColor1, V2LightmapColor2, lightmap.rgb)`, plus sun specular. The specular is gated by the lightmap's green between c224.z and c224.w, then fogged.
- **Sampling.** The lightmap is sampled at texcoord2 (the vertex shader's second UV), through the UV box, then `Light_Map_uvOS`. That is the lightmap's texture record's scale and offset: a 1/16 tile in a 1024² atlas, for example, or a 1/128 constant tile. The texture is the record's own file, or else its parent atlas.
- **Texture format.** Lightmap textures are gamma-encoded, so they're degammaed on fetch.
- **Build.** An `ambientOcclusionMap` on UV1 (`ambientOcclusionMapUseUV`), as FH1's AO maps: `lum(lerp(DarkColor, LightColor, lightmap)) / lum(LightColor)`. Fully lit is 1, and BeamNG's sun and sky supply the light. The lightmap's tint (warm in sun, blue in shade) is lost to the single channel.
- **Coverage.** 1,234 of Tsukuba's sections. Not yet: lightmaps the draw supplies (slot 0xFFFFFFFF, 106 sections), and ground shaders, whose UV1 carries the blend mask.

## Fog

The vertex shaders compute `fog = sat(1 - 2^(-FogConsts.w * max(d - FogConsts.x, 0)))`, d being the distance to the camera. The pixel shader lerps to `FogColor` by it.
- **Live values on Tsukuba.** `FogConsts` is Settings0's (`Near`, `FarDensity / (Far - Near)`, `NearDensity`, 0.0002). The 0.0002 isn't in TrackSettings; another fog record in memory, (125, 5, 0.015, 0.0002), carries it too. y and z aren't used by `DIFF_1`.
- **Angles.** FM4 picks between enabled settings by camera heading (`Angle`). Tsukuba enables only Settings0.
- **Build.** BeamNG's fog is `exp(-density * (d - offset))`, so it gets `fogDensity` = 0.0002 ln 2 and `fogDensityOffset` = `Near`, with no height falloff and `fogColor` from the setting. ScatterSky may still tint the fog; not yet checked in-game.

## Not decoded yet

- **Ground details.** Normal maps on ground and objects (DXN 0x71, as FH1's `tex.normal`), the decal shoulder's third layer, and terrain's extra multiply (an interpolator whose source isn't certain).
- **Lightmaps and tints.** Slot 7 lightmaps are atlas tiles (texture table parent, scale, offset); `Paint_Mask` and `Palette` tint `diff_clr_*`.
- **Cloth flags** (44 meshes on Tsukuba) have no layout yet, as in FH1.
- **The `.col` vertex word** (`0x7FBF2801` and the like) and the 10 m sub-grid.
- **The FPVS draw record** beyond mesh, position and rotation: the u16 at 4 varies with LOD (0x0A14 LOD0, 0x7A14 LOD1) and isn't decoded, so neither are fade distances.
