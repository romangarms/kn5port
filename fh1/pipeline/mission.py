"""The level's top-level MissionGroup/items.level.json, which the scripts after build3.py each add to. build3.py runs several of them
at once, so every change reads, edits and writes the file under a lock."""
import hashlib, json, os, tempfile, time
from contextlib import contextmanager


def read(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write(path, rows):
    path.write_text(''.join(json.dumps(o) + '\n' for o in rows))


@contextmanager
def locked(path):
    """an exclusive lock for path, kept outside the level so BeamNG never sees it. The OS drops it if a script dies holding it"""
    lock = os.path.join(tempfile.gettempdir(), 'fh1-' + hashlib.md5(str(path.resolve()).lower().encode()).hexdigest() + '.lock')
    with open(lock, 'a+b') as f:
        if os.name == 'nt':
            import msvcrt
            while True:
                try:
                    f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1); break
                except OSError:
                    time.sleep(.05)
            try:
                yield
            finally:
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def rows(path):
    """a snapshot of the rows, never half written"""
    with locked(path):
        return read(path)


@contextmanager
def edit(path):
    """the rows to change in place, written back on leaving"""
    with locked(path):
        r = read(path); yield r; write(path, r)


def add_groups(path, *names):
    """SimGroups under MissionGroup, each once"""
    with edit(path) as r:
        for n in names:
            if not any(o.get('name') == n for o in r):
                r.append({'name': n, 'class': 'SimGroup', '__parent': 'MissionGroup'})
