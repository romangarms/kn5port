"""The private Python's wheel unpacking (kn5port.runtime), on a synthetic wheel. No downloads."""
import tempfile
import zipfile
from pathlib import Path

from kn5port import runtime


def test_unpack_wheel_places_platlib_and_skips_scripts():
    with tempfile.TemporaryDirectory() as tmp:
        whl = Path(tmp) / "demo-1.0-cp312-cp312-win_amd64.whl"
        with zipfile.ZipFile(whl, "w") as z:
            z.writestr("demo/__init__.py", "X = 1\n")
            z.writestr("demo-1.0.dist-info/METADATA", "Name: demo\n")
            z.writestr("demo-1.0.data/platlib/demo_ext.pyd", b"\0")
            z.writestr("demo-1.0.data/scripts/demo-tool", "#!python\n")
        site = Path(tmp) / "site"
        runtime._unpack_wheel(whl, site)
        assert (site / "demo" / "__init__.py").read_text() == "X = 1\n"
        assert (site / "demo-1.0.dist-info" / "METADATA").exists()
        assert (site / "demo_ext.pyd").exists()
        assert not any("scripts" in str(p) or p.name == "demo-tool" for p in site.rglob("*"))


def test_pins_cover_every_dependency():
    names = {u.rsplit("/", 1)[1].split("-")[0] for u, _, _ in runtime.WHEELS}
    assert names == {"numpy", "scipy", "numba", "llvmlite", "pillow"}
    assert all(len(sha) == 64 for _, sha, _ in [runtime.PY] + runtime.WHEELS)
    assert "import site" in runtime.PTH
