# BeamNG output: rendering, terrain, LODs

How FH1 data becomes a level. Constants are at the top of `build3.py`.

## Terrain and ground

FH1's own terrain meshes are the visible ground and the collision. The level has no `TerrainBlock`: the user tested hiding it and asked for it to go. The heightfield notes below are history.

Far ground near a road is kept and capped just under road height, not cut away. `TERR_CUBE` stays out: enabling it added almost no coverage. Sections listing a red and green blend mask as a colour layer use a real layer instead (`rgmask`), or they draw yellow.

`TERR` decides what is ground. Mountains Area 3, around Beaumont, is named `TERR_Moun_A3_LOD00_04` and so on, and once missed it: it drew as props with one texture and wasn't counted as coverage, so MIDDIST and UberLOD ground was laid over it. The festival's fine ground, `Festival_Area5_09_LOD00` and so on, was missed the same way. `TERR_Zone*` (sparse, plain diffuse) and `TERR_Fuji_Waterfall` (additive) stay out on purpose.

All LODs of a world mesh go in the cell of the group's centre (`group_cell`), so they switch together. Splitting them by triangle left ground missing at distance. Terrain LOD1 covers LOD2, to save memory.

Placed solid objects are instanced: one `m<mesh>.dae` shared by `TSStatic`s. Mirrored ones stay baked into cells.

- Heights come from terrain meshes rastered into tiers: LOD0, 1, 2, then far meshes. Roads are rastered separately.
- Where meshes, roads or rock are shown, the heightfield drops to `minimum_filter(H, 31) - 8 m` so it can't poke through. That window is in grid cells, so it's 62 m on Red Rock and 124 m on Colorado. Coverage holes are closed and the mask grows by 3 cells: the old erosion let the heightfield rise around every small hole.
- Origin-local meshes are left out of the world path (`origin_local`), or they appear raw at (0, 0) by the festival. Any zone record away from the origin marks a mesh, and its LOD group, as local.
- Near roads the height is capped at road height minus 0.2 m, rising with distance out to 40 m.
- Far terrain only shows where nothing better covers it. `far_tiers` orders it per triangle by how close FH1 ever draws it: the distance to the nearest zone hex listing any LOD of its mesh (see `Colorado_00.hex` in `formats.md`), in bands at 150, 400, 800 and 1500 m, then coarse (under 1,500 tris per km2) after fine. Each tier only fills what earlier tiers left. The `UberLOD` shell is never drawn within about 1 km of itself, so finer ground claims that space first. Names misled here: `TERR_UberLOD_Patch` is the shell, and a `NOLOD` tile can be ten times finer than the shell over it. Two equally coarse meshes that FH1 draws from the same hexes, such as `NoLOD_06` and `MIDDIST_02` near Redstone, still overlap. `UberLOD` meshes are exempt from the 6 km size limit: they're the ground under the eastern trees.
- `clip_far` splits triangles and keeps the parts outside coverage. Inside coverage, far vertices are first held to the near ground's height (`top`, the lowest cell in a 3-cell window, since `raster` keeps each cell's highest point), then sunk. Sinking alone left coarse mountain sides up to 17 m above the fine ground's edge near Beaumont. Far ground runs `LIP` metres under nearer ground, sloping down, so a height mismatch can't open a crack. Within 40 m of a road, far ground is capped near road height so it can't form panels across it.
- A zone record's `lod` field holds FH1's own switch distances, such as `[400, 650, 131008]`, where 131008 means never. The build doesn't read them yet. LOD1 and LOD2 files can have no record of their own, so a missing record only proves a mesh unused at LOD0.
- Terrain meshes cover 66% of the Colorado square. The rest is mostly edges and off-map land, showing the BeamNG terrain with map art as its base colour.
- `theTerrain.ter` is format 9: u32 size, heights, layer map, one material.

## Terrain blending (user confirmed "perfect")

`h_blnd*` shaders blend slots 0, 1 and 5 using the mask in slot 2, read with UV set 2 as `(s + 1) / 2`.

BeamNG equivalent: `activeLayers` 3, with layers 1 and 2 using `opacityMap` and `opacityMapUseUV: 1`. See `blend_material`.

## Surfaces

`h_road*` shaders are roads. `role_ground` sets asphalt or dirt from section names. The user confirmed dirt roads work. Rock uses `ROCK`, barriers and rails `METAL`.

