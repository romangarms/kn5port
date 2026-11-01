"""kn5port command line.

    kn5port inspect  <AC track folder>
    kn5port convert  <AC track folder> [--mods <BeamNG mods folder>] [--terrain auto|off|SIZE] ...
    kn5port validate <level folder>

Everything runs locally on files you already have. Converted levels are for personal use: never share them.
"""
import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

from . import __version__, beamng
from .kn5 import KN5, KN5Error
from .profile import Profile, read_surfaces, mostly_flat, phys_key

NOTICE = ("kn5port converts content you own for personal use. Check the track's license (some forbid conversions), "
          "and never share converted levels.")


def inspect_track(track, profile=None, beamng_path=None, mods_path=None):
    """What kn5port detects in a track (no conversion). Returns a dict (also used by the UI)."""
    track = Path(track)
    prof = Profile.load(profile, track)
    surfaces = read_surfaces(track)
    ui = sorted(p.parent.name for p in (track / "ui").glob("*/ui_track.json"))
    phys = re.compile(r"^(\d+)([A-Z]+)")
    veg_re, trunk_re, ground_re, hor_re = (prof.rx(n) for n in ("vegetation_re", "trunk_re", "ground_name_re",
                                                                 "horizon_re"))
    totals = Counter(); species = Counter(); protected = []; files = 0
    for f in sorted(track.glob("*.kn5")):
        try:
            k = KN5(str(f))
        except KN5Error:
            protected.append(f.name); continue
        files += 1
        with k:
            hor = bool(hor_re.search(f.stem))
            for m in k.meshes():
                if m.lod_in > 0:
                    continue
                mt = m.material
                key = phys_key(m.name, surfaces)
                if hor:
                    totals["horizon"] += 1
                elif key:
                    gt = prof.ground_type(key, surfaces.get(key))
                    nat = gt in prof.natural_ground_types and (mt.shader.startswith("ksMultilayer") or mostly_flat(m))
                    totals["natural ground" if nat else f"physics {gt.lower()}"] += 1
                elif mt.shader.startswith("ksMultilayer"):
                    totals["natural ground"] += 1
                elif trunk_re.search(mt.name) or ((veg_re.search(mt.name) or mt.shader == "ksTree") and
                                                  (mt.shader == "ksTree" or mt.alpha_tested or mt.alpha_blend)):
                    totals["vegetation"] += 1; species[prof.species_of(mt.name)] += 1
                elif ground_re.search(m.name):
                    totals["distant ground"] += 1
                else:
                    totals["other visual"] += 1
    return {
        "track": track.name,
        "profile": prof.name,
        "surfaces": {k: prof.ground_type(k, v) for k, v in surfaces.items()},
        "layouts": ui or (["(single)"] if (track / "ui" / "ui_track.json").exists() else []),
        "kn5_files": files,
        "meshes": dict(totals.most_common()),
        "vegetation": dict(species.most_common()),
        "protected": protected,
        "terrain": bool(totals["natural ground"] or totals["distant ground"]),
        "beamng": str(beamng.find_install(beamng_path) or ""),
        "mods": str(beamng.find_mods(mods_path) or ""),
    }


def cmd_inspect(a):
    r = inspect_track(a.track, a.profile, a.beamng, a.mods)
    print(f"track: {r['track']}  (profile: {r['profile']})")
    print(f"surfaces.ini keys: {', '.join(f'{k}->{v}' for k, v in r['surfaces'].items()) or 'none'}")
    print(f"layouts: {', '.join(r['layouts']) or 'none'}")
    print("meshes: " + ", ".join(f"{k} {v}" for k, v in r["meshes"].items()))
    if r["vegetation"]:
        print("vegetation species: " + ", ".join(f"{k} {v}" for k, v in r["vegetation"].items()))
    if r["protected"]:
        print(f"PROTECTED / unreadable (will be skipped): {', '.join(r['protected'])}")
    print(f"terrain conversion: {'yes' if r['terrain'] else 'no natural ground found'}")
    print(f"BeamNG install: {r['beamng'] or 'not found (pass --beamng)'}; mods folder: {r['mods'] or 'not found'}")


