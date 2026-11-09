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


NEEDED = "media/tracks/colorado/bin.zip"


def _checked(kind, path, why):
    """an ISO or GOD result, after looking inside it for Forza Horizon's map"""
    try:
        g = Game(_IsoSource(path) if kind == "iso" else _GodSource(str(path) + ".data"))
    except (OSError, XboxError) as e:
        return {"kind": None, "path": str(path), "why": f"Couldn't read it: {e}"}
    try:
        if not any(p.lower() == NEEDED for p, _, _ in g.files()):
            return {"kind": None, "path": str(path),
                    "why": "An Xbox 360 game, but not Forza Horizon (2012): it has no media/tracks/colorado."}
    except (OSError, XboxError, struct.error) as e:
        return {"kind": None, "path": str(path), "why": f"Couldn't read its files: {e}"}
    finally:
        g.close()
    return {"kind": kind, "path": str(path), "why": why}


def detect(path, look_inside=True):
    """{'kind': 'folder'|'iso'|'god'|None, 'path': the folder/file to use, 'why': text}.
    look_inside checks that an ISO or GOD package holds Forza Horizon (reads its directories, under a second)."""
    d = _detect(path)
    if look_inside and d["kind"] in ("iso", "god"):
        return _checked(d["kind"], d["path"], d["why"])
    return d


def _detect(path):
    p = Path(str(path or "").strip().strip('"'))
    if not str(path or "").strip():
        return {"kind": None, "path": "", "why": "Nothing picked yet."}
    if not p.exists():
        return {"kind": None, "path": str(p), "why": "That path doesn't exist."}
    if p.is_dir():
        for d in (p, p / "Content"):
            if (d / "media" / "tracks" / "colorado" / "bin.zip").exists():
                return {"kind": "folder", "path": str(d), "why": "Extracted game files."}
        if (p / "default.xex").exists() or (p / "media").is_dir():
            return {"kind": None, "path": str(p),
                    "why": "This looks like an Xbox 360 game folder, but not Forza Horizon's: "
                           "media/tracks/colorado/bin.zip is missing."}
    if p.is_file() and p.suffix.lower() in (".iso", ".xiso"):
        return {"kind": "iso", "path": str(p), "why": "A disc image (ISO)."}
    g = _god_header(p)
    if g:
        return {"kind": "god", "path": str(g[0]), "why": "A Games on Demand (GOD) package."}
    if p.is_dir() and list(p.glob("*.iso")):
        return {"kind": "iso", "path": str(sorted(p.glob("*.iso"))[0]), "why": "A disc image (ISO)."}
    return {"kind": None, "path": str(p), "why": "No Forza Horizon game files, ISO or GOD package found here."}


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


def open_game(path):
    d = detect(path, look_inside=False)
    if d["kind"] == "iso":
        return Game(_IsoSource(d["path"]))
    if d["kind"] == "god":
        return Game(_GodSource(str(d["path"]) + ".data"))
    raise XboxError(d["why"] if d["kind"] is None else "Already extracted: nothing to read.")
