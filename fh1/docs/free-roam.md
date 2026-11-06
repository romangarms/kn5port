# Choosing what appears in free roam

FH1 ships event scenery (grandstands, cones, food trucks) in the same zones as the world. The level must show free roam only.

## Static objects: the rule

Implemented in `build3.py`, using `zonerec.pkl` from `zones.py`. Keep a record unless it has a list or an owner name.

| Record | Examples | Kept |
|---|---|---|
| No owner block | Buildings, telegraph poles, outpost tent | Yes |
| Block, empty list, no owner | Signs, fences, bins, snow poles | Yes |
| Block with list | Food trucks, RVs, cones, tyre stacks, grandstands | No, except `event_kept`: flower holders, and barriers that are off the road |
| Block with owner name | `FR22_03`, `flyer_066`, `BF_..._OPEN`, speed camera markers | No, except `TeamTrailer`: each event owns one starter trailer at its marker |

Extra filters: names matching `NOUSE`, `PLANT_RE` (plants come from `.pgeo`), and `S_`, `C_` or `Debri` prefixes (smashable pieces), plus records near the origin.

**Status:** tested in-game around Red Rock only, and the user reported everything looking good there. Not tested on the rest of the map.

**Likely exceptions:** floodlights, speakers, some flags and marquees pass the rule while looking event-like. They might be permanent. Barnfind barns have owner names (`_OPEN`, `_CLOSED`) and are dropped, so a default barn state may need adding.

## List bytes: race routes (decoded 2026-11-06)

Each list byte is a `Tracks.RouteId` in `gamedb.slt`, the `<n>` of `TrackRoute<n>.xml`. A record shows in every route its list names, so `86d2` is routes 134 and 210 and `9193ced3` is 145, 147, 206 and 211, layouts that share a stretch of road. Byte 0 is route 0, the track `Colorado Free Roam` (event `FREE_ROAM`).

Evidence:
- 83 distinct byte values. All 47 route ids used by festival, showcase, plane and headline events appear as bytes, plus route 12 (`STREET_PLNS_005`, 5 props). Street and Nemesis routes (60 to 66, 171 to 201) have none, as those races run on open roads.
- Scoring each byte's props against all 242 routes by median distance to the route's points and waypoints: in 46 of 81, the byte's own route is the nearest. Nearly all the others come second or third, behind a route on the same roads (204 or 210 beside 136, 134, 205 and 209). Examples: 0x6e (110, Main Stage Run) 71% of props within 30 m of route 110, the next route 25%; 0x8f (143, Red Rock Dirt Sprint) 82% against 34%; 0x03 (Eagle Ridge Mini Circuit) 96%.
- The bytes that aren't event routes are named in `Tracks`: 21 to 31 are `ColoradoFreeRoam` variants (track ids 1101 to 1111, 152 props, all listed together as `00151617181a1c1d1e1f`), 170 is `E3Route`, and 212 to 224 are named areas (`MediaCentre`, `KettleHills`, `RedRock`, `Beaumont` and so on). 15 and 203 have no route file. 0x6e was the tyre-wall puzzle below: those 67 walls belong to route 110's layout.

Owner names are separate from this. 3,436 owner-named records match an event id by prefix (89 events) and make each event's start pen: about 39 records, 30 `OBJ_FEST_BarrierMetal`, 4 flags and the `TeamTrailer`, a median 7 to 11 m from the event's `GameObjs.xml` node. 3,520 of them list only route 0, so FH1 shows them in free roam. Today only the trailer is kept (`STARTER`). Showing the whole pen in free roam would be a decision for the user.

**Use:** `build3.py` collects records whose list names an event route (`RACE_ROUTES`, read from `gamedb.slt`) and skips owner-named and free-roam-kept ones. The same name filters as free roam apply, and placements are deduplicated by model and position, keeping the lowest LOD. Each route's props go to `art/fh1_races/route_<n>.prefab.json` as TSStatics. Models no free-roam placement uses get their `m<no>.dae` shape (`prop_shape`). Cones, flags, chevron boards, barrels, signs, banners and countdown boards don't collide; everything else uses its shape's simple collision. `races.py` copies the prefab into each mission folder as `mainPrefab.prefab.json`. The stock `timeTrial` flowgraph (`ttFg.flow.json`, `scene/spawnPrefab` with path `mainPrefab`) spawns it when the race starts, and the flowgraph prefab module deletes it when the race ends. Colorado has 17.6k race-layout placements over 47 routes, the largest 694 (route 109). Nothing is resident in free roam. The shapes aren't cooked by `cookship.py` until a race has loaded them once, so the first start of a race may hitch.

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

## Barriers

Road fences and barriers are permanent scenery in `Models_Ungrouped` files: `MetalFarmFence`, `BrickMetalFence`, `STFT_Chain_Linked_Fence`, `OBJ_BarrierBrand`, `OBJ_FEST_BarrierMetal`. See full-transform instances in `formats.md`. They aren't event props.

Zone record owners are real event ids. `FR05` is `Events.HorizonEventID` in `media/db/gamedb.slt`, and `FESTIVAL_FIRST_001` is `Tracks.DevName`. A record with an owner is never kept. `models_proc_clrd_*_barriers_*` sets stay out, and `model_item` refuses `BarrierConcrete`: the user reports it never appears in the game and blocks real roads.

Hoardings and `TWALL` tyre walls stay out. They once came back when `0x6e` was in their list, a value seen on them at the festival, but 0x6e also marks 67 tyre walls on race routes 370 m to 2.6 km away, most of them within 11 m of a road's centre line. The list bytes turned out to be route ids (above): 0x6e is route 110, Main Stage Run, so those walls now come with that race's prefab.
