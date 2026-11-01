# Choosing what appears in free roam

FH1 ships event scenery (grandstands, cones, food trucks) in the same zones as the world. The level must show free roam only.

## Static objects: the rule

Implemented in `build3.py`, using `zonerec.pkl` from `zones.py`. Keep a record unless it has a list or an owner name.

| Record | Examples | Kept |
|---|---|---|
| No owner block | Buildings, telegraph poles, outpost tent | Yes |
| Block, empty list, no owner | Signs, fences, bins, snow poles | Yes |
| Block with list | Food trucks, RVs, cones, tyre stacks, grandstands | No |
| Block with owner name | `FR22_03`, `flyer_066`, `BF_..._OPEN`, speed camera markers | No |

Extra filters: names matching `NOUSE`, `PLANT_RE` (plants come from `.pgeo`), and `S_`, `C_` or `Debri` prefixes (smashable pieces), plus records near the origin.

**Status:** tested in-game around Red Rock only, and the user reported everything looking good there. Not tested on the rest of the map.

**Likely exceptions:** floodlights, speakers, some flags and marquees pass the rule while looking event-like. They might be permanent. Barnfind barns have owner names (`_OPEN`, `_CLOSED`) and are dropped, so a default barn state may need adding.

**Evidence for the list meaning events:** values like `8f`, `90`, `9193ced3`, `d8ed` repeat across race scenery. They look like event ids, but this is not decoded. Solving it would allow choosing any event's layout.

## Plants and model sets

`EVENT` in `build3.py` drops `.pgeo` sets by name (festival, showcase, `_fest_`, `_pr_barriers`, ghostmp and so on). This is a name filter, so it's the weak spot. `[freeroam]` tagged sets are kept.

## Ruled out, don't repeat

- Draw-table flags as a layer switch.
- Live record field +144: per-model.
- Zone bit 31: load or unload, not a layer.
- `freeroam` string in game data: car setups.
- `LayoutId` query: controllers.
- `RacesSpecial.CollObjLayoutId`: only 9 layouts.
- `game_free_roam_flow.xml`: names no objects.
- Route 170: just the base entry.

## History, so nobody rebuilds it

Name allow-lists (`KEEP`, `NOTKEEP`, `ONROAD_OK`), a 1 m gameplay-node rule, and zone voting (`zonevote.pkl`) all came first. The zone reader replaced them, and they're gone from `build3.py`.
