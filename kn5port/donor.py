"""Vegetation / terrain palettes from stock BeamNG levels.

Any level in the BeamNG install can be the donor: its forest items are classified by name (conifer, broadleaf,
palm, shrub, grass), measured (heights from their Collada files) and weighted by how often the level itself
uses them; its terrain materials are assigned to roles (forest floor, grass, dry grass, dirt, gravel, rock, sand)
and its GroundCover comes along. Results are cached per level.
"""
import json
import math
import os
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np

PALETTE_VERSION = 3
ROLES = ["forest", "grass", "dry_grass", "dirt", "gravel", "rock", "sand"]
ROLE_PATTERNS = {
    "forest": [r"^forest_floor$", r"^forest$", r"forest"],
    "grass": [r"^grass$", r"^grass\d$", r"grass(?!.*dirt)"],
    "dry_grass": [r"^dirt_grass$", r"dry.?grass", r"dirt_grass"],
    "dirt": [r"^dirt$", r"^dirt_loose$", r"^dirt_dusty$", r"rockydirt", r"^dirt", r"mud"],
    "gravel": [r"gravel", r"pebble", r"dirt_rocky"],
    "rock": [r"rock_cliff", r"^rock$", r"rock"],
    "sand": [r"beachsand", r"^sand$", r"sand"],
}
ROLE_FALLBACK = {"forest": "grass", "grass": "dry_grass", "dry_grass": "grass", "dirt": "dry_grass",
                 "gravel": "dirt", "rock": "gravel", "sand": "dirt"}
NOT_GROUND = re.compile(r"asphalt|concrete|groundmodel|road|tarmac|test|paint", re.I)

NOT_VEGETATION = re.compile(r"rock|boulder|stone|guard|post|tire|bld|build|pole|fence|sign|barrier|cone|pebble|"
                            r"crate|lamp|light|bench|bin_|debris|ruin|cliff|pipe|wire|rail|barrel|box|car_|"
                            r"_group|filler|blocker|_wall_?|log_pile|dead|chopped|fallen|distant|branch|stump|"
                            r"_log\b|lod\d|impostor|imposter", re.I)
BUCKETS = [("palm", r"palm"),
           ("grass", r"grass|fern|clover|weed|flower|reed|vine_ground|ivy|groundcover|moss"),
           ("shrub", r"bush|shrub|scrub|scraggly|vine|hedge|cactus|yucca|agave|sage|plant"),
           ("conifer", r"pine|fir|spruce|douglas|cedar|cypress|juniper|conifer|redwood|sequoia|larch"),
           ("broad", r"oak|beech|birch|maple|olive|eucal|tree|willow|poplar|elm|ash_|acacia|baobab")]
# which buckets serve which kn5port species (first non-empty wins)
SERVES = {"redwood": ["conifer", "broad"], "fir": ["conifer", "broad"], "oak": ["broad", "conifer"],
          "madrone": ["broad", "conifer"], "palm": ["palm", "broad"], "shrub": ["shrub", "broad"],
          "grass": ["grass", "shrub"]}

NS = {"c": "http://www.collada.org/2005/11/COLLADASchema"}


def cache_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "kn5port" / "palettes"


def levels_dir(install):
    p = Path(install)
    if (p / "content" / "levels").exists():
        return p / "content" / "levels"
    return p if p.name == "levels" else p / "levels"


def list_levels(install):
    """Stock level zips in the install, name only (fast)."""
    d = levels_dir(install)
    return sorted(z.stem for z in d.glob("*.zip")) if d.exists() else []


def _json(z, name):
    try:
        return json.loads(z.read(name))
    except (KeyError, ValueError):
        return {}


def bucket_of(name, shape=""):
    s = f"{name} {posixpath.basename(shape)}"
    if NOT_VEGETATION.search(s) and not re.search(r"bush|tree|shrub|grass|fern|palm", name, re.I):
        return None
    if re.search(r"_group|filler|blocker|dead|chopped|fallen|distant|branch|stump|impost", name, re.I):
        return None
    for b, pat in BUCKETS:
        if re.search(pat, s, re.I):
            return b
    return None


def dae_height(z, path):
    """Height (m) of a Collada shape inside the zip, from its vertex data."""
    rel = path.lstrip("/")
    names = {n.lower(): n for n in z.namelist()} if not hasattr(z, "_lower") else z._lower
    z._lower = names
    real = names.get(rel.lower())
    if real is None:
        return None
    try:
        root = ET.fromstring(z.read(real))
    except ET.ParseError:
        return None
    up = root.find(".//c:up_axis", NS)
    unit = root.find(".//c:unit", NS)
    meter = float(unit.get("meter")) if unit is not None and unit.get("meter") else 1.0
    ax = 2 if up is not None and up.text and up.text.strip().upper() == "Z_UP" else 1
    lo, hi = math.inf, -math.inf
    for g in root.iterfind(".//c:geometry", NS):
        fa = g.find(".//c:mesh/c:source/c:float_array", NS)
        if fa is None or not fa.text:
            continue
        a = np.array(fa.text.split(), dtype=np.float64)
        if len(a) % 3:
            continue
        a = a.reshape(-1, 3)[:, ax] * meter
        lo, hi = min(lo, a.min()), max(hi, a.max())
    h = hi - lo
    return float(h) if math.isfinite(h) and 0.05 < h < 150 else None