def cmd_convert(a):
    from . import level, terrain, forest, validate
    track = Path(a.track)
    if not track.exists():
        sys.exit(f"no such folder: {track}")
    print(NOTICE)
    name = re.sub(r"[^A-Za-z0-9_]", "_", a.level or track.name).lower()
    if a.out:
        mod_root = Path(a.out)
    else:
        mods = beamng.find_mods(a.mods)
        if mods is None:
            sys.exit("couldn't find your BeamNG mods folder; pass --mods or --out")
        mod_root = mods / "unpacked" / name
    inst = beamng.find_install(a.beamng)
    if inst is None and (a.terrain != "off" or not a.no_forest):
        sys.exit("couldn't find BeamNG.drive (needed for its trees and terrain materials); pass --beamng, "
                 "or use --terrain off --no-forest")
    level_dir = mod_root / "levels" / name
    common = ["--profile", a.profile] if a.profile else []
    t0 = time.time()
    print(f"\n== 1/4 meshes, materials, spawns -> {mod_root}")
    args = [str(track), str(mod_root), "--level", name] + common
    if a.terrain != "off":
        args += ["--terrain", a.terrain]
        if a.square:
            args += ["--terrain-square", str(a.square)]
    if a.keep_ac_trees:
        args += ["--keep-ac-trees"]
    level.main(args)
    if a.terrain != "off":
        print("\n== 2/4 terrain")
        args = [str(track), str(level_dir), "--beamng", str(inst)] + common
        if a.ao is not None:
            args += ["--ao", str(a.ao)]
        if a.near_gain:
            args += ["--near-gain", str(a.near_gain)]
        if a.palette:
            args += ["--donor", a.palette]
        terrain.main(args)
    if not a.no_forest and not a.keep_ac_trees:
        print("\n== 3/4 trees and grass")
        fargs = [str(track), str(level_dir), "--beamng", str(inst)] + common
        if a.tree_density is not None:
            fargs += ["--tree-density", str(a.tree_density)]
        if a.palette:
            fargs += ["--donor", a.palette]
        forest.main(fargs)
    print("\n== 4/4 validate")
    try:
        validate.main([str(level_dir), "--ac-track", str(track), "--drive-check"])
    except SystemExit as e:
        if e.code:
            print("validation found problems (see above)")
    print(f"\ndone in {time.time() - t0:.0f}s -> {level_dir}\nRestart BeamNG fully before loading the level.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kn5port", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"kn5port {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("inspect", help="show what kn5port detects in a track, without converting")
    p.add_argument("track"); p.add_argument("--profile"); p.add_argument("--beamng"); p.add_argument("--mods")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("convert", help="convert a track into a BeamNG level (unpacked mod)")
    p.add_argument("track", help="AC track folder (content/tracks/<name>)")
    p.add_argument("--mods", help="BeamNG mods folder (default: auto-detected)")
    p.add_argument("--out", help="mod folder to write (default: <mods>/unpacked/<level>)")
    p.add_argument("--level", help="level name (default: track folder name)")
    p.add_argument("--beamng", help="BeamNG.drive install folder (default: auto-detected via Steam)")
    p.add_argument("--profile", help="track profile JSON (see kn5port/profile.py)")
    p.add_argument("--terrain", default="auto", help="auto (default), off, or a size such as 4096")
    p.add_argument("--square", type=float, default=0, help="metres per terrain sample (default: auto)")
    p.add_argument("--ao", type=float, default=None, help="terrain ambient-occlusion strength (default 0.35)")
    p.add_argument("--near-gain", type=float, default=0, help="brighten near-road ground (default: auto)")
    p.add_argument("--no-forest", action="store_true", help="don't replace AC trees with BeamNG forest items")
    p.add_argument("--tree-density", type=float, help="0-1: thin trees and shrubs for performance (default 1)")
    p.add_argument("--palette", help="stock BeamNG level whose trees, terrain materials and grass to use "
                                     "(e.g. west_coast_usa, east_coast_usa, italy, utah; default west_coast_usa)")
    p.add_argument("--keep-ac-trees", action="store_true", help="keep AC's tree meshes as they are")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("validate", help="check a converted level for broken references")
    p.add_argument("level_dir"); p.add_argument("--ac-track", help="the AC track folder: also checks drivability")
    p.set_defaults(func=lambda a: __import__("kn5port.validate", fromlist=["main"]).main(
        [a.level_dir] + (["--ac-track", a.ac_track, "--drive-check"] if a.ac_track else [])))

    p = sub.add_parser("ui", help="open the kn5port web UI in your browser (runs locally)")
    p.add_argument("--port", type=int, default=0, help="port (default: any free port)")
    p.add_argument("--no-browser", action="store_true", help="just print the URL")
    p.set_defaults(func=lambda a: __import__("kn5port.ui.server", fromlist=["serve"]).serve(
        a.port, not a.no_browser))

    a = ap.parse_args(argv)
    a.func(a)


def gui_main():
    """Entry point for the 'kn5port-ui' launcher."""
    main(["ui"])


if __name__ == "__main__":
    main()
