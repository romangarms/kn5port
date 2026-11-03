# FH1 Colorado to BeamNG (experiment)

Converts Forza Horizon (2012) Colorado into a BeamNG level from your own legally dumped copy: the whole map (16.4 km square, 4 m grid)
or just Red Rock (4 km, 2 m). This folder is separate from kn5port's converter and only imports a few helpers from it.

**Never commit or share game-derived data.** `fh1/.gitignore` is a whitelist: only `pipeline/*.py`, `pipeline/lzx.c`, `docs/*.md` and this file are tracked.
Extracted files, captures and generated levels live in `fh1/` and stay local. Converted levels are for personal use only.

## Notes

| File | Read it for |
|---|---|
| [docs/open-work.md](docs/open-work.md) | **Start here:** current state, task list, what the user has decided |
| [docs/formats.md](docs/formats.md) | Every FH1 file format and how well each is proven |
| [docs/free-roam.md](docs/free-roam.md) | How free-roam objects are chosen, and what's been ruled out |
| [docs/pipeline.md](docs/pipeline.md) | Scripts, data files, `build3.py` layout, checks, shell pitfalls |
| [docs/beamng-output.md](docs/beamng-output.md) | Terrain, blending, LODs, collision, plants, performance |
| [docs/tools-and-re.md](docs/tools-and-re.md) | Live capture, recomp tools, how to test a format theory |

## Running it

```
cd fh1/pipeline
clang -O2 -fno-builtin -ffreestanding -fno-stack-protector -mno-stack-arg-probe -c lzx.c -o lzx.o
lld-link -dll -noentry -nodefaultlib -export:lzx_decode -out:lzx.dll lzx.o      # MSYS_NO_PATHCONV=1 in Git Bash
python prepare.py                     # about 3 minutes, FH1_DUMP overrides the dump path
python buildq.py submit -m "what changed" --by <session> --chunk colorado   # queue a build: the way to build when other sessions may be editing
python buildq.py status               # running, queued and recent builds, with notes and changed files
FH1_CHUNK=colorado python build3.py   # whole map, 3.2 GB, 7 minutes, 4 GB of RAM. Default chunk is redrock
FH1_CHUNK=clearsprings python build3.py   # 4 km around Clear Springs (BeamNG centre 2000, -1500) as fh1_clearsprings, about 4 minutes. Through buildq.py with --chunk clearsprings
python sky.py fh1_colorado            # FH1 sky for ten times of day and lighting, under a minute. build3.py runs this itself unless given an output folder. Each build wipes the level
python navmap.py fh1_colorado         # FH1 road graph for the minimap and AI, run the same way as sky.py
python grade.py fh1_colorado          # saturation and contrast baked into the map textures, run the same way. Safe to rerun with new values
python levelinfo.py fh1_colorado      # freeroam menu text and previews, spawn points, location zones, HUD road names. Run the same way
python ambience.py fh1_colorado       # FH1's regional ambient loops, needs ffmpeg. Run the same way
python particles.py fh1_colorado      # FH1's water, smoke and sprinkler particles. Run the same way, after levelinfo.py
python animated.py fh1_colorado       # FH1's windmills, turbines, tractors, combines and crop duster. Run the same way
python grass.py fh1_colorado          # FH1 grass, reeds, flowers and corn as forest clumps. Run the same way
python nightlights.py fh1_colorado    # Prototype: Carson's night lightmaps as glowing overlays, switched by stock core_environment. Run the same way
```

The build writes to `mods/unpacked/<level>`, or pass an output folder. Then fully restart BeamNG. Python needs numpy, scipy, numba, pillow.

## Status

Working and tested in-game around Red Rock: real textures, terrain blending, dirt and asphalt roads, plants, buildings, the outpost, free-roam object selection, LODs (about 120 fps on a 2070 Super at 1440p).

The whole map builds and passes file checks, but hasn't been tested in-game.

Missing: normal maps, lightmaps, water, about 3% of plants. `Grass_Ungrouped` sets are placed by `grass.py`, untested in-game. Semi wheels are wrong.
