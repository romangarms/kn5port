# How kn5port works

Notes for contributors, including the BeamNG behaviours that cost the most time to find. For the full
symptom → cause → fix log, open issues and a debugging checklist, see [learnings.md](learnings.md).

## Pipeline

1. **`level.py` (meshes):** reads every `.kn5` listed in the track's `models*.ini` (or all of them),
   converts AC's Y-up coordinates (`x east, y up, z south`) to BeamNG's Z-up (`x, -z, y`), and recentres everything on the
   road. Physics meshes (`1ROAD_…`, `1GRASS_…`; the key comes from `surfaces.ini`) become collision shapes
   with a BeamNG ground model; everything else is visual-only. Meshes are merged per material inside 500 m
   cells so BeamNG can cull them. Writes materials, spawn points (one per layout) and `info.json`.
   **LODs (`lods.py`):** each AC mesh draws between its `lodIn` and `lodOut` metres, and most track geometry
   has a `lodOut` (often 80 to 500 m). An object's LOD meshes are linked into a chain by overlapping bands on
   meshes of about the same size and place (Kunos overlaps them: HI to 75 m, LOW from 70 m), and switch halfway
   through the overlap. Objects with the same switch distances share a shape in a cell that shrinks to under half
   the first switch, as Torque measures from the shape's centre. Each band is a detail level sized
   `PX x radius / distance` (`PX` 1250, FH1's, about 1440p); below the last size the shape draws nothing, which is
   the cull a `nulldetail` node gives hand-made shapes. Lower LODs aren't layer-lifted. Chains with collision in
   them, and lower LODs whose LOD0 wasn't found, stay as before: LOD0 at every distance. `--no-lods` turns it
   off, `--lod-scale` stretches every distance. Laguna Seca: 244 of 255 meshes of the main kn5 vanish somewhere,
   and the level grows 7% from the lower LODs.
2. **`terrain.py` (terrain):** rasterises AC's ground in priority order (horizon backdrop → distant terrain →
   near ground). Each layer is offset from the one below where they overlap (AC sinks hidden geometry), so
   lower layers are bent to meet upper ones over a fade distance. The terrain is then *fitted* under the
   remaining meshes: it starts just under the topmost surface and an iterative solver lowers only the grid
   corners needed so that, for both possible cell triangulations, it never rises above a driving surface.
   Where it still drops away from a road edge, sloped skirts are generated. Material layers come from AC's
   multilayer masks (or ground colour), and GroundCover from the donor level is restricted to those layers
   and kept off a band around every driving surface.
3. **`forest.py` (trees):** vegetation meshes are split into elements (cards, or connected parts of 3D
   meshes). Tall elements standing on the ground are whole trees (crossed cards share a centre); elevated
   leaf cards are crowns, claimed by a trunk mesh underneath or grid-filled; bushes and grass are grid-filled.
   Each becomes a donor forest item scaled to the AC height, snapped to the ground and kept clear of roads.
4. **`validate.py`:** checks every reference, texture sizes against the terrain texture set, GroundCover
   layers, and that each AC AI line has road under it with no terrain above.

## KN5 format (v5/v6, unprotected)

`"sc6969"`, `int32 version` (+ `int32` if > 5), textures (`int32 active, string name, int32 size, bytes`),
materials (`name, shader, u8 blend, u8 alphaTested, int32 depthMode`, properties `name, float, 9 floats`,
samplers `name, int32 slot, texture`), then a node tree: class 1 = transform (4x4 row-major, row vectors),
2 = mesh (`u8 castShadows, u8 visible, u8 transparent`, vertices 44 bytes: pos, normal, uv, tangent;
`u16` indices; `materialId, layer, lodIn, lodOut, bounding sphere, u8 renderable`), 3 = skinned mesh.
Strings are `int32 length + bytes`. AC triangles are counter-clockwise in the converted frame; UV v is
flipped for Collada.

## BeamNG gotchas

- **KN5 meshes carry `visible` and `renderable` flags; AC draws neither kind.** Game-rip tracks (Forza ports
  especially) ship a hidden physics copy of the road exactly on the visual one. Drawn in BeamNG it z-fights
  (flickers), so hidden physics meshes get an invisible, alpha-discarded material that still collides, and
  hidden visual meshes are dropped.
- **Stacked road layers z-fight.** Tracks (game rips most of all) lay patch, line and grime/decal meshes
  exactly on the road. AC draws opaque meshes in file order, then transparent ones, with a less-or-equal depth
  test, so the later layer always wins. BeamNG doesn't, so `overlay.py` replays that order: a mesh whose
  triangles lie (centroid inside an earlier triangle, within a few cm) on an earlier-drawn surface is one layer above it,
  and those triangles are raised 2 cm per layer (`--layer-lift`). Meshes that only meet at a seam are untouched.
- **AC blend mode 2 is alpha-to-coverage**, i.e. a cut-out. It's mapped to `alphaTest`, otherwise the decal
  turns into an opaque sheet.
- **AC water is a tiling diffuse plus normal map made glossy by a strong specular.** At BeamNG's PBR roughness
  each tile of the normal map shows as a matte square, so water materials get no normal map and a very low
  roughness.
- **Texture colour space comes from the file name:** `*.color.png` is sRGB, `*.data.png` linear,
  `*.normal.png` a normal map. Without a suffix colours wash out.
- **v1.5 materials take alpha from `opacityMap`**, not from the base colour's alpha channel.
- **Block-compressed DDS must have power-of-two sides**, or it is refused (convert to PNG).
- **Torque reads a trailing number in a Collada node name as a detail size**: end node names with letters.
- **Materials are cached for the whole game session.** After changing a level, restart BeamNG fully.
- **Levels only load their own materials.** Shapes borrowed from another level need their material
  definitions copied in, or they render orange up close (far away, imposters hide it).
- **`.ter` (v9):** `u8 9, u32 size, u16 heights[y][x]` (y = north), `u8 layers[y][x]`,
  `u32 count, (u8 len, name)…`; height = value / 65535 × maxHeight + position.z.
- **The terrain base colour texture is stored north-up** (image row 0 = north), the reverse of the heightmap.
- **Terrain base textures must exactly match the texture set's `baseTexSize`**, or BeamNG rejects them and
  close-up terrain turns nearly black.
- **GroundCover only grows on a `TerrainBlock`**, on the layers named in each type.
