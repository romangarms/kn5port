"""The Xbox 360 image reader (kn5port.xbox) on a synthetic XDVDFS image, as an ISO and as a GOD package."""
import struct
import tempfile
from pathlib import Path

from kn5port import xbox

SECTOR = xbox.SECTOR


def _entry(name, sector, size, is_dir, right=0):
    n = name.encode()
    e = struct.pack("<HHIIBB", 0, right, sector, size, 0x10 if is_dir else 0x20, len(n)) + n
    return e + b"\xff" * (-len(e) % 4)


def make_image(files_at=40):
    """root: a.bin, media/ (b.txt). Returns the partition bytes and the file contents."""
    a = bytes(range(256)) * 40                       # spans several sectors
    b = b"hello colorado"
    sa, sb, sroot, smedia = files_at, files_at + 6, 34, 35
    media = _entry("b.txt", sb, len(b), False)
    second = _entry("media", smedia, SECTOR, True)
    first = _entry("a.bin", sa, len(a), False, right=0)
    first = _entry("a.bin", sa, len(a), False, right=len(first) // 4)   # root tree: a.bin, then media to its right
    root = first + second
    img = bytearray(SECTOR * (sb + 2))
    vd = xbox.MAGIC + struct.pack("<II", sroot, SECTOR)
    img[32 * SECTOR:32 * SECTOR + len(vd)] = vd
    img[32 * SECTOR + 0x7EC:32 * SECTOR + 0x7EC + 20] = xbox.MAGIC
    for sec, data in ((sroot, root), (smedia, media)):
        img[sec * SECTOR:sec * SECTOR + SECTOR] = data.ljust(SECTOR, b"\xff")
    img[sa * SECTOR:sa * SECTOR + len(a)] = a
    img[sb * SECTOR:sb * SECTOR + len(b)] = b
    return bytes(img), {"a.bin": a, "media/b.txt": b}


def write_god(root, part, skip=0):
    """a GOD package of part[skip:] with the real block layout (hash blocks filled with junk)"""
    pk = Path(root) / "4D5309C9" / "00007000"
    data_dir = pk / "0123456789ABCDEF.data"
    data_dir.mkdir(parents=True)
    (pk / "0123456789ABCDEF").write_bytes(b"LIVE" + bytes(0x1000))
    data = part[skip:]
    data += bytes(-len(data) % xbox.BLOCK)
    out = bytearray(b"\xaa" * xbox.BLOCK)
    for j in range(len(data) // xbox.BLOCK):
        if j % xbox.DATA_PER_GROUP == 0:
            out += b"\xbb" * xbox.BLOCK
        out += data[j * xbox.BLOCK:(j + 1) * xbox.BLOCK]
    (data_dir / "Data0000").write_bytes(out)
    return Path(root) / "4D5309C9"


def _check(g, files, tmp):
    listed = {p: (s, n) for p, s, n in g.files()}
    assert sorted(listed) == sorted(files)
    n = g.extract(Path(tmp) / "out", lambda p: p.startswith("media/"))
    assert n == 1
    assert (Path(tmp) / "out" / "media" / "b.txt").read_bytes() == files["media/b.txt"]
    assert not (Path(tmp) / "out" / "a.bin").exists()
    s, size = listed["a.bin"]
    assert g.read_file(s, size) == files["a.bin"]


def test_iso():
    part, files = make_image()
    with tempfile.TemporaryDirectory() as tmp:
        iso = Path(tmp) / "game.iso"
        iso.write_bytes(part)
        assert xbox.detect(iso, look_inside=False)["kind"] == "iso"
        assert xbox.detect(iso)["kind"] is None          # an Xbox game, but without Forza Horizon's map
        assert "not Forza Horizon" in xbox.detect(iso)["why"]
        g = xbox.open_game(iso)
        try:
            _check(g, files, tmp)
        finally:
            g.close()


def test_god_and_trimmed_god():
    part, files = make_image()
    for skip in (0, 0x3000):
        with tempfile.TemporaryDirectory() as tmp:
            title = write_god(tmp, part, skip)
            for p in (Path(tmp), title, title / "00007000", title / "00007000" / "0123456789ABCDEF.data"):
                assert xbox.detect(p, look_inside=False)["kind"] == "god", p
            g = xbox.open_game(title)
            try:
                assert g.base == -skip
                _check(g, files, tmp)
            finally:
                g.close()


def test_god_offsets_cross_groups_and_files():
    assert xbox.god_file_offset(0) == (0, 2 * xbox.BLOCK)
    assert xbox.god_file_offset(xbox.DATA_PER_GROUP * xbox.BLOCK) == (0, (2 + 205) * xbox.BLOCK)
    assert xbox.god_file_offset(xbox.DATA_PER_FILE * xbox.BLOCK + 5) == (1, 2 * xbox.BLOCK + 5)


def test_detect_folder():
    with tempfile.TemporaryDirectory() as tmp:
        assert xbox.detect(tmp)["kind"] is None
        (Path(tmp) / "media" / "tracks" / "colorado").mkdir(parents=True)
        (Path(tmp) / "media" / "tracks" / "colorado" / "bin.zip").write_bytes(b"PK")
        assert xbox.detect(tmp)["kind"] == "folder"
