"""Read an Xbox 360 game from a disc image (ISO) or a Games on Demand (GOD) package, and copy files out of it.

Both hold the game's XDVDFS file system ("MICROSOFT*XBOX*MEDIA" at sector 32 of the game partition):

- An ISO is the whole disc. The game partition starts at 0 (a trimmed / XGD1 image), 0x2080000 (XGD3) or
  0xFD90000 (XGD2).
- A GOD package is ``<title id>/00007000/<name>`` (a header file) beside ``<name>.data/Data0000, Data0001…``.
  Each data file is 4 KiB blocks: one master hash block, then 203 groups of a hash block and 204 data blocks.
  Taking the data blocks in order gives the game partition (sometimes with its start trimmed), so the volume
  descriptor is found by searching rather than assumed.

Use: ``g = open_game(path)``, then ``g.files()`` and ``g.extract(dest, want)``. ``detect(path)`` says what a
path holds without opening anything large.
"""
import os
import re
import struct
from pathlib import Path

SECTOR = 0x800
MAGIC = b"MICROSOFT*XBOX*MEDIA"
ISO_PARTITIONS = (0, 0x2080000, 0xFD90000, 0x18300000)

BLOCK = 0x1000
DATA_PER_GROUP = 0xCC           # 204 data blocks after each sub hash block
GROUPS = 0xCB                   # 203 groups per data file
DATA_PER_FILE = DATA_PER_GROUP * GROUPS


class XboxError(Exception):
    pass


# ---- block sources -----------------------------------------------------------------------------------------

class _IsoSource:
    def __init__(self, path):
        self.f = open(path, "rb")
        self.size = os.path.getsize(path)

    def read(self, off, n):
        self.f.seek(off)
        return self.f.read(n)

    def close(self):
        self.f.close()


def god_file_offset(logical):
    """(data file index, byte offset inside it) for a byte of the partition stored in a GOD package."""
    b, inside = divmod(logical, BLOCK)
    fi, r = divmod(b, DATA_PER_FILE)
    group, k = divmod(r, DATA_PER_GROUP)
    return fi, (1 + group * (DATA_PER_GROUP + 1) + 1 + k) * BLOCK + inside


class _GodSource:
    def __init__(self, data_dir):
        self.parts = sorted((p for p in Path(data_dir).iterdir() if re.fullmatch(r"Data\d{4}", p.name, re.I)),
                            key=lambda p: p.name.lower())
        if not self.parts:
            raise XboxError(f"{data_dir} has no Data0000 file")
        self.files = {}
        last = self.parts[-1].stat().st_size
        self.size = (len(self.parts) - 1) * DATA_PER_FILE * BLOCK + _god_data_bytes(last)

    def _f(self, i):
        if i not in self.files:
            if i >= len(self.parts):
                raise XboxError("read past the end of the GOD data files (a Data file is missing?)")
            self.files[i] = open(self.parts[i], "rb")
        return self.files[i]

    def read(self, off, n):
        out = bytearray()
        while n > 0:
            fi, fo = god_file_offset(off)
            run = BLOCK * DATA_PER_GROUP - (off % (BLOCK * DATA_PER_GROUP))      # contiguous to the next hash block
            take = min(n, run)
            f = self._f(fi)
            f.seek(fo)
            got = f.read(take)
            out += got
            if len(got) < take:
                break
            off += take; n -= take
        return bytes(out)

    def close(self):
        for f in self.files.values():
            f.close()


