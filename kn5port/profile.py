"""Track profiles: every heuristic that decides how an AC track maps onto a BeamNG level lives here.

Defaults are written to work on any reasonably built AC track. A track can override any of them with a JSON
profile (``kn5port convert --profile my_track.json``, or ``profiles/<track folder>.json`` in this repo) whose
keys are the field names below; regular expressions are given as strings.
"""
import json
import re
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path

# AC surface key keywords (surfaces.ini KEY / mesh-name prefix such as 1ROAD, 1GRASS) -> BeamNG ground model
SURFACE_KEYWORDS = [
    ("KERB", "ASPHALT"), ("ROAD", "ASPHALT"), ("ASPH", "ASPHALT"), ("TARMAC", "ASPHALT"), ("PIT", "ASPHALT"),
    ("CONCRETE", "ASPHALT"), ("CEMENT", "ASPHALT"), ("BRIDGE", "ASPHALT"),
    ("GRAVEL", "GRAVEL"), ("SAND", "SAND"), ("MUD", "MUD"), ("DIRT", "DIRT"), ("SNOW", "SNOW"), ("ICE", "ICE"),
    ("GRASS", "GRASS"), ("WALL", "ROCK"), ("ROCK", "ROCK"), ("WOOD", "WOOD"), ("METAL", "METAL"),
]


@dataclass
class Profile:
    name: str = "default"

    # --- surfaces -----------------------------------------------------------------------------------------
    ground_types: dict = field(default_factory=dict)      # explicit AC surface key -> BeamNG ground model
    default_ground_type: str = "ASPHALT"
    # surfaces (BeamNG ground models) that count as natural ground: replaced by the terrain in terrain mode
    natural_ground_types: tuple = ("GRASS", "SAND")

    # --- ground meshes -----------------------------------------------------------------------------------------
    # visual-only meshes that are terrain (get GRASS collision in mesh mode, become terrain in terrain mode)
    ground_name_re: str = r"FAR|TERRAIN|GROUND|HILL|MOUNTAIN|LANDSCAPE|VALLEY"
    # kn5 files / meshes that are a distant backdrop (lowest-priority terrain source, never converted as meshes)
    horizon_re: str = r"HORIZON|BACKDROP|SKYBOX|PANORAMA"

    # --- vegetation --------------------------------------------------------------------------------------------
    vegetation_re: str = (r"TREE|BARK|TRUNK|FOLIAGE|LEAF|LEAVES|BUSH|SHRUB|HEDGE|GROUNDCOVER|PLANT|VEG|PALM|PINE|"
                          r"FIR\b|FIR_|OAK|CONIF|CYPRESS|REDWOOD|SPRUCE|CEDAR|GRASS|WEED|FERN|FLOWER|REED")
    trunk_re: str = r"BARK|TRUNK|STEM"
    species_re: dict = field(default_factory=lambda: {
        "grass": r"GRASS|GROUNDCOVER|WEED|FERN|FLOWER|REED|SHADE",
        "shrub": r"BUSH|SHRUB|HEDGE|CLUSTER|SCRUB|PLANT",
        "palm": r"PALM",
        "redwood": r"REDWOOD|SEQUOIA",
        "fir": r"PINE|FIR\b|FIR_|CONIF|SPRUCE|CEDAR|CYPRESS|EVERGREEN|LARCH",
        "madrone": r"MADRONE|EUCAL",
        "oak": r"OAK|BROAD|BIRCH|BEECH|MAPLE|ASH|ELM|WILLOW|POPLAR|DECID",
    })
    default_tree_species: str = "oak"

    # --- donor content (stock BeamNG level whose trees / terrain materials / grass are reused) ----------------
    donor_level: str = "west_coast_usa"
    # species -> list of [item, min_height, max_height]
    tree_items: dict = field(default_factory=lambda: {
        "redwood": [["redwood_tall", 0, 99], ["redwood_broad", 0, 99], ["redwood_split", 0, 99]],
        "fir": [["pine_radiata_small", 0, 11], ["pine_radiata_forest", 11, 20], ["pine_radiata_large", 11, 20],
                ["pine_radiata_weathered", 11, 20], ["redwood_tall", 20, 99], ["redwood_split", 20, 99]],
        "oak": [["oak_dry_a", 0, 99], ["oak_dry_b", 0, 99], ["oak_dry_c", 0, 99], ["oak_dry_d", 0, 99]],
        "madrone": [["oak_dry_b", 0, 99], ["oak_dry_c", 0, 99]],
        "palm": [["oak_dry_a", 0, 99]],
        "shrub": [["shrub_a", 0, 99], ["shrub_c", 0, 99], ["shrub_e", 0, 99], ["shrub_g", 0, 99]],
        "grass": [["grassclump_large", 0, 99]],
    })
    # reference heights (m) of the donor items, used to scale them to the AC tree heights
    item_heights: dict = field(default_factory=lambda: {
        "redwood_tall": 38.5, "redwood_broad": 38.5, "redwood_split": 38.1,
        "pine_radiata_large": 13.2, "pine_radiata_forest": 13.3, "pine_radiata_weathered": 13.2,
        "pine_radiata_small": 8.6,
        "oak_dry_a": 9.6, "oak_dry_b": 8.2, "oak_dry_c": 8.4, "oak_dry_d": 7.1,
        "shrub_a": 3.6, "shrub_c": 4.0, "shrub_e": 3.2, "shrub_g": 5.0,
        "grassclump_large": 1.2,
    })
    scale_clamp: dict = field(default_factory=lambda: {
        "redwood": [0.4, 1.6], "fir": [0.6, 1.8], "oak": [0.5, 2.0], "madrone": [0.5, 2.0], "palm": [0.5, 2.0],
        "shrub": [0.3, 1.5], "grass": [0.5, 1.5]})
    clearance: dict = field(default_factory=lambda: {"grass": 0.5, "shrub": 3.0, "tree": 1.5})
    tree_density: float = 1.0           # < 1 thins trees/shrubs uniformly (performance); grass uses grass_density
    grass_density: float = 1.0
    # AC multilayer mask channels R, G, B, A -> terrain roles (see donor.ROLES)
    mask_roles: tuple = ("forest", "grass", "dry_grass", "dirt")

    # --- terrain -----------------------------------------------------------------------------------------------
    terrain_max_size: int = 4096        # samples per side (stock levels go up to 4096)
    terrain_min_square: float = 1.0     # finest metres per sample
    terrain_margin: float = 1500.0      # metres of terrain beyond the road's bounding box
    carve_depth: float = 0.2
    terrain_ao: float = 0.35
    fade_far: float = 600.0             # metres: near-ground offsets fade out over this into distant terrain
    fade_horizon: float = 2000.0

    # ------------------------------------------------------------------------------------------------------------
    def ground_type(self, key, surface_props=None):
        key = key.upper()
        if key in self.ground_types:
            return self.ground_types[key]
        for kw, gt in SURFACE_KEYWORDS:
            if kw in key:
                return gt
        if surface_props and str(surface_props.get("IS_VALID_TRACK", "1")).strip() == "0":
            return "GRASS"
        return self.default_ground_type

    def rx(self, name):
        return re.compile(getattr(self, name), re.I)

    def species_of(self, matname):
        for sp, pat in self.species_re.items():
            if re.search(pat, matname, re.I):
                return sp
        return self.default_tree_species

    @classmethod
    def load(cls, path=None, track_dir=None):
        p = cls()
        candidates = []
        if path:
            candidates.append(Path(path))
        elif track_dir:
            here = Path(__file__).resolve().parent.parent / "profiles"
            candidates += [Path(track_dir) / "kn5port.json", here / f"{Path(track_dir).name}.json"]
        for c in candidates:
            if c.exists():
                data = json.loads(c.read_text())
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k.startswith("_"):
                        continue
                    if k not in known:
                        raise SystemExit(f"{c}: unknown profile key '{k}'")
                    cur = getattr(p, k)
                    setattr(p, k, {**cur, **v} if isinstance(cur, dict) and isinstance(v, dict) else v)
                p.name = data.get("name", c.stem)
                print(f"profile: {c}")
                break
        return p

    def dump(self):
        return asdict(self)


