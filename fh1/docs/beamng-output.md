# BeamNG output: rendering, terrain, LODs

How FH1 data becomes a level. Constants are at the top of `build3.py`.

## Terrain and ground

FH1's own terrain meshes are the visible ground and the collision. The level has no `TerrainBlock`: the user tested hiding it and asked for it to go. The heightfield notes below are history.

Far ground near a road is kept and capped just under road height, not cut away. `TERR_CUBE` stays out: enabling it added almost no coverage. Sections listing a red and green blend mask as a colour layer use a real layer instead (`rgmask`), or they draw yellow.

All LODs of a world mesh go in the cell of the group's centre (`group_cell`), so they switch together. Splitting them by triangle left ground missing at distance. Terrain LOD1 covers LOD2, to save memory.

Placed solid objects are instanced: one `m<mesh>.dae` shared by `TSStatic`s. Mirrored ones stay baked into cells.

- Heights come from terrain meshes rastered into tiers: LOD0, 1, 2, then far meshes. Roads are rastered separately.
- Where meshes, roads or rock are shown, the heightfield drops to `minimum_filter(H, 31) - 8 m` so it can't poke through. That window is in grid cells, so it's 62 m on Red Rock and 124 m on Colorado. Coverage holes are closed and the mask grows by 3 cells: the old erosion let the heightfield rise around every small hole.
- Origin-local meshes are left out of the world path (`origin_local`), or they appear raw at (0, 0) by the festival. Any zone record away from the origin marks a mesh, and its LOD group, as local.
- Near roads the height is capped at road height minus 0.2 m, rising with distance out to 40 m.
- Far terrain only shows where nothing better covers it, in density order: `PATCH` (about 21,000 tris per km2, as fine as near ground and the only ground on much of the map), `MIDDIST` and `_MID_` (about 3,000), then the `UberLOD` shell (about 240). `TERR_UberLOD_Patch` is the shell despite its name. `UberLOD` meshes are exempt from the 6 km size limit: they're the ground under the eastern trees.
- `clip_far` splits triangles and keeps the parts outside coverage. Far ground runs `LIP` metres under nearer ground, sloping down, so a height mismatch can't open a crack. Within 40 m of a road, far ground is capped near road height so it can't form panels across it.
- A zone record's `lod` field holds FH1's own switch distances, such as `[400, 650, 131008]`, where 131008 means never. The build doesn't read them yet. LOD1 and LOD2 files can have no record of their own, so a missing record only proves a mesh unused at LOD0.
- Terrain meshes cover 66% of the Colorado square. The rest is mostly edges and off-map land, showing the BeamNG terrain with map art as its base colour.
- `theTerrain.ter` is format 9: u32 size, heights, layer map, one material.

## Terrain blending (user confirmed "perfect")

`h_blnd*` shaders blend slots 0, 1 and 5 using the mask in slot 2, read with UV set 2 as `(s + 1) / 2`.

BeamNG equivalent: `activeLayers` 3, with layers 1 and 2 using `opacityMap` and `opacityMapUseUV: 1`. See `blend_material`.

## Surfaces

`h_road*` shaders are roads. `role_ground` sets asphalt or dirt from section names. The user confirmed dirt roads work. Rock uses `ROCK`, barriers and rails `METAL`.

Textures are written as pre-cooked DXT1 `.dds` files by `dds.py`, capped at `TEXMAX = 1024`. A DXT1 top mip keeps FH1's own blocks unchanged. Lower mips, masks and DXT3/5 sources are encoded in `dds.py`. Colorado's PNGs were cooked up to 1024 px by BeamNG for an unknown reason (3.56 GB against 213 MB now), which caused the out-of-VRAM load. Red Rock's PNGs weren't. Keep the `.color.` and `.data.` suffixes: BeamNG picks the colour space from them.

The user has said terrain looks a little low-res. FH1's sources are mostly 128 to 512 px and only 22 reach `TEXMAX`, so raising it won't help. Normal maps aren't used, which is probably part of that.

