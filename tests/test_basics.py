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