Textures are written as pre-cooked DXT1 `.dds` files by `dds.py`, capped at `TEXMAX = 1024`. A DXT1 top mip keeps FH1's own blocks unchanged. Lower mips, masks and DXT3/5 sources are encoded in `dds.py`. Colorado's PNGs were cooked up to 1024 px by BeamNG for an unknown reason (3.56 GB against 213 MB now), which caused the out-of-VRAM load. Red Rock's PNGs weren't. Keep the `.color.` and `.data.` suffixes: BeamNG picks the colour space from them.

The user has said terrain looks a little low-res. FH1's sources are mostly 128 to 512 px and only 22 reach `TEXMAX`, so raising it won't help.

Normal maps are `n<id>.normal.dds`, BC5U like stock maps, keeping FH1's top-mip blocks. `section_normals` sets `NM`, the current section's colour-to-normal pairs, which `material` and `blend_material` read, as `LV` and `FIX` work. Each normal goes on the stage drawing its colour layer, chosen by gradient correlation, falling back to the first layer. Material names gain `_n<id>`.

## Water

Lake shader sections go in `wat` shapes: no collision, never culled, with FH1's ripple normals on the translucent material. `water.py` (run by `build3.py` after `particles.py`) rasters them at 3 m, traces each piece of water's centre line, the longest path through it weighted to keep away from the banks, then branches the first ribbon misses. Each becomes a `River` in `fh1_water`: nodes every 40 m or less, at FH1's surface height, with width from the distance to the bank plus 4 m and depth to the lowest ground under the water, so bridges don't count. The `wat` TSStatics are removed, and saved in `fh1_water/meshwater.json` so a rerun can read them. Ripples are FH1's two lake normals as RGB DXT1 (`w<id>.normal.dds`), reflections come from `fh1_sky_cubemap`, and the depth ramp and foam are BeamNG's core ones, with foam off.

## Particles

BeamNG 0.40 crashes on level load (integer divide by zero, `BeamNG.drive.x64.exe` + 0x9def55) in two particle cases. With `animateTexture`, `preload` reads `animTexTiling` as 0 0, although `"4 4"` passes `onAdd`, and the frame list divides by it. No stock content animates particle textures, so `particles.py` cuts each tile into its own particle. An emitter's `particles` list over 255 characters is dropped at load ("particle string too long"), which silently removed birds and campfire flames, so tiles are capped at 16 (`MAX_TILES`) under short `fh1q<crc>_<i>` names, and the script refuses longer lists. Separately, `preload` divides by `ejectionPeriodMS - periodVarianceMS`, and the validator clamps both to 2047 without keeping the variance below the period.

## Night lights

