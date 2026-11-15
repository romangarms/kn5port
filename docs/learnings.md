# Learnings: what broke and why

A log of the problems hit while getting real tracks (Skyline Boulevard, Montserrat/`anon_camino` and others)
into BeamNG, written as **symptom → cause → fix** so the next person who sees the same thing in-game can find it.
[how-it-works.md](how-it-works.md) explains the pipeline and the format details; this file records how we got
there, what's still open, and how to debug.

Always restart BeamNG fully before judging a fix. It caches materials for the whole game session, so a
reloaded level can still show the old look.

---

## Rendering

| Symptom in BeamNG | Cause | Fix (where) |
|---|---|---|
| Roads purple/magenta, terrain pale or washed out | Texture colour space comes from the file name. Plain `.png` isn't treated as sRGB colour | Baked textures are named `*.color.png`, `*.data.png` or `*.normal.png` (`level.py` `TextureBaker`) |
| Trees opaque (no cut-out) | v1.5 materials read alpha from `opacityMap`, not from the base colour's alpha | Separate opacity map plus `alphaTest`/`alphaRef` (`bng_material`) |
| Trees fine far away, orange or missing up close | Borrowed donor tree shapes reference materials that only exist in the donor level. Far away, imposters hide it | Copy the donor shapes' material definitions into the level (`forest.donor_materials`) |
| Near-road terrain nearly black or a flat grey | Terrain base textures were 256² while the texture set's `baseTexSize` was 4096, so BeamNG rejected them | Bake base maps at exactly `baseTexSize` |
| Ground near roads very dark | Strong ambient occlusion from donor terrain materials, plus dark AC ground colour | `--ao` (default 0.35) and an auto near-road brightness gain |
| "Import failed" messages, textures missing | BeamNG requires power-of-two sides ≥ 16 px. It also rejects 24-bit uncompressed DDS | Resize to power of two (16–4096). Pass DDS through only if DXT/BC/DX10 or 32-bit, otherwise re-encode as PNG (`raw_ok`) |
| Lakes drawn as a grid of squares | AC water is a tiling diffuse plus normal map made glossy by a huge specular (`ksSpecular 8`, `ksSpecularEXP 250`). At BeamNG's default roughness 0.85 every tile of the normal map reads as a matte square | Water materials (matched by `water_re`) drop the normal map and get roughness 0.04 |
| Road flickers (z-fighting), worst on Forza rips | The track ships a **hidden physics copy of the road** exactly on the visual road (`visible=0`, `renderable=0`, material `blank`; about 600k triangles on Montserrat). AC never draws it, but we did | `Mesh.hidden` reads both flags. Hidden physics meshes get an invisible alpha-discarded material that still collides; hidden visual meshes are dropped. `--show-hidden` undoes this |
| Road flickers on non-Forza rips too (Project CARS 2 California Highway): dark blotches over the whole road and lots | **Stacked visible layers**: base road plus patch/line/dirt-decal meshes at the same height. AC resolves them by draw order (later wins at equal depth); BeamNG has no such order | `overlay.py` lifts each layer 2 cm above the earlier-drawn surfaces it lies on, in AC's draw order (opaque by file order, then transparent). The level is per triangle, not per mesh: a per-mesh level carried the deepest stack under a mesh to all of it, and lines and grooves at Barcelona's start floated up to 19 cm. `--layer-lift 0` / "Separate stacked road layers" off undoes it |
| Decal meshes drawn as solid sheets | AC blend mode 2 (alpha-to-coverage) wasn't treated as a cut-out | Blend mode 2 maps to `alphaTest` (`bng_material`) |
| Ground and road textures blurry up close with "Keep AC meshes" | AC shaders tile small detail textures over a low-res base; we baked only the base (multilayer: only each layer's average colour) | AC detail textures become BeamNG `detailMap` + `detailScale` (MultiMap `txDetail` × `detailUVMultiplier`; multilayer: the layer covering most ground × its `multR..A`), baked to neutral grey (`TextureBaker.detail`) |
| Grass, rock and gravel stretched into a few huge blurry pixels (California Highway, mesh mode) | `ksPerPixelNM_UVMult` samples its textures at `uv * diffuseMult`; we used the raw UVs, so a tiling texture covered a whole hillside once | `Material.uv_mult` scales the UVs written to the DAE (and the terrain's colour sampling). Materials now carry `ac_uv` (AC's tiling props) and `ac_m_per_uv` (metres per texture repeat) for tracing this kind of thing |
| Lots, verges, shoulders and hillsides white, with fine moiré stripes, worse further away (California Highway, both modes) | The track's **separate physics kn5** (`*_phy`): its meshes are hidden in AC and got our invisible material, alpha-test only. BeamNG drew that as plain white. (Re-encoding every DDS to PNG was tried first and reverted: BeamNG then stuck on "importing texture") | `hidden_material` is also translucent with zero opacity (factor plus an all-black opacity map) and casts no shadow. Skirts never take the hidden material as their source. To find a white surface's source, click it in the World Editor: the object name says which kn5 it came from |
| Multilayer hillsides still flat and blurry; detail maps had no visible effect (California Highway) | Its multilayer materials have `multR..A` of 0.01–0.2 on meshes whose UVs span up to 2.8 km: CSP's object-space variant, where a layer tiles every 1/mult **metres**. Read as UV tiling that gave tiles kilometres wide | `detail_scale`: mult < 1 means object space, converted to UV repeats with the mesh's measured metres per UV unit (`ac_m_per_uv`). The detail layer is the dominant one that actually has a tiling. Not on road-like materials: one layer's detail over a whole road read as gravel |
| Ground still low-res in mesh mode after the detail fixes (California Highway) | One BeamNG material shows one colour texture, so the multilayer bake squeezes four tiling layers into one image over meshes up to 2.8 km per UV unit (~0.5 m per pixel) | `layers.py`: each triangle takes the layer with the highest mask weight at its centre and that layer's own texture, UVs scaled to AC's tiling; the macro map's variation returns as a detail map at 1/scale. Hard edges between layers instead of blends. `--no-ground-layers` / "Sharp layered ground" off restores the bake |
| Hillsides unchanged by the layer split and detail maps; only small verges got `_layerR/G/B` materials (California Highway) | The road check (`ROAD_LIKE`) matched "ROAD" inside the macro map name `Sec5_CAH_**Broad**.dds`, so every big hillside material counted as road: no split, no detail map, and kept as a mesh in terrain mode | `ROAD_LIKE` ignores "ROAD" after a B. Check the converted `materials.json` for `*_layer*` entries of the big ground materials |
| After the layer split worked: hillsides near-black and brown with hard-edged sand and rock blotches (California Highway) | Each layer's own texture was the base colour. AC multiplies the layers over the macro map, which carries the colour; the layer textures alone are far darker (grass 0.23 vs macro 0.48) | Layer materials now use the macro map as colour (original UVs, soft AC colours) and the layer texture as a tiled detail map at contrast 0.6 (1.6 gave white speckles). One material per layer and tiling, since meshes sharing an AC material can have different metres per UV |
| Numbered node names render at odd distances | Torque reads a trailing number in a Collada node name as a detail size | Node names always end in letters |

**Ruling out the terrain first.** Before blaming a material, sample the terrain height under the problem area
and compare it with the mesh. For the water squares, the terrain was already 3–17 m under the lake, so the cause
had to be the material.

**Looking at textures without a viewer.** Printing a coarse ASCII luminance map of a texture, along with its
min/max/mean per channel, quickly shows tiling, flat textures (water.dds varied only between 38 and 44) and
unusual alpha use. Read DDS headers (FourCC and bit count) before assuming a format.

## Geometry and terrain

| Symptom | Cause | Fix |
|---|---|---|
| Road floating above the terrain everywhere | The carve followed the *lowest* surface, and AC hangs sloped curtains off road edges into the ground | The terrain starts just under the **topmost** surface. An iterative solver then lowers only the grid corners needed so neither cell triangulation rises above a driving surface |
| Gaps or visible drops at road edges | A 5 m terrain grid can't follow a shoulder edge | Lift terrain corners to meet the edges, and generate curtains ("skirts") under edges where the terrain still falls away. Both can look wrong on odd road-edge geometry, so `--no-edge-fill` turns them off ("Fill gaps at road edges" in the app) |
| Skirts silently vanished after a refactor | Dropped code path; nothing checked for it | Restored. The validator now flags SimGroups that are declared without their folder |
| Grass poking through the road | GroundCover grows on every terrain layer it's allowed on, including under roads | A "verge" layer that no GroundCover uses, under and one cell around every driving surface, and under water |
| Part of the terrain missing (Montserrat) | Physics keys like `GRASS`/`ROAD` come from AC's **built-in** `system/data/surfaces.ini`, not just the track's | `read_surfaces` merges built-in defaults, the AC install's file and the track's files |
| Multilayer road replaced by terrain | Any `ksMultilayer` mesh was treated as ground | The physics key decides first. Road-like material names (checked on material name plus `txDiffuse` only; mask textures caused false positives) always stay meshes |
| Lake bed poking through water | Coarse AC lake beds | The terrain is capped `WATER_DEPTH` (0.6 m) below every water triangle |

## Vegetation

- AC trees come in many forms: crossed cards, single cards, 3D meshes with separate trunk meshes, and
  elevated crown cards. Treat everything as "elements" (cards, or connected parts), then classify them:
  - Grounded tall elements are whole trees.
  - Trunks claim the crowns above them.
  - Everything else is grid-filled.
- Use donor items' measured DAE heights to scale them to the AC height. Clamp the scale, or saplings become
  giant trees.
- Weight a palette by how much the donor level actually uses each item, or rarely used props dominate. Grass
  items taller than 1.5 m aren't grass.
- Palettes are cached per BeamNG level (`%APPDATA%/kn5port/palettes`). Bump `PALETTE_VERSION` whenever the
  analysis changes.

## Track detection and protection

- Encryption in AC is almost entirely a **car** thing. Of 93 installed tracks, none was encrypted. Don't
  assume a "protected" count of 0 is a bug.
- Detect protection by actually checking content: the header, sane counts, the root node type, image magic
  bytes for textures, and a sample of meshes for plausible geometry (`KN5.verify`). File size or extension
  checks are useless.
- A track can be **partially** readable (one protected kn5 among many). Report it as such rather than as
  convertible or not.
- Layouts are the `ui/*/ui_track.json` folders. Single-layout tracks keep `ui_track.json` directly in `ui/`.

## Desktop app

- In a **windowed** PyInstaller exe there are no console handles, so subprocess pipes hang and the conversion
  never starts. Conversions run **in-process** on a thread, with per-thread stdout routing (`_OutputRouter`).
  Cancelling uses `PyThreadState_SetAsyncExc`.
- When the exe is called with arguments it attaches to the parent console, so the CLI still prints.
- Steam libraries: read the install path from the registry, then `libraryfolders.vdf`, then look for
  `appmanifest_244210.acf` (AC) or `appmanifest_284160.acf` (BeamNG) in each library.
- The local server binds 127.0.0.1 and checks a session token and the Host header. `track_dir` re-validates
  names, so a request can't escape the tracks folder through symlinks.
- Re-rendering the track grid reset its scroll position. Keep `scrollTop` across renders. Use
  `grid-auto-rows: max-content` so long titles don't get cropped.

## Performance and environment

- Large tracks have millions of triangles, and the VM ran out of memory. Process the colour stage in chunks
  (1.5M points), free big arrays right after the fit, and cap textures at 4096.
- NumPy 2 removed the 2D form of `np.cross`. Write the 2D cross product out by hand.

---

## Open issues (v1)

- **Road flicker far from the camera.** Stacked layers are now separated by 2 cm (see Rendering). If flicker
  remains only at long range, it is depth precision: try `--layer-lift 0.04`.
- **Some grass looks emissive / lit in shade.** Untested suspects:
  - AC materials with `ksEmissive > 0` mapped to `emissiveFactor`. AC tracks sometimes use emissive to fake
    brightness on vegetation.
  - Alpha-tested grass cards with flipped or upward normals.
  - Donor GroundCover materials.

  Next step: find which material it is (the BeamNG material editor shows it when you click the object), then
  check its `ac_shader`/`ksEmissive` in the generated `materials.json`.
- No AI paths, race timing or night lighting. Water is a mesh, not a BeamNG `WaterBlock` (no swimming or
  buoyancy).

## Debugging checklist

1. `kn5port inspect <track>`: how meshes are classified (physics, ground, vegetation, hidden…).
2. `kn5port inspect <track> --materials`: the largest materials by area with shader, mesh/hidden counts, metres per
   UV unit, tiling props and textures. Start here for blurry, stretched or oddly coloured surfaces.
3. `kn5port validate <level> --ac-track <track>`: missing textures and folders, texture sizes, and the
   drive check (road under every AI line point, no terrain above it).
4. Generated materials keep `ac_shader` and `ac_material`, and are tagged `kn5_ground`, `kn5_hidden` or
   `kn5_water`. Grep `art/shapes/<level>.materials.json` to trace an in-game material back to AC.
5. Check KN5 mesh flags (`visible`, `renderable`, `transparent`, LOD in/out). They explain a lot about rips.
6. Restart BeamNG fully.
