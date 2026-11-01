# Reverse-engineering tools

## Paths

- Dump, **read-only**: `D:\Roms\Xbox 360\Xenia\FH1XeniaFix`. Copy anything you need to edit.
- Recomp: `D:\Forza Horizon 1 Recomp\Forza Horizon 1 ReImagined (TU4)`. Launcher: `C:\Users\roman\AppData\Local\Programs\ForzaHorizon1-ReImagined`.
- Generated C++: `build/forza-recomp/generated_tu4/default`.

## Live capture: `memscan.py`

`python memscan.py <xenia pid> <passes>` reads Xenia's memory without writing, while the user drives. It needs the game running, so it needs the user. Writes `memobj_all.pkl` and `memrec.pkl`.

Never run Xenia and BeamNG together: Xenia reported graphics device lost and crashed when that happened.

With `zones.py`, this is now only needed to check results.

## Recomp strings and code: `x1.py`, `x2.py`

- `tu/default.xex` is uncompressed, so image = file from the PE offset, base 0x82000000.
- `x1.py` builds the string cross-reference index.
- `python x2.py sub_XXXX` prints a generated function as PowerPC with strings and calls annotated.
- Useful functions: track load `sub_82E28DE0`, `UpdatePVS` `sub_82E2AC38`, game data load `sub_82662520`.

## GPU traces: `xtr.py`

Reads Xenia `.xtr` files. But F4 tracing on Canary Nov 2025 writes nothing, and the trace menu option didn't work either, so don't plan around getting new traces.

## Rendering checks

`pv.py` renders mesh groups in software, and `v2.py` uses it to compare UV formulas without launching BeamNG.

## Approach that worked

1. Check any format theory against `memrec.pkl` live truth, not plausibility. Several theories looked right until tested.
2. Walk records exactly to the end of the file. If a parser lands on EOF across all files, it's right.
3. Survey every file before rebuilding. Red Rock only exercised some layouts, and the whole map hit 3 more.
4. Make the build count unreadable files rather than crash, and report the counts.
