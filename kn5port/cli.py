"""kn5port command line.

    kn5port inspect  <AC track folder>
    kn5port convert  <AC track folder> [--preset ac|beamng] [--mods <BeamNG mods folder>] [--terrain auto|off|SIZE] ...
    kn5port validate <level folder>

Everything runs locally on files you already have. Levels converted from community tracks (mods) are
for personal use: never share them.
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

NOTICE = ("This is a community track: converting it is for personal use. Check its license (some forbid conversions), "
          "and never share the converted level.")

# Assetto Corsa's own tracks (folder names Kunos ships); everything else is a mod and gets the notice
KUNOS_TRACKS = {"drift", "imola", "magione", "monza", "mugello", "spa", "trento-bondone", "vallelunga"}


def is_kunos(track):
    name = Path(track).name.lower()
    return name.startswith("ks_") or name in KUNOS_TRACKS


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
                if m.hidden:
                    totals["hidden (physics-only)" if phys_key(m.name, surfaces) else "hidden"] += 1
                elif hor:
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


def material_table(track, top=40):
    """Per (kn5, material) surface statistics, largest area first: what to look at when a surface renders
    blurry, stretched or wrong. Returns a list of dicts (also printed by `inspect --materials`)."""
    import numpy as np
    from collections import defaultdict
    rows = defaultdict(lambda: {"meshes": 0, "hidden": 0, "tris": 0, "area": 0.0, "uv_area": 0.0})
    for f in sorted(Path(track).glob("*.kn5")):
        try:
            k = KN5(str(f))
        except KN5Error:
            continue
        with k:
            for m in k.meshes():
                if m.lod_in > 0 or not len(m.indices):
                    continue
                t = m.indices.reshape(-1, 3)
                p = m.positions.astype(np.float64)
                uv = m.uvs.astype(np.float64) * m.material.uv_mult
                r = rows[(f.name, m.material.name)]
                r.update(shader=m.material.shader, samplers=dict(m.material.samplers),
                         mults={n: round(v[0], 4) for n, v in m.material.props.items() if "mult" in n.lower()})
                r["meshes"] += 1; r["hidden"] += int(m.hidden); r["tris"] += len(t)
                r["area"] += float(np.linalg.norm(np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]]), axis=1).sum() / 2)
                e1, e2 = uv[t[:, 1]] - uv[t[:, 0]], uv[t[:, 2]] - uv[t[:, 0]]
                r["uv_area"] += float(np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]).sum() / 2)
    out = []
    for (kn5, mat), r in rows.items():
        r = dict(r, kn5=kn5, material=mat)
        r["m_per_uv"] = round((r["area"] / r["uv_area"]) ** 0.5, 2) if r["uv_area"] > 0 else None
        out.append(r)
    out.sort(key=lambda r: -r["area"])
    return out[:top]


def cmd_inspect(a):
    if a.materials:
        for r in material_table(a.track, a.materials):
            print(f"{r['area'] / 1e4:9.2f} ha  {r['kn5']:<28} {r['material']:<32} {r['shader']:<28} "
                  f"meshes {r['meshes']} (hidden {r['hidden']})  tris {r['tris']}  m/uv {r['m_per_uv']}")
            print(f"{'':13}mults {r['mults']}  samplers {r['samplers']}")
        return
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
    # presets set the ground and trees together; an explicit --terrain still wins
    if a.preset == "ac":
        a.terrain = a.terrain or "off"
        a.keep_ac_trees = True
    a.terrain = a.terrain or "auto"
    if not is_kunos(track):
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
    if a.show_hidden:
        args += ["--show-hidden"]
    if a.layer_lift is not None:
        args += ["--layer-lift", str(a.layer_lift)]
    if a.no_ground_layers:
        args += ["--no-ground-layers"]
    if a.no_lods:
        args += ["--no-lods"]
    if a.lod_scale is not None:
        args += ["--lod-scale", str(a.lod_scale)]
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
        if a.no_edge_fill:
            args += ["--no-edge-fill"]
        if a.show_hidden:
            args += ["--show-hidden"]
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
    p.add_argument("--materials", type=int, nargs="?", const=40, default=0, metavar="N",
                   help="instead: list the N largest materials by surface area (shader, tiling, textures; default 40)")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("convert", help="convert a track into a BeamNG level (unpacked mod)")
    p.add_argument("track", help="AC track folder (content/tracks/<name>)")
    p.add_argument("--mods", help="BeamNG mods folder (default: auto-detected)")
    p.add_argument("--out", help="mod folder to write (default: <mods>/unpacked/<level>)")
    p.add_argument("--level", help="level name (default: track folder name)")
    p.add_argument("--beamng", help="BeamNG.drive install folder (default: auto-detected via Steam)")
    p.add_argument("--profile", help="track profile JSON (see kn5port/profile.py)")
    p.add_argument("--preset", choices=("ac", "beamng"), help="ac: keep AC's ground and trees as built (best for "
                   "mods and ports with detailed terrain and props); beamng (default): BeamNG terrain, grass and trees "
                   "(best for simpler, grassy tracks)")
    p.add_argument("--terrain", default=None, help="auto (default), off, or a size such as 4096")
    p.add_argument("--square", type=float, default=0, help="metres per terrain sample (default: auto)")
    p.add_argument("--ao", type=float, default=None, help="terrain ambient-occlusion strength (default 0.35)")
    p.add_argument("--near-gain", type=float, default=0, help="brighten near-road ground (default: auto)")
    p.add_argument("--no-forest", action="store_true", help="don't replace AC trees with BeamNG forest items")
    p.add_argument("--tree-density", type=float, help="0-1: thin trees and shrubs for performance (default 1)")
    p.add_argument("--palette", help="stock BeamNG level whose trees, terrain materials and grass to use "
                                     "(e.g. west_coast_usa, east_coast_usa, italy, utah; default west_coast_usa)")
    p.add_argument("--keep-ac-trees", action="store_true", help="keep AC's tree meshes as they are")
    p.add_argument("--no-edge-fill", action="store_true", help="don't raise the terrain to the road edges or hang "
                   "curtains off them (turn off if they look wrong on a track)")
    p.add_argument("--show-hidden", action="store_true", help="also draw meshes AC hides, e.g. physics-only road "
                   "copies (off by default: on game-rip tracks they z-fight/flicker with the visual road)")
    p.add_argument("--no-ground-layers", action="store_true", help="keep AC multilayer ground as one blended, "
                   "low-res texture instead of splitting it into sharp per-layer tiling textures")
    p.add_argument("--layer-lift", type=float, default=None, metavar="M", help="raise road layers lying exactly on "
                   "an earlier-drawn surface (patches, lines, decals) by M metres per layer so they don't z-fight "
                   "(default 0.02; 0 = off)")
    p.add_argument("--no-lods", action="store_true", help="keep only AC's highest LOD of each mesh and draw it at "
                   "every distance, instead of swapping in AC's lower LODs and culling where AC does")
    p.add_argument("--lod-scale", type=float, default=None, metavar="X", help="multiply AC's LOD switch and cull "
                   "distances by X (default 1)")
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
