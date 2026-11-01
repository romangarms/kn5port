<p align="center"><img src="packaging/kn5port.png" alt="kn5port icon" width="128"></p>

# kn5port

Convert Assetto Corsa tracks **that you own** into BeamNG.drive levels, on your own computer.

kn5port contains no game content. It reads the track files already installed on your machine, reuses trees,
grass and terrain materials from your own BeamNG.drive install, and writes the converted level into your local
BeamNG mods folder. Like game recompilation projects, the repository is only the tool; you bring the data.

> **Personal use only.** Converted levels are derivative works of someone else's track. Never upload, share or
> sell them, or post them in issues. Check the track's license first: some mods explicitly forbid conversions
> to other games, and that applies to you even if you never share the result. kn5port only reads unprotected
> `.kn5` files and does not attempt to get around encryption or other protection.

## What it does

| AC | BeamNG |
|---|---|
| Road, kerb, gravel, wall meshes (physics surfaces) | Collision meshes with matching ground models (asphalt, gravel, grass, ...) |
| Visual meshes (buildings, props, decals) | Static meshes, merged per material and split into 500 m cells |
| Multilayer / grass / sand ground and distant terrain | A `TerrainBlock` (up to 4096²), fitted under the road, with stock terrain materials and GroundCover grass |
| Card / mesh trees, bushes, grass cards | BeamNG forest items (redwoods, pines, oaks, shrubs, grass clumps) from a stock level, scaled to the AC heights |
| Layouts, pit / start positions, previews, descriptions | Spawn points with previews and descriptions in the level picker |

It also generates dirt skirts along road edges where the terrain can't follow them, keeps grass and trees off
the road, and checks the result (including that every AC AI racing line has road under it).

## Install

**Windows app:** download `kn5port.exe` from the [releases](../../releases) page and run it. No install needed;
it opens in its own window (uses the WebView2 runtime that ships with Windows 10/11).

**From source** (Python 3.9+):

```
git clone https://github.com/romangarms/kn5port
cd kn5port
pip install .[app]
```

## Use

### App

![Picking a track: previews, convertibility status and what kn5port finds in it](docs/screenshots/track.png)

![Conversion options, each with its benefits and trade-offs](docs/screenshots/options.png)

![A finished conversion with the validation results](docs/screenshots/converted.png)

<sub>The tracks in these screenshots are made-up demo data (`python packaging/screenshots.py` regenerates them).</sub>

Run `kn5port.exe` (or `kn5port-app` from source). It:

- finds Assetto Corsa and BeamNG through Steam, on any drive (Steam's location comes from the registry, then
  every library in `libraryfolders.vdf`), and your BeamNG mods folder in `%LOCALAPPDATA%\BeamNG`; anything it
  can't find, you pick once with a folder browser
- lists your tracks with their previews and checks every model file in the background (structure, texture
  signatures and sampled mesh data): **✓ ready**, **! partial** (unreadable files are skipped) or **🔒 protected**
  (can't be converted). Results are cached, so later launches are instant
- right-click a track to open its Assetto Corsa folder, or the converted BeamNG level / mod folder
- marks tracks already converted into your mods folder
- converts with one button, with options for the scenery style (which BeamNG map to borrow trees, ground and
  grass from), ground (BeamNG terrain or AC's own), trees (BeamNG vegetation, AC's trees or none), tree density,
  terrain shading and road fixes, each listing its benefits and trade-offs; then shows live progress, the validation results and a button to open the level folder

`kn5port ui` serves the same interface in your browser instead (only on `127.0.0.1`, with a per-session
access token).

### Command line

```
kn5port inspect  "D:\Steam\steamapps\common\assettocorsa\content\tracks\<track>"
kn5port convert  "D:\Steam\steamapps\common\assettocorsa\content\tracks\<track>"
```

`convert` finds your BeamNG install (via Steam) and mods folder automatically and writes
`mods\unpacked\<track>`. **Fully restart BeamNG** before loading a converted level (it caches materials).

Useful options:

| option | |
|---|---|
| `--terrain off` | keep AC's ground as meshes instead of building a terrain (faster, no GroundCover) |
| `--palette italy` | borrow trees, terrain materials and grass from another stock BeamNG map (see below) |
| `--tree-density 0.5` | thin trees and shrubs for performance |
| `--ao 0.2`, `--near-gain 1.3` | lighter terrain shading / brighter near-road ground |
| `--no-forest` / `--keep-ac-trees` | skip tree replacement / keep AC's own tree meshes |
| `--no-edge-fill` | don't raise the terrain to the road edges or hang curtains under them (if they show as walls/ramps) |
| `--show-hidden` | also draw meshes AC hides; off by default because physics-only road copies on ripped (e.g. Forza) tracks flicker against the visual road |
| `--layer-lift 0.02` | metres to raise each road layer (patch, line, dirt decal) that lies exactly on an earlier-drawn surface, so stacked layers don't flicker; `0` turns it off |
| `--no-ground-layers` | keep AC's blended (multilayer) ground as one low-res texture instead of splitting it into sharp grass/dirt/rock textures with hard edges between them |
| `--profile my.json` | per-track overrides (see below) |
| `--beamng`, `--mods`, `--out` | paths, if auto-detection fails |

`kn5port validate <level folder> --ac-track <track>` re-checks a converted level.

## Building the exe

On Windows: `powershell -ExecutionPolicy Bypass -File packaging\build.ps1` → `dist\kn5port.exe`.
Pushing a tag like `v0.2.0` makes GitHub Actions run the tests, build the exe and attach it to a release
([`.github/workflows/release.yml`](.github/workflows/release.yml)). The exe contains only kn5port and its
Python libraries, never game content.

## Palettes

Trees, terrain materials and grass come from a stock BeamNG map of your choice (West Coast USA by default).
kn5port analyses the map from your own install: its forest items are sorted into conifers, broadleaf trees,
palms, shrubs and grass by name, measured, and weighted by how often the map uses them; its terrain materials
are matched to roles (forest floor, grass, dry grass, dirt, gravel, rock, sand). Each AC tree then gets the
item closest to its own height. So Italy gives cypresses, pines, oaks and olives; East Coast USA Douglas firs
and aspens; Jungle Rock Island palms and tropical trees; Utah junipers. West Coast USA uses a hand-tuned mapping.

## Track profiles

Everything track-specific (surface → ground model mapping, which meshes are terrain, which materials are
vegetation and what species, which donor items replace them, terrain size and shading) lives in
[`kn5port/profile.py`](kn5port/profile.py). The defaults work on most tracks; when one needs tweaks, put a JSON
file with just the fields to change next to the track as `kn5port.json`, or pass `--profile`. See
[`profiles/skyline_boulevard.json`](profiles/skyline_boulevard.json) for the format. Run `kn5port inspect` to
see how a track is being interpreted before converting.

## Known limits

- Terrain is at most 4096² samples, so large maps get coarser terrain (e.g. 5 m for a 20 km road); road edges
  are covered by generated skirts but may look slightly raised in places.
- AC shaders (CSP GrassFX, multilayer detail, reflections) don't exist in BeamNG; materials are simplified.
- Trees, terrain materials and grass come from a stock BeamNG level (West Coast USA by default), so the look is
  BeamNG's, placed where the AC track had vegetation.
- No AI paths, race timing or night lighting yet.

## How it works

See [docs/how-it-works.md](docs/how-it-works.md), and [docs/learnings.md](docs/learnings.md) for what broke
along the way, how it was fixed and what's still open.

## License

MIT for the code in this repository. It does not grant any rights to tracks you convert or to BeamNG content.
