# FH1 Colorado to BeamNG

Converts Forza Horizon (2012) Colorado into a BeamNG level from your own legally dumped copy: the whole map (16.4 km square, 4 m grid)
or a 4 km chunk around Red Rock or Clear Springs (2 m grid). This folder is separate from kn5port's AC converter and only imports a few helpers from it.

**Never commit or share game-derived data.** `fh1/.gitignore` is a whitelist: only `pipeline/*.py`, `pipeline/lzx.c`, `docs/*.md` and this file are tracked.
Extracted files, captures and generated levels live in `fh1/` and stay local. Converted levels are for personal use only.

## Notes

| File | Read it for |
|---|---|
| [docs/open-work.md](docs/open-work.md) | **Start here:** current state, task list, what the user has decided |
| [docs/formats.md](docs/formats.md) | Every FH1 file format and how well each is proven |
| [docs/free-roam.md](docs/free-roam.md) | How free-roam objects are chosen, and what's been ruled out |
| [docs/pipeline.md](docs/pipeline.md) | Scripts, data files, `build3.py` layout, the build queue, other tracks, shell pitfalls |
| [docs/beamng-output.md](docs/beamng-output.md) | Terrain, blending, LODs, collision, plants, sky, races, performance |
| [docs/tools-and-re.md](docs/tools-and-re.md) | Live capture, recomp tools, how to test a format theory |

## Running it

The easy way is the kn5port app's **Forza Horizon 1** tab (`kn5port-app` after `pip install .`): it takes a GOD package, an ISO or extracted files, runs the steps below into a work folder, and checks ffmpeg, disk space and that BeamNG is closed first. See [the main README](../README.md#forza-horizon-1).

By hand:

```
cd fh1/pipeline
echo D:/path/to/FH1 > ../dump_path.txt     # the folder holding default.xex and media/ (or set FH1_DUMP)
python prepare.py                          # decode the map's tables, about 3 minutes, once per copy of the game
python buildq.py submit -m "what changed" --by <name> --chunk colorado   # queue a build (the way to build when other sessions may be editing)
python buildq.py status                    # running, queued and recent builds, with notes and changed files
FH1_CHUNK=colorado python build3.py        # or run a build directly: whole map as fh1_colorado, about 10 minutes, 3.3 GB
python radio.py                            # FH1's radio as its own mod, fh1_radio (334 MB), about a minute. Needs no level
python soundtrack.py                       # FH1's 67 radio songs as tagged MP3s with station cover art, into ~/Music/Forza Horizon Soundtrack
```

| Chunk | Level | Area | Build |
|---|---|---|---|
| `colorado` | `fh1_colorado` | Whole map, 4 m grid | About 10 minutes, 3.3 GB |
| `redrock` (default) | `fh1_redrock` | 4 km around Red Rock, 2 m grid | About 2 minutes, 0.3 GB |
| `clearsprings` | `fh1_clearsprings` | 4 km around Clear Springs (BeamNG centre 2000, -1500), 2 m grid | About 4 minutes, 0.5 GB |
| `coloradodirt` | `fh1_coloradodirt` | The Rally Expansion DLC's map, from `fh1/dirt/` after `prepare_dlc.py` (see `docs/pipeline.md`, Other tracks) | 1.9 GB. Not in the app yet, not yet tried in-game |

`build3.py` runs the follow-up scripts itself, side by side, once the level's shapes are written: `sky` (FH1's sky for ten times of day, fog and bloom zones), `navmap` (road graph for the minimap and AI), `grass`, `water`, `animated` (windmills, turbines, rides, balloons, the blimp, farm machines, the crop duster, cable cars, birds), `particles` (water, smoke, sprinklers, leaves, campfires, fireworks), `spectators` (the festival crowd), `nightlights` (lit windows and street lamps), `ambience` (regional loops, needs ffmpeg), `grade` (FH1's colour grade baked into the textures), `levelinfo` (menu text and previews, spawns at the festival and FH1's outposts, location zones, HUD road names) and `races` (FH1's 118 events as BeamNG time trials). Each can be rerun alone on an installed level, for example `python grade.py fh1_colorado`.

The game's zips are XMem LZX. `lzxdec.py` decodes them with `lzx.dll` when it's built, else with a numba port of `lzx.c` (same output, checked by CRC). The DLL is a bit faster:

```
clang -O2 -fno-builtin -ffreestanding -fno-stack-protector -mno-stack-arg-probe -c lzx.c -o lzx.o
lld-link -dll -noentry -nodefaultlib -export:lzx_decode -out:lzx.dll lzx.o      # MSYS_NO_PATHCONV=1 in Git Bash
```

The build writes to `mods/unpacked/<level>` (`KN5PORT_MODS` overrides the mods folder), or pass an output folder. Then fully restart BeamNG. Python needs numpy, scipy, numba, pillow, and ffmpeg on PATH.

## Status

The whole map builds, loads and has been driven end to end. In-game it has:

- FH1's own terrain, roads, buildings, props and plants with their real textures and normal maps, LODs, and simple colliders for props.
- Rivers and lakes as BeamNG water at FH1's heights, and the waterfalls.
- FH1's sky at ten times of day, with its fog and bloom zones. At night, buildings' lightmaps, lit windows and about 2,500 street lamps.
- Moving scenery: wind turbines, windmills, the festival's rides, light rigs and fireworks, balloons, the blimp, tractors, combines, the crop duster, Beaumont's cable cars and birds.
- Particles (water spray, smoke, sprinklers, leaves, campfires), FH1's regional ambience and placed sounds, and the festival crowd.
- Free roam as FH1 shows it: event start pens and signs stay, race-only props load with their race.
- Spawns at the festival and ten outposts, a minimap of FH1's roads with road names, and 118 time trials. The radio is a separate mod.

On a 2070 Super at 1440p the whole map runs at around 90 fps in towns and takes about 7.5 GB of RAM once settled.

Not done: textures are right almost everywhere, but a few are still off. Races are time trials with no AI rivals. Skinned animation (tube men, animals, people walking) is left out, the festival crowd stands still, and some ride sounds and sprays aren't placed. The Rally Expansion builds but isn't tested in-game. See [docs/open-work.md](docs/open-work.md) for the full list.
