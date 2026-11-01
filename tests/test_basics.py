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