def analyze(install, level):
    """Build (or load from cache) the palette of one stock level."""
    zp = levels_dir(install) / f"{level}.zip"
    if not zp.exists():
        raise SystemExit(f"no such BeamNG level: {level}")
    cache = cache_dir() / f"{level}-{int(zp.stat().st_mtime)}-v{PALETTE_VERSION}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except ValueError:
            pass
    z = zipfile.ZipFile(zp)
    names = z.namelist()
    root = f"levels/{level}/"

    # forest items + how often the level uses each
    items = {}
    for n in names:
        if n.endswith("managedItemData.json"):
            items.update(_json(z, n))
    use = Counter()
    for n in names:
        if n.endswith(".forest4.json"):
            for line in z.read(n).decode(errors="replace").splitlines():
                m = re.search(r'"type"\s*:\s*"([^"]+)"', line)
                if m:
                    use[m.group(1)] += 1
    veg = {}
    for name, d in items.items():
        if not isinstance(d, dict) or "shapeFile" not in d:
            continue
        b = bucket_of(name, d["shapeFile"])
        if not b:
            continue
        h = dae_height(z, d["shapeFile"])
        if not h:
            continue
        dd = dict(d); dd.pop("persistentId", None)
        sf = dd["shapeFile"]
        dd["shapeFile"] = sf if sf.startswith("/") else "/" + sf
        veg[name] = {"bucket": b, "height": round(h, 2), "weight": math.sqrt(use.get(name, 0) + 1), "def": dd}

    # terrain materials by role
    tmats = {}
    for n in names:
        if n.endswith(".json") and "materials" in n:
            for k, v in _json(z, n).items():
                if isinstance(v, dict) and v.get("class") == "TerrainMaterial" and v.get("internalName"):
                    tmats.setdefault(v["internalName"], v)
    ground = [k for k in tmats if not NOT_GROUND.search(k)]
    terrain = {}
    for role in ROLES:
        for pat in ROLE_PATTERNS[role]:
            hit = next((k for k in ground if re.search(pat, k, re.I) and k not in terrain.values()), None) or \
                  next((k for k in ground if re.search(pat, k, re.I)), None)
            if hit:
                terrain[role] = hit
                break
    for _ in range(3):
        for role in ROLES:
            if role not in terrain and ROLE_FALLBACK[role] in terrain:
                terrain[role] = terrain[ROLE_FALLBACK[role]]
    if ground:
        for role in ROLES:
            terrain.setdefault(role, ground[0])

    # preview image
    prev = next((n for n in names if n.startswith(root) and n.count("/") == 2 and
                 re.search(r"preview[^/]*\.(jpg|png)$", n, re.I)), None) or \
        next((n for n in names if re.search(r"preview[^/]*\.(jpg|png)$", n, re.I)), None)

    buckets = Counter(v["bucket"] for v in veg.values())
    pal = {"level": level, "version": PALETTE_VERSION, "vegetation": veg, "buckets": dict(buckets),
           "terrain": terrain, "terrain_defs": {k: tmats[k] for k in set(terrain.values())},
           "preview": prev}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(pal))
    return pal


def choose(pal, species, height, rng):
    """Pick a donor item for an AC tree of `species` and `height` m: weighted by use, favouring similar heights."""
    if species == "grass":
        # only genuinely low items; otherwise leave grass to the palette's GroundCover
        cands = [(n, v) for n, v in pal["vegetation"].items() if v["bucket"] in ("grass", "shrub") and v["height"] <= 1.5]
        if not cands:
            return None
    else:
        order = SERVES.get(species, ["broad"]) + ["broad", "conifer", "palm", "shrub"]
        for b in order:
            cands = [(n, v) for n, v in pal["vegetation"].items() if v["bucket"] == b]
            if cands:
                break
        else:
            return None
    sc = np.array([v["weight"] * math.exp(-(math.log(max(height, 0.2) / v["height"]) ** 2) / (2 * 0.35 ** 2))
                   for _, v in cands])
    if sc.sum() <= 0:
        sc = np.ones(len(cands))
    i = rng.choice(len(cands), p=sc / sc.sum())
    return cands[i][0], cands[i][1]["height"]


def summary(pal):
    """Short description for UIs."""
    b = pal.get("buckets", {})
    parts = [f"{b[k]} {lbl}" for k, lbl in (("conifer", "conifers"), ("broad", "broadleaf"), ("palm", "palms"),
                                            ("shrub", "shrubs"), ("grass", "grass/ferns")) if b.get(k)]
    return ", ".join(parts) or "no vegetation"


def preview_bytes(install, level):
    zp = levels_dir(install) / f"{level}.zip"
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        root = f"levels/{level}/"
        prev = next((n for n in names if n.startswith(root) and n.count("/") == 2 and
                     re.search(r"preview[^/]*\.(jpg|png)$", n, re.I)), None) or \
            next((n for n in names if re.search(r"preview[^/]*\.(jpg|png)$", n, re.I)), None)
        return (z.read(prev), prev) if prev else (None, None)
