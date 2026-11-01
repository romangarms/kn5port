"""Self-contained tests: a synthetic kn5 is written on the fly (no game content needed)."""
import struct
import tempfile
from pathlib import Path

import numpy as np

from kn5port.kn5 import KN5, KN5Error
from kn5port.profile import Profile


def _s(t):
    b = t.encode()
    return struct.pack("<i", len(b)) + b


def make_kn5(path, version=6):
    out = b"sc6969" + struct.pack("<i", version) + (struct.pack("<i", 0) if version > 5 else b"")
    out += struct.pack("<i", 1) + struct.pack("<i", 1) + _s("tex.dds") + struct.pack("<i", 4) + b"DDS "
    out += struct.pack("<i", 1) + _s("ROAD") + _s("ksPerPixel") + bytes([0, 0]) + struct.pack("<i", 0)
    out += struct.pack("<i", 1) + _s("ksDiffuse") + struct.pack("<10f", 0.4, *[0] * 9)
    out += struct.pack("<i", 1) + _s("txDiffuse") + struct.pack("<i", 0) + _s("tex.dds")
    # root dummy with one mesh child: a 10 m quad
    out += struct.pack("<i", 1) + _s("root") + struct.pack("<i", 1) + bytes([1])
    out += struct.pack("<16f", *np.identity(4).ravel())
    out += struct.pack("<i", 2) + _s("1ROAD_test") + struct.pack("<i", 0) + bytes([1])
    out += bytes([1, 1, 0]) + struct.pack("<i", 4)
    for x, z in ((0, 0), (10, 0), (10, 10), (0, 10)):
        out += struct.pack("<11f", x, 0, z, 0, 1, 0, x / 10, z / 10, 1, 0, 0)
    out += struct.pack("<i", 6) + struct.pack("<6H", 0, 2, 1, 0, 3, 2)
    out += struct.pack("<ii", 0, 0) + struct.pack("<2f", 0, 0) + struct.pack("<4f", 5, 0, 5, 8) + bytes([1])
    Path(path).write_bytes(out)


def test_reads_synthetic_kn5():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.kn5"
        make_kn5(p)
        with KN5(str(p)) as k:
            assert k.version == 6 and len(k.textures) == 1 and k.materials[0].name == "ROAD"
            meshes = list(k.meshes())
            assert len(meshes) == 1 and meshes[0].name == "1ROAD_test"
            assert meshes[0].positions.shape == (4, 3) and len(meshes[0].indices) == 6


def test_refuses_garbage():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.kn5"
        p.write_bytes(b"sc6969" + struct.pack("<ii", 6, 0) + b"\xff" * 64)
        try:
            KN5(str(p))
        except KN5Error as e:
            assert "protected" in str(e)
        else:
            raise AssertionError("garbage kn5 was accepted")


def test_profile_defaults():
    p = Profile()
    assert p.ground_type("ROAD") == "ASPHALT" and p.ground_type("GRASS") == "GRASS"
    assert p.ground_type("XYZ", {"IS_VALID_TRACK": "0"}) == "GRASS"
    assert p.species_of("H9_TREES_CONIFER") == "fir" and p.species_of("SKY_GROUNDCOVER") == "grass"
    assert p.species_of("PALM_TREE_01") == "palm" and p.species_of("SOME_TREE") == p.default_tree_species


def test_steam_library_detection(monkeypatch=None):
    """AC on a second library (another drive) is found through libraryfolders.vdf + its app manifest."""
    from kn5port import beamng
    with tempfile.TemporaryDirectory() as d:
        root, lib2 = Path(d) / "Steam", Path(d) / "OtherDrive" / "SteamLibrary"
        for p in (root / "steamapps" / "common" / "assettocorsa" / "content",       # stale folder, no manifest
                  lib2 / "steamapps" / "common" / "assettocorsa" / "content" / "tracks",
                  lib2 / "steamapps" / "common" / "BeamNG.drive" / "content" / "levels"):
            p.mkdir(parents=True)
        (root / "steamapps" / "libraryfolders.vdf").write_text(
            '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }' % (root, lib2))
        (lib2 / "steamapps" / f"appmanifest_{beamng.AC_APPID}.acf").write_text("x")
        (lib2 / "steamapps" / f"appmanifest_{beamng.BEAMNG_APPID}.acf").write_text("x")
        orig = beamng._steam_roots
        beamng._steam_roots = lambda: [root]
        try:
            assert beamng.find_ac_tracks() == lib2 / "steamapps" / "common" / "assettocorsa" / "content" / "tracks"
            assert beamng.find_install() == lib2 / "steamapps" / "common" / "BeamNG.drive"
        finally:
            beamng._steam_roots = orig