def _god_data_bytes(file_size):
    """data bytes held in one GOD data file of this size"""
    blocks = max(0, file_size // BLOCK - 1)
    full, rest = divmod(blocks, DATA_PER_GROUP + 1)
    return (full * DATA_PER_GROUP + max(0, rest - 1)) * BLOCK


# ---- finding what a path holds -----------------------------------------------------------------------------

def _god_header(path):
    """(header file, data folder) for a GOD package given as any of its folders or files, else None"""
    p = Path(path)
    if p.is_file() and Path(str(p) + ".data").is_dir():
        return p, Path(str(p) + ".data")
    if p.is_dir() and p.name.lower().endswith(".data") and p.with_name(p.name[:-5]).is_file():
        return p.with_name(p.name[:-5]), p
    if p.is_dir():
        # the package folder, the title id folder or 00007000; at most three levels down
        for depth in range(4):
            for d in sorted(p.glob("/".join(["*"] * depth) + "/*.data") if depth else p.glob("*.data")):
                h = d.with_name(d.name[:-5])
                if d.is_dir() and h.is_file() and (d / "Data0000").exists():
                    return h, d
    return None


class Title:
    """what a game copy must hold to be this game: needed, paths in lower case with '/', all of them there"""
    def __init__(self, name, short, *needed):
        self.name, self.short, self.needed = name, short, needed


FH1 = Title("Forza Horizon (2012)", "Forza Horizon", "media/tracks/colorado/bin.zip")
# FM3 has a gamedb.slt too, and most of the same tracks; the Top Gear test track is FM4's (and on its first disc)
FM4 = Title("Forza Motorsport 4 (2011)", "Forza Motorsport 4", "media/db/gamedb.slt", "media/tracks/topgear/bin.zip")
NEEDED = FH1.needed[0]


def _checked(kind, path, why, title=FH1):
    """an ISO or GOD result, after looking inside it for the game's needed file"""
    try:
        g = Game(_IsoSource(path) if kind == "iso" else _GodSource(str(path) + ".data"))
    except (OSError, XboxError) as e:
        return {"kind": None, "path": str(path), "why": f"Couldn't read it: {e}"}
    try:
        have = {p.lower() for p, _, _ in g.files()}
        miss = next((n for n in title.needed if n not in have), None)
        if miss:
            return {"kind": None, "path": str(path),
                    "why": f"An Xbox 360 game, but not {title.name}: it has no {miss.rsplit('/', 1)[0]}."}
    except (OSError, XboxError, struct.error) as e:
        return {"kind": None, "path": str(path), "why": f"Couldn't read its files: {e}"}
    finally:
        g.close()
    return {"kind": kind, "path": str(path), "why": why}


def detect(path, look_inside=True, title=FH1):
    """{'kind': 'folder'|'iso'|'god'|None, 'path': the folder/file to use, 'why': text}, for the game title (FH1, FM4).
    look_inside checks that an ISO or GOD package holds that game (reads its directories, under a second)."""
    d = _detect(path, title)
    if look_inside and d["kind"] in ("iso", "god"):
        return _checked(d["kind"], d["path"], d["why"], title)
    return d


def _detect(path, title=FH1):
    p = Path(str(path or "").strip().strip('"'))
    if not str(path or "").strip():
        return {"kind": None, "path": "", "why": "Nothing picked yet."}
    if not p.exists():
        return {"kind": None, "path": str(p), "why": "That path doesn't exist."}
    if p.is_dir():
        for d in (p, p / "Content") + ((p.parent,) if p.name.lower() == "media" else ()):
            if all((d / n).exists() for n in title.needed):
                return {"kind": "folder", "path": str(d), "why": "Extracted game files."}
        if (p / "default.xex").exists() or (p / "media").is_dir():
            return {"kind": None, "path": str(p),
                    "why": f"This looks like an Xbox 360 game folder, but not {title.short}'s: "
                           f"{next(n for n in title.needed if not (p / n).exists())} is missing."}
    if p.is_file() and p.suffix.lower() in (".iso", ".xiso"):
        return {"kind": "iso", "path": str(p), "why": "A disc image (ISO)."}
    g = _god_header(p)
    if g:
        return {"kind": "god", "path": str(g[0]), "why": "A Games on Demand (GOD) package."}
    if p.is_dir() and list(p.glob("*.iso")):
        return {"kind": "iso", "path": str(sorted(p.glob("*.iso"))[0]), "why": "A disc image (ISO)."}
    return {"kind": None, "path": str(p), "why": f"No {title.short} game files, ISO or GOD package found here."}


# ---- XDVDFS ------------------------------------------------------------------------------------------------

class Game:
    def __init__(self, src):
        self.src = src
        self.base = self._find_partition()
        vd = src.read(self.base + 32 * SECTOR, SECTOR)
        self.root_sector, self.root_size = struct.unpack_from("<II", vd, 20)

    def _find_partition(self):
        if isinstance(self.src, _IsoSource):
            cands = [b + 32 * SECTOR for b in ISO_PARTITIONS]
        else:      # GOD: normally at 32 sectors, but search the first 64 MB in case the start was trimmed
            cands = [32 * SECTOR] + [o for o in range(0, min(self.src.size, 64 << 20), SECTOR) if o != 32 * SECTOR]
        for at in cands:
            if at + SECTOR > self.src.size:
                continue
            d = self.src.read(at, SECTOR)
            if d[:20] == MAGIC and d[0x7EC:0x7EC + 20] == MAGIC:
                return at - 32 * SECTOR
        raise XboxError("no Xbox file system found in this image (is it an Xbox 360 game?)")

    def _dir(self, sector, size):
        if not size:
            return
        data = self.src.read(self.base + sector * SECTOR, size)
        todo, seen = [0], set()
        while todo:
            o = todo.pop()
            if o in seen or o + 14 > len(data):
                continue
            seen.add(o)
            left, right, sec, sz, attr, nl = struct.unpack_from("<HHIIBB", data, o)
            if left == 0xFFFF and right == 0xFFFF:
                continue          # sector padding
            name = data[o + 14:o + 14 + nl].decode("latin1")
            if left:
                todo.append(left * 4)
            if right:
                todo.append(right * 4)
            yield name, sec, sz, bool(attr & 0x10)

    def files(self):
        """every file as (path with '/', sector, size)"""
        stack = [("", self.root_sector, self.root_size)]
        while stack:
            pre, sec, size = stack.pop()
            for name, s, sz, is_dir in self._dir(sec, size):
                if is_dir:
                    stack.append((pre + name + "/", s, sz))
                else:
                    yield pre + name, s, sz

    def read_file(self, sector, size):
        return self.src.read(self.base + sector * SECTOR, size)

    def extract(self, dest, want=lambda p: True, progress=None, cancelled=lambda: False):
        """copy the files want(path) accepts into dest; progress(done_bytes, total_bytes, path). -> files copied"""
        dest = Path(dest)
        todo = [(p, s, n) for p, s, n in self.files() if want(p)]
        total = sum(n for _, _, n in todo)
        done = 0
        for path, sec, size in sorted(todo, key=lambda t: t[1]):           # in disc order, for speed
            if cancelled():
                raise KeyboardInterrupt
            out = dest / path
            if out.exists() and out.stat().st_size == size:
                done += size
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            with open(tmp, "wb") as f:
                at = 0
                while at < size:
                    if cancelled():
                        f.close(); tmp.unlink()
                        raise KeyboardInterrupt
                    n = min(8 << 20, size - at)
                    chunk = self.src.read(self.base + sec * SECTOR + at, n)
                    if len(chunk) != n:
                        raise XboxError(f"{path}: the image ends early (incomplete copy?)")
                    f.write(chunk)
                    at += n; done += n
                    if progress:
                        progress(done, total, path)
            os.replace(tmp, out)
        return len(todo)

    def close(self):
        self.src.close()


# ---- STFS packages (DLC) -----------------------------------------------------------------------------------
# A content package off a console or Xbox Live: one file, "LIVE", "PIRS" or "CON " at 0. Layout as Xenia reads it
# (src/xenia/vfs/devices/stfs_container_device.cc, stfs_xbox.h, BSD licence):
# - the header, then 4 KiB blocks from round_up(header_size, 4 KiB)
# - before every 170 data blocks a level 0 hash table (170 entries of SHA-1, then a big-endian u32 whose low 24 bits
#   are the next block in the file's chain), before every 170 of those a level 1 table, then one level 2 table.
#   A read-only package has one copy of each table; others two, and a flag in the table above picks the live copy.
# - the file table: 64-byte entries, a chain of blocks starting at the volume descriptor's block number.

STFS_MAGIC = (b"LIVE", b"PIRS", b"CON ")
HASH_PER = (170, 170 * 170, 170 ** 3)
END_OF_CHAIN = 0xFFFFFF


def _u24le(b, o):
    return b[o] | b[o + 1] << 8 | b[o + 2] << 16


def stfs_info(path):
    """{'magic', 'title_id', 'name', 'content_type'} of an STFS package's header, or None if the file isn't one"""
    try:
        with open(path, "rb") as f:
            h = f.read(0x611)
    except OSError:
        return None
    if len(h) < 0x611 or h[:4] not in STFS_MAGIC:
        return None
    name = h[0x411:0x611].decode("utf-16-be", "replace").split("\0", 1)[0]
    return {"magic": h[:4].decode(), "title_id": h[0x360:0x364].hex().upper(), "name": name,
            "content_type": struct.unpack_from(">I", h, 0x344)[0]}


class Stfs:
    def __init__(self, path):
        self.path = str(path)
        self.f = open(path, "rb")
        h = self.f.read(0x971A)
        if len(h) < 0x3AD or h[:4] not in STFS_MAGIC:
            raise XboxError("not an Xbox 360 content package (no LIVE, PIRS or CON header)")
        if struct.unpack_from(">I", h, 0x3A9)[0] != 0:
            raise XboxError("an SVOD package (several data files): not supported, only single-file STFS packages")
        vd = h[0x379:0x379 + 0x24]
        flags = vd[2]
        self.read_only = bool(flags & 1)
        self.root_active = bool(flags & 2)
        self.table_count = struct.unpack_from("<H", vd, 3)[0]
        self.table_block = _u24le(vd, 5)
        self.total_blocks = struct.unpack_from(">I", vd, 0x1C)[0]
        self.data = (struct.unpack_from(">I", h, 0x340)[0] + BLOCK - 1) & ~(BLOCK - 1)
        self.per_table = 1 if self.read_only else 2
        self.step0 = HASH_PER[0] + self.per_table
        self.step1 = HASH_PER[1] + (HASH_PER[0] + 1) * self.per_table
        self.tables = {}
        self._files = None

    def _read(self, off, n):
        self.f.seek(off)
        b = self.f.read(n)
        if len(b) != n:
            raise XboxError("the package ends early (incomplete copy?)")
        return b

    def block_offset(self, block):
        base, b = HASH_PER[0], block
        for _ in range(3):
            b += (block + base) // base * self.per_table
            if block < base:
                break
            base *= HASH_PER[0]
        return self.data + (b << 12)

    def _hash_block(self, block, level):
        if level == 0:
            if block < HASH_PER[0]:
                return 0
            b = block // HASH_PER[0] * self.step0 + (block // HASH_PER[1] + 1) * self.per_table
            return b if block < HASH_PER[1] else b + self.per_table
        if level == 1:
            return self.step0 if block < HASH_PER[1] else block // HASH_PER[1] * self.step1 + self.per_table
        return self.step1

    def _table(self, off, second):
        if off not in self.tables:
            self.tables[off] = self._read(off + (BLOCK if second else 0), BLOCK)
        return self.tables[off]

    @staticmethod
    def _entry(table, i):
        return table[i * 24:i * 24 + 20], struct.unpack_from(">I", table, i * 24 + 20)[0]

    def hash_entry(self, block):
        """(sha1, info) of a data block: its hash, and the next block of its chain in info's low 24 bits"""
        lv0 = self.data + (self._hash_block(block, 0) << 12)
        if lv0 not in self.tables:
            second = self.root_active and not self.read_only
            if not self.read_only and self.total_blocks > HASH_PER[0]:
                lv1 = self.data + (self._hash_block(block, 1) << 12)
                if lv1 not in self.tables:
                    if self.total_blocks > HASH_PER[1]:
                        lv2 = self.data + (self._hash_block(block, 2) << 12)
                        t2 = self._table(lv2, second)
                        second = bool(self._entry(t2, block // HASH_PER[1] % HASH_PER[0])[1] & 0x40000000)
                    self._table(lv1, second)
                second = bool(self._entry(self.tables[lv1], block // HASH_PER[0] % HASH_PER[0])[1] & 0x40000000)
            self._table(lv0, second)
        return self._entry(self.tables[lv0], block % HASH_PER[0])

    def files(self):
        """every file as (path with '/', start block, size), read from the file table"""
        if self._files is None:
            names, out, block = [], [], self.table_block
            for _ in range(self.table_count):
                t = self._read(self.block_offset(block), BLOCK)
                for i in range(BLOCK // 64):
                    e = t[i * 64:i * 64 + 64]
                    if not e[0]:
                        break
                    name = e[:e[0x28] & 0x3F].decode("latin1")
                    parent = struct.unpack_from(">H", e, 0x32)[0]
                    full = name if parent == 0xFFFF else names[parent] + "/" + name
                    names.append(full)
                    if not e[0x28] & 0x80:
                        out.append((full, _u24le(e, 0x2F), struct.unpack_from(">I", e, 0x34)[0]))
                block = self.hash_entry(block)[1] & 0xFFFFFF
                if block == END_OF_CHAIN:
                    break
            self._files = out
        return iter(self._files)

    def read_blocks(self, start, size, cancelled=lambda: False):
        """a file's bytes in pieces, following its block chain and checking each block's SHA-1"""
        import hashlib
        block, left = start, size
        while left > 0:
            if cancelled():
                raise KeyboardInterrupt
            run, off = [], self.block_offset(block)
            while left > 0 and len(run) < 2048:         # up to 8 MB of blocks that sit next to each other
                if block == END_OF_CHAIN:
                    raise XboxError("a file's block chain ends early (damaged package?)")
                if run and self.block_offset(block) != off + len(run) * BLOCK:
                    break
                sha, info = self.hash_entry(block)
                run.append((block, sha, min(BLOCK, left)))
                left -= min(BLOCK, left)
                block = info & 0xFFFFFF
            data = self._read(off, len(run) * BLOCK - BLOCK + run[-1][2])
            for i, (b, sha, n) in enumerate(run):
                # the hash covers the whole 4 KiB block, so the last one is read in full for the check
                chunk = data[i * BLOCK:i * BLOCK + n] if n == BLOCK else self._read(off + i * BLOCK, BLOCK)
                if hashlib.sha1(chunk).digest() != sha:
                    raise XboxError(f"block {b} fails its hash check (damaged package?)")
            yield data

    def extract(self, dest, want=lambda p: True, progress=None, cancelled=lambda: False):
        """copy the files want(path) accepts into dest; progress(done_bytes, total_bytes, path). -> files copied"""
        dest = Path(dest)
        todo = [t for t in self.files() if want(t[0])]
        total, done = sum(t[2] for t in todo), 0
        for path, start, size in todo:
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            try:
                with open(tmp, "wb") as f:
                    for chunk in self.read_blocks(start, size, cancelled):
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total, path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            os.replace(tmp, out)
        return len(todo)

    def close(self):
        self.f.close()


def open_game(path):
    d = detect(path, look_inside=False)
    if d["kind"] == "iso":
        return Game(_IsoSource(d["path"]))
    if d["kind"] == "god":
        return Game(_GodSource(str(d["path"]) + ".data"))
    raise XboxError(d["why"] if d["kind"] is None else "Already extracted: nothing to read.")