## Static LODs

- 250 m cells (`CELL`), one shape per cell and category.
- 3 details from FH1's LOD00, 01 and 02. If a group lacks a level, its coarsest mesh stands in.
- Switch distances: `LOD_DIST = (300, 800)`, from the shape's edge. Props vanish at `PROP_FAR = 1600`. `col` shapes never vanish.
- Detail sizes are computed from each shape's radius and `PX = 1250` (pixels of radius per radius/distance at 1440p), so all cells switch at the same distance. The engine measures from the centre, so `detail_sizes` adds the radius: tile groups can be 1 km wide. A name like `o0_a71` means detail size 71.
- Shape tree: `base00 > start01 > o<i>_a<size>`, as in stock shapes.
- Each LOD and `Colmesh` node is a full copy in the `.cdae`: size fits 56.1 bytes per instantiated vertex and 11.8 per triangle, R2 0.9999. `write_lod` welds vertices that would write the same text, which took Colorado's predicted cooked shapes from 2.80 GB to 1.91 GB.

## Collision

`col` and `pro` shapes collide with their coarsest LOD. Barriers and objects under 15 m have no LOD2 copy and vanish at 800 m (`FARCUT`). Each has a `collision-1` node and `collisionType: Collision Mesh` on the `TSStatic`. `vis` shapes have none. The user drove this on Red Rock with no problems.

FH1's `.fiz` tiles were tried and reverted. Cars behaved as if under 10x gravity and crumpled, and RAM and fps got worse. Their triangles have mixed winding (47% up, 53% down), and BeamNG collision is one-sided, which is the likely cause. Any retry must re-wind them to face up, and must not duplicate vertex arrays. `fiz.py` still reads them.

## Plants

Plants use BeamNG's forest system, not `TSStatic`. Items have `o0_a120`, `o*_a40` and `bb_autobillboard25`: LOD00, LOD01, then an imposter. `FH1_IMPOSTERS=0` keeps LOD01 at distance instead. Stride-16 tree cards are skipped by the world mesh path.

## Scene

`visibleDistance` 6000, `shadowDistance` 1600, `TimeOfDay` fixed, `ScatterSky`, one `SpawnSphere`. The spawn is the first race route checkpoint near Red Rock, moved onto the road height. `info.json` holds the title and spawn point.

## Performance

Tested on a 2070 Super at 1440p, at Red Rock:

| Build | fps |
|---|---|
| First real textures | 110 |
| Full objects, no LODs | 95 |
| LODs | 120 |

Estimates from the outpost:

| | Static tris | Plant tris |
|---|---|---|
| Red Rock, no LODs | 1.47M | 3.75M |
| Red Rock, LODs | 602k | 138k |
| Whole map, LODs | 2.48M, of which 2.1M is LOD2 | 282k |

The whole map has no in-game number yet. If it's slow, first shorten far distance: `visibleDistance`, or drop `col` LOD2 past a limit, because that's where the triangles are. The estimate code ends in the `static tris` and `plant tris` lines.

## Rotation lists

A `TSStatic`'s `rotationMatrix` lists its axes as rows: stock guardrail chains match rows 408 to 43 across 12 levels. `build3.py` works with axes as columns, so it transposes on writing. Forest items are written untransposed and haven't been measured.

## Always-on ground

Any world mesh whose zone record has a first LOD distance of 1e5 or more (`SHELL`) is coarse ground, whatever its name. This covers `Mountains_Area04A_Terrain_05`, which sits over the Finley Dam area and shares a `lodkey` with the fine tile `Terrain_LOD00_05`.

Far ground is hidden over water, and only sinks under near ground, not at road edges.

## Cooked shapes

Stock levels ship a `.cdae` beside every `.dae`. `cookship.py` copies BeamNG's cooked shapes from `temp/` into the level, after one load. A rebuild wipes them.

The Uber shell has festival tents baked in as lumps. In a small hole in finer ground (under 0.15 km²), shell vertices are held to 1 m above that ground (`lump`).