# AC's own system/data/surfaces.ini (used when the install's copy can't be read)
AC_DEFAULT_SURFACES = {
    "ROAD": {"KEY": "ROAD", "IS_VALID_TRACK": "1"}, "GRASS": {"KEY": "GRASS", "IS_VALID_TRACK": "0"},
    "KERB": {"KEY": "KERB", "IS_VALID_TRACK": "1"}, "SAND": {"KEY": "SAND", "IS_VALID_TRACK": "0"},
}


def read_surfaces(track_dir):
    """Physics surface definitions available to the track: AC's built-in ones (system/data/surfaces.ini of the
    install the track lives in), then the track's own surfaces.ini files. KEY -> properties."""
    out = dict(AC_DEFAULT_SURFACES)
    track_dir = Path(track_dir)
    files = []
    if len(track_dir.parents) >= 3:
        files.append(track_dir.parents[2] / "system" / "data" / "surfaces.ini")      # assettocorsa/system
    files += sorted(track_dir.rglob("surfaces.ini"))
    for ini in files:
        if not ini.exists():
            continue
        cur = None
        for line in ini.read_text(errors="replace").splitlines():
            line = line.split(";")[0].strip()
            if line.startswith("["):
                cur = {}
                continue
            if "=" in line and cur is not None:
                k, v = [s.strip() for s in line.split("=", 1)]
                cur[k.upper()] = v
                if k.upper() == "KEY":
                    out[v.upper()] = cur
    return out


def mostly_flat(mesh, threshold=0.8):
    """Area-weighted mean 'upness' of a mesh's triangles: True for ground the terrain can replace,
    False for steep faces (rock cuts, embankment walls) that must stay meshes."""
    import numpy as np
    p = mesh.positions.astype(np.float64)
    t = mesh.indices.reshape(-1, 3)
    if not len(t):
        return False
    n = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
    area = np.linalg.norm(n, axis=1)
    if area.sum() <= 0:
        return False
    up = np.abs(n[:, 1]) / np.maximum(area, 1e-12)          # AC is Y-up
    return float((up * area).sum() / area.sum()) >= threshold


PHYS_NAME = re.compile(r"^(\d+)([A-Za-z]+)")


def phys_key(name, surfaces):
    """AC physics surface key of a mesh name, or None for a visual mesh.

    AC treats every mesh named <digits><KEY>... as a physics object. KEY is matched against the defined surfaces
    (longest match, with or without the digits, since some tracks define keys like '1PIT'); a key that isn't
    defined anywhere still collides in AC, as a wall, so it is returned as-is."""
    m = PHYS_NAME.match(name)
    if not m:
        return None
    up = name.upper()
    rest = up[len(m.group(1)):]
    best = None
    for k in surfaces:
        ku = k.upper()
        if (rest.startswith(ku) or up.startswith(ku)) and (best is None or len(k) > len(best)):
            best = k
    return best or m.group(2).upper()