def test_scanner_flags_protected_tracks():
    import os
    with tempfile.TemporaryDirectory() as d:
        os.environ["APPDATA"] = str(Path(d) / "appdata")
        from kn5port.ui.server import Scanner
        tracks = Path(d) / "tracks"
        good, bad, mixed = tracks / "good", tracks / "locked", tracks / "mixed"
        for t in (good, bad, mixed):
            t.mkdir(parents=True)
        make_kn5(good / "good.kn5")
        (bad / "locked.kn5").write_bytes(b"sc6969" + struct.pack("<ii", 6, 0) + b"\xff" * 64)
        make_kn5(mixed / "a.kn5")
        (mixed / "b.kn5").write_bytes(b"not a kn5 at all")
        s = Scanner()
        s._run(tracks)
        assert s.status["good"]["state"] == "ok"
        assert s.status["locked"]["state"] == "protected"
        assert s.status["mixed"]["state"] == "partial"


def test_verify_catches_scrambled_payload():
    """A file whose structure is intact but whose data is scrambled (as a protection scheme would) is caught."""
    import os
    with tempfile.TemporaryDirectory() as d:
        good = Path(d) / "good.kn5"
        make_kn5(good)
        with KN5(str(good)) as k:
            assert k.verify() is None
        raw = bytearray(good.read_bytes())
        # scramble the texture bytes (they no longer start with an image signature)
        i = raw.index(b"DDS ")
        raw[i:i + 4] = os.urandom(4)
        tex_bad = Path(d) / "tex.kn5"
        tex_bad.write_bytes(bytes(raw))
        with KN5(str(tex_bad)) as k:
            assert k.verify() and "textures" in k.verify()
        # scramble the vertex block of the mesh (positions/normals become noise), structure untouched
        raw = bytearray(good.read_bytes())
        j = raw.index(b"1ROAD_test") + len(b"1ROAD_test") + 4 + 1 + 3 + 4
        raw[j:j + 4 * 44] = os.urandom(4 * 44)
        mesh_bad = Path(d) / "mesh.kn5"
        mesh_bad.write_bytes(bytes(raw))
        with KN5(str(mesh_bad)) as k:
            assert k.verify() is not None


def test_sample_tris_numpy2():
    """Regression: 2D np.cross was removed in NumPy 2.x."""
    from kn5port.forest import sample_tris
    T = np.array([[[0, 0, 0], [4, 0, 0], [0, 4, 0]]], dtype=np.float64)
    pts = sample_tris(T, 1.0)
    assert pts.shape[1] == 2 and len(pts) > 10


def test_physics_surface_keys():
    """AC's built-in surfaces count even when a track only defines extras; unknown keys still collide."""
    from kn5port.profile import read_surfaces, phys_key
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "assettocorsa" / "content" / "tracks" / "x"
        (t / "layout" / "data").mkdir(parents=True)
        (t / "layout" / "data" / "surfaces.ini").write_text("[SURFACE_0]\nKEY=OFF\nIS_VALID_TRACK=0\n"
                                                            "[SURFACE_1]\nKEY=1PIT\nIS_VALID_TRACK=1\n")
        s = read_surfaces(t)
        assert {"ROAD", "GRASS", "KERB", "SAND", "OFF", "1PIT"} <= set(s)
        assert phys_key("1GRASS_hill_03", s) == "GRASS"
        assert phys_key("1OFF_verge", s) == "OFF"
        assert phys_key("1PIT_lane", s) == "1PIT"
        assert phys_key("1WALL_barrier", s) == "WALL"          # undefined: a wall in AC
        assert phys_key("tree_card_01", s) is None


def test_terrain_colour_roles_lean_green():
    from kn5port.terrain import classify_colour
    RI = {"forest": 0, "grass": 1, "dry_grass": 2, "dirt": 3, "gravel": 4, "rock": 5, "sand": 6}
    c = np.array([[94, 104, 62], [98, 80, 62], [160, 142, 102], [128, 128, 124], [235, 235, 233]], np.float32) / 255
    # grass, forest floor (dark brown), dry grass (warm tan), grey -> grass, bright bare -> rock
    assert classify_colour(c, RI).tolist() == [1, 0, 2, 1, 5]


def test_texture_sizes_round_to_power_of_two():
    from kn5port.level import TextureBaker
    assert [TextureBaker.pow2(v) for v in (4, 255, 1536, 2048, 6000)] == [16, 256, 2048, 2048, 4096]


def test_track_list_sorted_by_display_name():
    import json
    from kn5port.ui.server import scan_tracks
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for folder, name in [("ks_zandvoort", "Zandvoort"), ("aaa_monza", "monza"), ("rt_brands", "Brands Hatch")]:
            d = root / folder
            (d / "ui").mkdir(parents=True)
            (d / "track.kn5").write_bytes(b"")
            (d / "ui" / "ui_track.json").write_text(json.dumps({"name": name}))
        names = [t["name"] for t in scan_tracks({"tracks": str(root)})]
        assert names == ["Brands Hatch", "monza", "Zandvoort"]



