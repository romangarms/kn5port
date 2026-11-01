# How kn5port works

Notes for contributors, including the BeamNG behaviours that cost the most time to find.

## Pipeline

1. **`level.py` (meshes):** reads every `.kn5` listed in the track's `models*.ini` (or all of them),
   converts AC's Y-up coordinates (`x east, y up, z south`) to BeamNG's Z-up (`x, -z, y`), and recentres everything on the
   road. Physics meshes (`1ROAD_…`, `1GRASS_…`; the key comes from `surfaces.ini`) become collision shapes
   with a BeamNG ground model; everything else is visual-only. Meshes are merged per material inside 500 m
   cells so BeamNG can cull them. Writes materials, spawn points (one per layout) and `info.json`.
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

* KN5 meshes carry `visible` and `renderable` flags; AC draws neither kind. Game-rip tracks (Forza ports especially)
  ship a hidden physics copy of the road exactly on the visual one. Drawn in BeamNG they z-fight (flicker), so hidden
  physics meshes get an invisible, alpha-discarded material that still collides; hidden visual meshes are dropped.
* AC water is a tiling diffuse + normal map made glossy by a strong specular. At BeamNG's PBR roughness each tile of
  the normal map shows as a matte square, so water materials get no normal map and a very low roughness.

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
