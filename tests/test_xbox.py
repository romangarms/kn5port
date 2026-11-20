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


# ---- STFS ---------------------------------------------------------------------------------------------------

def write_stfs(path, files, per=(4, 16, 64), title="4D5309C9"):
    """a read-only STFS package laid out block by block, independently of the reader's offset formulas: a level 0
    hash table before each group of per[0] data blocks, the level 1 table after the first group and before each
    later run of per[1] blocks, the level 2 table after the first per[1]. files: {path: bytes}; the first is written
    interleaved with the second, so its block chain isn't contiguous."""
    import hashlib
    bs = xbox.BLOCK
    blocks = [None]          # data block 0: the file table
    entries, dirs = [], {}
    starts = {}
    for name in files:
        parent = 0xFFFF
        parts = name.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d not in dirs:
                dirs[d] = len(entries)
                entries.append((parts[i - 1], True, dirs.get("/".join(parts[:i - 1]), 0xFFFF), 0, 0))
            parent = dirs[d]
        entries.append((parts[-1], False, parent, name, len(files[name])))
    # data blocks: alternate the first two files' blocks, then the rest in order
    chunks = {n: [files[n][i:i + bs] for i in range(0, len(files[n]), bs)] for n in files}
    order, names = [], list(files)
    second = names[1] if len(names) > 1 else None
    a, b = chunks[names[0]], chunks[second] if second else []
    for i in range(max(len(a), len(b))):
        order += [(names[0], i)] * (i < len(a)) + [(second, i)] * (i < len(b))
    for n in names[2:]:
        order += [(n, i) for i in range(len(chunks[n]))]
    nxt, prev = {}, {}
    for k, (n, i) in enumerate(order, start=1):
        blocks.append(chunks[n][i].ljust(bs, b"\0"))
        if i == 0:
            starts[n] = k
        else:
            nxt[prev[n]] = k
        prev[n] = k
    table = bytearray(bs)
    for j, (nm, is_dir, parent, full, size) in enumerate(entries):
        e = bytearray(64)
        e[:len(nm)] = nm.encode()
        e[0x28] = len(nm) | (0x80 if is_dir else 0)
        st = starts.get(full, 0) if not is_dir else 0
        nb = -(-size // bs)
        e[0x29:0x2C] = nb.to_bytes(3, "little"); e[0x2C:0x2F] = nb.to_bytes(3, "little")
        e[0x2F:0x32] = st.to_bytes(3, "little")
        struct.pack_into(">HI", e, 0x32, parent, size)
        table[j * 64:j * 64 + 64] = e
    blocks[0] = bytes(table)
    nxt[0] = 0xFFFFFF
    # lay out: tables interleaved with the data blocks
    phys, l0 = [], {}
    for i, data in enumerate(blocks):
        if i % per[0] == 0:
            if i == per[0]:
                phys.append(b"L1")
            if i == per[1]:
                phys.append(b"L2")
            if i >= per[1] and i % per[1] == 0:
                phys.append(b"L1")
            l0[i // per[0]] = len(phys)
            phys.append(None)
        phys.append(data)
    for g, at in l0.items():
        t = bytearray(bs)
        for k in range(per[0]):
            i = g * per[0] + k
            if i < len(blocks):
                t[k * 24:k * 24 + 20] = hashlib.sha1(blocks[i]).digest()
                struct.pack_into(">I", t, k * 24 + 20, 0x80000000 | nxt.get(i, 0xFFFFFF))
        phys[at] = bytes(t)
    hdr = bytearray(0xA000)
    hdr[:4] = b"LIVE"
    struct.pack_into(">I", hdr, 0x340, 0x971A)
    hdr[0x360:0x364] = bytes.fromhex(title)
    hdr[0x411:0x411 + 18] = "Test pack".encode("utf-16-be")
    vd = bytearray(0x24); vd[0] = 0x24; vd[2] = 1        # read-only: one copy of each hash table
    struct.pack_into("<H", vd, 3, 1); vd[5:8] = (0).to_bytes(3, "little")
    struct.pack_into(">I", vd, 0x1C, len(blocks))
    hdr[0x379:0x379 + 0x24] = vd
    with open(path, "wb") as f:
        f.write(hdr)
        for p in phys:
            f.write(p if len(p) == bs else p.ljust(bs, b"\0"))


def test_stfs_chains_across_hash_levels(monkeypatch):
    monkeypatch.setattr(xbox, "HASH_PER", (4, 16, 64))       # small tables, so a few blocks cross all three levels
    bs = xbox.BLOCK
    files = {"Media/DLCZips/a.zip": bytes(range(256)) * (bs * 20 // 256) + b"tail",
             "Media/b.bin": b"\x07" * (bs * 30 + 5), "spa.bin": b"spa"}
    with tempfile.TemporaryDirectory() as tmp:
        pkg = Path(tmp) / "PKG"
        write_stfs(pkg, files)
        info = xbox.stfs_info(pkg)
        assert info["magic"] == "LIVE" and info["title_id"] == "4D5309C9" and info["name"] == "Test pack"
        s = xbox.Stfs(pkg)
        assert sorted(p for p, _, _ in s.files()) == sorted(files)
        assert s.extract(Path(tmp) / "out", lambda p: p.startswith("Media/")) == 2
        for n in ("Media/DLCZips/a.zip", "Media/b.bin"):
            assert (Path(tmp) / "out" / n).read_bytes() == files[n]
        assert not (Path(tmp) / "out" / "spa.bin").exists()
        s.close()
        # a damaged block is caught by its hash
        raw = bytearray(pkg.read_bytes())
        off = raw.index(b"\x07" * bs)
        raw[off + 100] ^= 0xFF
        pkg.write_bytes(bytes(raw))
        s = xbox.Stfs(pkg)
        try:
            s.extract(Path(tmp) / "bad", lambda p: p == "Media/b.bin")
            assert False, "damage not caught"
        except xbox.XboxError as e:
            assert "hash" in str(e)
        finally:
            s.close()
        assert not (Path(tmp) / "bad" / "Media" / "b.bin").exists()


def test_stfs_info_rejects_other_files():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "x"
        p.write_bytes(b"\0" * 0x1000)
        assert xbox.stfs_info(p) is None


def test_app_finds_and_copies_the_rally_package():
    from kn5port.ui import fh1
    files = {"Media/DLCZips/ColoradoDirt_pri_65.zip": b"z" * 9000, "Media/DLCZips/1600_pri_65.zip": b"p" * 5000,
             "Media/DLCZips/1600_pri_65/Media/audio/a.xma": b"a" * 100, "spa.bin": b"s"}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        drive = tmp / "usb" / "Content" / "0000000000000000" / "4D5309C9" / "00000002"
        drive.mkdir(parents=True)
        pkg = drive / "6F6992766050D818245ADD408031E280FB5F4E634D"
        write_stfs(pkg, files, per=xbox.HASH_PER)
        with open(pkg, "ab") as f:          # big enough to be looked at as a package
            f.write(b"\0" * 0x10000)
        other = drive / "CARPACK"
        write_stfs(other, {"Media/DLCZips/cars.zip": b"c"}, per=xbox.HASH_PER)
        for where in (pkg, drive, tmp / "usb"):
            d = fh1.detect_dlc(where)
            assert d["package"] == str(pkg) and d["path"] is None, (where, d)
        assert "not the Rally Expansion" in fh1.detect_dlc(other)["why"]
        notfh = tmp / "NOTFH"
        write_stfs(notfh, {"x": b"x"}, per=xbox.HASH_PER, title="41560845")
        assert "not Forza Horizon's" in fh1.detect_dlc(notfh)["why"]

        app = fh1.FH1({"fh1_dlc": str(pkg), "fh1_work": str(tmp / "work")}, lambda s: None)
        assert app.dlc()["copied"] is False and app.dlc_ok()
        media, n = app.copy_dlc(pkg)
        assert n == 4 and Path(media) == tmp / "work" / "dlc" / "Media"
        assert (Path(media) / "DLCZips" / "ColoradoDirt_pri_65.zip").read_bytes() == files["Media/DLCZips/ColoradoDirt_pri_65.zip"]
        d = app.dlc()
        assert d["copied"] and Path(d["path"]) == Path(media)