def _grid(x0, x1, y0, y1, z=0.0, n=4):
    xs, ys = np.linspace(x0, x1, n + 1), np.linspace(y0, y1, n + 1)
    X, Y = np.meshgrid(xs, ys)
    pos = np.stack([X.ravel(), Y.ravel(), np.full(X.size, z)], 1).astype(np.float32)
    idx = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            idx += [a, a + 1, a + n + 2, a, a + n + 2, a + n + 1]
    return pos, np.array(idx, np.uint32)


def test_overlay_layers_lifted_in_draw_order():
    from kn5port.overlay import OverlayLifter
    lf = OverlayLifter(step=0.02)
    road_a = _grid(0, 10, 0, 10); road_b = _grid(10, 20, 0, 10)       # segments sharing a seam
    shoulder = _grid(-3, 0, 0, 10, z=0.001)                              # adjacent, not overlapping
    patch = _grid(2, 8, 2, 8, z=0.0, n=3)                                # on road_a
    decal = _grid(3, 7, 3, 7, z=-0.005, n=2)                             # translucent, on top of the patch
    for (p, i), tr in ((road_a, 0), (decal, 1), (road_b, 0), (shoulder, 0), (patch, 0)):
        lf.add(p, i, tr)
    lf.resolve()
    assert np.all(road_a[0][:, 2] == 0) and np.all(road_b[0][:, 2] == 0)   # seams don't chain
    assert np.allclose(shoulder[0][:, 2], 0.001)
    assert np.allclose(patch[0][:, 2], 0.02)                                  # level 1
    assert np.allclose(decal[0][:, 2], -0.005 + 0.04 + 0.005, atol=1e-5)      # level 2, drawn after opaque
    # a later kn5 that lays a line on road_b sees the earlier kn5's surfaces
    line = _grid(12, 18, 4, 5, n=2)
    lf.add(*line); lf.resolve()
    assert np.allclose(line[0][:, 2], 0.02)
    assert lf.stats["max_level"] == 2


def test_overlay_off():
    from kn5port.overlay import OverlayLifter
    lf = OverlayLifter(step=0)
    road, patch = _grid(0, 10, 0, 10), _grid(2, 8, 2, 8)
    lf.add(*road); lf.add(*patch); lf.resolve()
    assert np.all(patch[0][:, 2] == 0)


def test_overlay_stacks_across_kn5s():
    from kn5port.overlay import OverlayLifter
    lf = OverlayLifter(step=0.02)
    road, patch = _grid(0, 10, 0, 10), _grid(2, 8, 2, 8)
    lf.add(*road); lf.add(*patch); lf.resolve()
    decal = _grid(3, 7, 3, 7, n=2)          # next kn5: on the patch (level 1) -> level 2
    lf.add(*decal); lf.resolve()
    assert np.allclose(patch[0][:, 2], 0.02) and np.allclose(decal[0][:, 2], 0.04)


def test_overlay_identical_copy_and_mixed_sizes():
    from kn5port.overlay import OverlayLifter
    lf = OverlayLifter(step=0.02)
    lot = _grid(0, 200, 0, 200, n=2)                 # coarse parking lot (100 m triangles)
    road = _grid(0, 10, 0, 10, z=5.0, n=20)          # dense road elsewhere, different height
    lines = _grid(0, 10, 0, 10, z=5.0, n=20)         # line mesh sharing the road's vertices exactly
    mark = _grid(50, 52, 50, 60, n=1)                # small marking on the big lot
    for p, i in (lot, road, lines, mark):
        lf.add(p, i)
    lf.resolve()
    assert np.allclose(lines[0][:, 2], 5.02) and np.allclose(mark[0][:, 2], 0.02)
    assert np.allclose(road[0][:, 2], 5.0) and np.allclose(lot[0][:, 2], 0)


def test_detail_map_is_neutral_grey():
    from PIL import Image
    from kn5port.level import TextureBaker
    with tempfile.TemporaryDirectory() as d:
        b = TextureBaker(Path(d), "/levels/x/art/textures")
        noise = (np.random.default_rng(0).random((64, 64, 3)) * 120 + 40).astype(np.uint8)
        b._img = lambda k, name: Image.fromarray(noise).convert("RGBA")
        rel = b.detail(None, "asphalt_detail.dds")
        assert rel.endswith("asphalt_detail_detail.data.png")
        out = np.asarray(Image.open(Path(d) / Path(rel).name).convert("L"), np.float32) / 255
        assert abs(out.mean() - 0.5) < 0.02 and out.std() > 0.05
        b._img = lambda k, name: Image.new("RGBA", (32, 32), (90, 90, 90, 255))
        assert b.detail(None, "flat.dds") is None


def test_uvmult_shader_tiling():
    from kn5port.kn5 import Material
    m = Material(0, "grass", "ksPerPixelNM_UVMult", 0, False, 0, {"diffuseMult": (40.0, (), (), ())})
    assert m.uv_mult == 40.0
    assert Material(0, "road", "ksPerPixel", 0, False, 0, {"diffuseMult": (40.0, (), (), ())}).uv_mult == 1.0
    assert Material(0, "x", "ksPerPixelNM_UVMult", 0, False, 0).uv_mult == 1.0