`nightlights.py` (run by `build3.py` after `water.py`) writes the lightmap overlays (`fh1_night`, glowing through stock `nightEmissive`, hidden by day by `fh1Extras.lua`) and lamps (`fh1_lamps`). Lamps are built like West Coast's street lamps (`lightemitters_pl_double`, `_single`, `_lightcity`): a `SpotLight` at the head pointing straight down (`rotationMatrix` [1,0,0, 0,0,-1, 0,1,0], the one they all use), `innerAngle` 25, `outerAngle` 150 to 160 and `range` 45 to 70 m growing with head height, `intensityUnit` lm with `brightness` = intensity / 5000, `isEnabled` false and `nightLight` '1', so stock `core_environment` switches every lamp from 18:00 to 6:00 and the clustered renderer culls them and budgets shadows. Brightness matches the PointLights the user approved (12000 / 20000 pi, median 0.163), scaled by the pool's peak. Lamp-post heads cast shadows, pool-only lamps don't. Before that they were PointLights, which `fh1Extras.lua` switched nearest-first: 20 within 250 m showed about 6, and even 250 within 1 km with a 40 to 60 m radius only showed right by the camera. Lowering `ClusterShading.minLightScreenSize` was tried and reverted, as it changes the player's settings. Lamp glass glows at night from the next rebuild: FH1's `h_diff_spec_ao_2__emissive_lm` lamps keep an emissive mask in slot position 3, black except the glass, which `build3.py` (`GLOW`) makes the material's `emissiveMap` under `instanceEmissive` (`GLOW_NITS` 3000, by eye from West Coast's floodlight at 4000), and their TSStatics carry `nightEmissive` with a black `instanceColor`.

## Static LODs

- 250 m cells (`CELL`), one shape per cell and category.
- 3 details from FH1's LOD00, 01 and 02. If a group lacks a level, its coarsest mesh stands in.
- Switch distances: `LOD_DIST = (300, 800)`, from the shape's edge. Props vanish at `PROP_FAR = 1600`. `col` shapes never vanish.
- Detail sizes are computed from each shape's radius and `PX = 1250` (pixels of radius per radius/distance at 1440p), so all cells switch at the same distance. The engine measures from the centre, so `detail_sizes` adds the radius: tile groups can be 1 km wide. A name like `o0_a71` means detail size 71.
- Shape tree: `base00 > start01 > o<i>_a<size>`, as in stock shapes.
- A cell's meshes drawn at all three LODs go in a second shape, `c<u>_<cat>f`, with one detail (size `detail_sizes(...)[2]`) and its own `Colmesh`. In one shape they'd be cooked once per detail. `animated.py` reads ground from both `_col` and `_colf` shapes.
- Each LOD and `Colmesh` node is a full copy in the `.cdae`: size fits 56.1 bytes per instantiated vertex and 11.8 per triangle, R2 0.9999. `write_lod` welds vertices that would write the same text, which took Colorado's predicted cooked shapes from 2.80 GB to 1.91 GB.

## Collision

`col` and `pro` shapes collide with their coarsest LOD. Barriers and objects under 15 m have no LOD2 copy and vanish at 800 m (`FARCUT`). Each has a `collision-1` node and `collisionType: Collision Mesh` on the `TSStatic`. `vis` shapes have none. The user drove this on Red Rock with no problems.

FH1's `.fiz` tiles were tried and reverted. Cars behaved as if under 10x gravity and crumpled, and RAM and fps got worse. Their triangles have mixed winding (47% up, 53% down), and BeamNG collision is one-sided, which is the likely cause. Any retry must re-wind them to face up, and must not duplicate vertex arrays. `fiz.py` still reads them.

## Plants

Plants use BeamNG's forest system, not `TSStatic`. Items have `o0_a120`, `o*_a40` and `bb_autobillboard25`: LOD00, LOD01, then an imposter. `FH1_IMPOSTERS=0` keeps LOD01 at distance instead. Stride-16 tree cards are skipped by the world mesh path.

## Scene

`visibleDistance` 6000, `shadowDistance` 1600, `TimeOfDay` fixed, `ScatterSky`, one `SpawnSphere`. The spawn is the first race route checkpoint near Red Rock, moved onto the road height. `info.json` holds the title and spawn point.

The level starts at 9:30 (`TIME` in `sky.py`), as the user asked. The sky is a `SkyBox`, `fh1_skybox`, over the `ScatterSky`. `sky.py` bakes ten DXT1 cubemaps from `TimeOfDayA.xml`, at FH1's 0:00, 6:45, 7:25, 10:00, 12:00, 15:36, 18:10, 19:20, 19:55 and 20:40 (about 41 MB, 1024 per face). The 15:36 bake keeps the names `fh1_sky` and `fh1_sky_cubemap`, so the default look and the rivers' reflections are unchanged. Every bake scales the measured 15:36 gradient by FH1's atmosphere colours and `SkyGain` relative to 15:36, and uses FH1's cloud and ground colours for its own minute. Stars come from `Stars.bin`, faded by `StarFade`. In-game they were invisible as 0.6 px points, because mips averaged them away, so they're 0.9 px and bakes with `StarFade` over 0.1 build mips that keep the brightest of each 2x2 (`dds.save(peak=True)`). The moon is `Moon2.xds` (full moon with glow) at FH1's fixed night position, about 11 degrees up in the south-east, faded by `SunObjectMoon` and `SunObjectAlpha`, and 3 degrees wide by eye. FH1's sun never sets: its night runs 20:02 to 6:28. BeamNG's legacy sun sets at 18:00, so each bake takes over at a BeamNG hour chosen by phase: night 20:30, predawn 5:00, sunrise 5:45, morning 6:45, midday 8:30, afternoon 14:00, sunset 16:45, golden 17:30, dusk 18:00, twilight 19:00. `fh1_sky.json` lists the bakes and the bake for each quarter hour. Every 2 s, `fh1Extras.lua` checks it, and when the bake changes it sets the skybox `material`, `theLevelInfo.globalEnviromentMap` and every `River`'s `cubemap`, each followed by `postApply`. Switches are cuts, as a `SkyBox` can't cross-fade. Bakes in `BEAMNG_SKY` (night, 20:30 to 5:00) set `hideBox`, and the Lua hides the skybox so BeamNG's `ScatterSky` and `core_celestial` draw the night: their stars are additive and stay visible at full night, where the baked ones faded with the scene. The user chose this over FH1's stars. The night bake still feeds reflections. In-game the switching works. The user found FH1's 9:00 bake, held until 10:30, far yellower than FH1, which is blue most of the day: FH1's horizon is only yellow from about 8:00 to 9:45, so morning is now 10:00 and short. Needs an in-game look: stars after the mip change, the 18:00 sunset match, and night brightness.

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
