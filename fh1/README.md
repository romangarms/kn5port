# FH1 Colorado to BeamNG (experiment)

Converts a chunk of Forza Horizon (2012) Colorado into a BeamNG level from your own legally dumped copy. Currently Red Rock:
a 4 × 4 km square. This folder is separate from kn5port's converter and only imports a few helpers from it.

**Never commit or share game-derived data.** `fh1/.gitignore` is a whitelist: only `pipeline/*.py`, `pipeline/lzx.c` and this file are tracked.
Extracted files, captures and generated levels live in `fh1/` and stay local. Converted levels are for personal use only.

## Layout

- `pipeline/`: scripts. Run them from this folder; they read and write `../`.
- `x/`: extracted `bin.zip` contents.
- `memobj_all.pkl`, `memrec.pkl`: the live Xenia capture. **These took a real drive to collect, so back them up.**
- `zonevote.pkl`, `cat2.pkl`, `meshtab.pkl`, `mt.npy` and others: derived. `prepare.py` rebuilds them.

## Running it

```
cd fh1/pipeline
clang -O2 -fno-builtin -ffreestanding -fno-stack-protector -mno-stack-arg-probe -c lzx.c -o lzx.o
lld-link -dll -noentry -nodefaultlib -export:lzx_decode -out:lzx.dll lzx.o      # MSYS_NO_PATHCONV=1 in Git Bash
python prepare.py        # about 3 minutes, FH1_DUMP overrides the dump path
python build3.py         # writes mods/unpacked/fh1_redrock, or pass an output folder
```

Then fully restart BeamNG. Python needs numpy, scipy, numba, pillow.

Other tools: `memscan.py <xenia pid> <passes>` (read-only live capture), `x1.py` and `x2.py` (recomp string index and function viewer),
`pv.py` and `v2.py` (software renderer to compare UV formulas), `xtr.py` (Xenia GPU trace reader).
The chunk is set by `CX, CZ, N, SQ` at the top of `build3.py`.

## Formats

**`bin.zip`**: no local headers. Offset is in extra field 0x1123, method 21 is XMem LZX with window 17. Entries repeat per zone, 57k unique names.

**`*.rmb.bin`**: big endian, world space or origin-local. Header holds the mesh count (`hdr[4]`). Each mesh: name, vertex count, stride, vertices, then sections
(name, 8 ints, 8 floats, index width and count, strips with 0xFFFF restart). Materials follow the last mesh.
Vertices are position (3 floats), 10:10:10 normal, s16 UV; 16-byte vertices are tree cards (position plus UV).
UV is `s * scale / 65535` with scale from the section's `fl[6:8]`. No offset: the old offset form only looked right on tiling textures.

**Textures**: `*.bix` plus `*_B.bix`: BIX1 header, mips, top mip, Xenos tiled DXT, byte swapped in 16-bit words.

**`Colorado_00.pvs`** (FPVS): at 0xAC a 28-byte texture table (index to `_0x` id); 173 shader names; a 62,173-record 18-byte draw table (draw id to mesh number, `f` flag);
14,560 per-mesh records (texture index list, shader list, OBB). A material's slot N is the Nth texture in the mesh's record.

**Coordinates**: meshes are x east, y up, z south, matching the developer map art. `GameObjs.xml`, `CollObjs.xml`, routes and `.pgeo` use the opposite z sign.
A game matrix with negative determinant already carries that flip.

**`*.pgeo`** (`Models_*`): offset 0x50 is the byte size of the 40-byte instance block at the end of the file. Word 0 is 11:11:10 position inside the file's bounding box,
then 11:11:10 up and side axes, a half-float size pair, tint. Model records `(flag, k, a, n, lods)` give n instances per model, in order.
Each model lists 1 or 2 LOD ids, as its header entry says. `models_proc_*` files use 96-byte full transforms, with a 32-byte trailer after each model's block.

**`*.pvsz`** zones: `u32 n`, n ids (bit 31 is load or unload, low bits a draw id), `u32 k`, k u16 mesh numbers, a typed 16-byte chain (type 0 outhouses, 2 smashables, 3 armco, 9 static models),
gameplay nodes, then roughly 90-byte placement records (3 u16, position, 3×3 half-float rotation) in list order. Zones are deltas, and between them they cover every object.
Only entries with draw flag 0x800 have records. World-space meshes sit at the origin.

**Live records** (`memscan.py`): 64-byte big-endian matrix with the draw id at +132, in 224-byte records. Exact positions and ids, for what was loaded while driving.

**Terrain**: `h_blnd*` shaders blend slots 0, 1 and 5 using mask slot 2, read with UV set 2 as `(s + 1) / 2`.
BeamNG equivalent: `activeLayers` 3, layers 1 and 2 use `opacityMap` with `opacityMapUseUV: 1`.

## Status

Working: real textures, terrain blending, roads with dirt and asphalt, plants through BeamNG's forest system, buildings, the outpost.

Known gaps:
- Mesh naming from zones is 70% unaided and 98.6% with a vote share of 0.7 or more, so `zonevote.pkl` is only safe at high share. An exact record walker should fix this, and it unlocks the whole map without driving.
- No free-roam switch found. The rule so far: skip `festival`, `multiplayer`, `street` etc. sets, skip objects exactly on a `GameObjs` position, then the `KEEP` and `NOTKEEP` name lists in `build3.py`.
- No normal maps, lightmaps, water or `.fiz` collision. Semi wheels are wrong. `Grass_Ungrouped` sets aren't placed.

Ruled out, so don't repeat: F4 tracing on Canary Nov 2025 writes nothing. `freeroam` string is car setups. `LayoutId` query is controllers. `RacesSpecial.CollObjLayoutId` has only 9 layouts.
Zone bit 31 is load/unload, not a layer. Record field +144 is per-model. `game_free_roam_flow.xml` names no objects, and route 170 is just the base entry.

Recomp notes (ReImagined build): generated C++ is in `build/forza-recomp/generated_tu4/default`. `tu/default.xex` is uncompressed, so image = file from the PE offset, base 0x82000000.
Useful functions: track load `sub_82E28DE0`, `UpdatePVS` `sub_82E2AC38`, game data load `sub_82662520`.
