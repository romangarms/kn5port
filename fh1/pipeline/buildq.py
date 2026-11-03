"""Build queue, so several sessions can ask for builds without stepping on each other.

    python buildq.py submit -m "what changed" [--by name] [--chunk colorado] [--env FH1_X=0] [--beamng-ok]
    python buildq.py status          what's running, queued and recently built, with each request's note
    python buildq.py wait <id>       block until the build covering request <id> ends, then print its result
    python buildq.py cancel <id>     drop a queued request
    python buildq.py stop            kill the running build

One runner builds at a time. Pending requests with the same chunk and flags fold into one build of the newest code, which covers
them all. Before starting, the runner waits until no file in pipeline/ has changed for SETTLE seconds, so it doesn't catch a
session halfway through an edit, and while BeamNG is open, as a build wipes the level it may have loaded. It then copies
pipeline/ to fh1/build_snapshot/ and builds from the copy, so edits made during a build go into the next one. `submit` starts the
runner if none is alive, and the runner exits when the queue is empty. State lives in fh1/buildq/."""
import sys, os, json, time, shutil, hashlib, argparse, subprocess, ctypes
from pathlib import Path

PIPE = Path(__file__).resolve().parent; FH1 = PIPE.parent
Q = Path(os.environ.get('BUILDQ_DIR', FH1 / 'buildq')); PENDING = Q / 'pending'; RUNS = Q / 'runs'; LOCK = Q / 'runner.lock'; CURRENT = Q / 'current.json'; HISTORY = Q / 'history.jsonl'
SNAP = Path(os.environ.get('BUILDQ_SNAP', FH1 / 'build_snapshot'))      # one level under fh1/, like pipeline/, so the scripts' ../ paths still reach the data
SETTLE = int(os.environ.get('BUILDQ_SETTLE', 15)); POLL = 15
NOWIN = 0x08000000       # CREATE_NO_WINDOW. The runner has no visible console, so every console program it starts would open a window
BUILD = os.environ.get('BUILDQ_CMD', 'build3.py')          # BUILDQ_DIR, BUILDQ_SNAP, BUILDQ_CMD and BUILDQ_SETTLE are for testing the queue itself
for d_ in (PENDING, RUNS):
    d_.mkdir(parents=True, exist_ok=True)


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def alive(pid):
    """os.kill(pid, 0) would terminate the process on Windows, so ask for its exit code instead"""
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong(); ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)); ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259                                   # STILL_ACTIVE


def write_json(p, d):
    t = p.with_suffix('.tmp'); t.write_text(json.dumps(d, indent=1)); os.replace(t, p)


def read_json(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def pending():
    return sorted(filter(None, (read_json(p) for p in PENDING.glob('*.json'))), key=lambda r: r['id'])


def history(n=None):
    try:
        rows = [json.loads(l) for l in HISTORY.read_text().splitlines() if l.strip()]
    except OSError:
        rows = []
    return rows[-n:] if n else rows


def files():
    return [p for p in PIPE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.tmp', '.pyc')]


def manifest():
    return {str(p.relative_to(PIPE)).replace('\\', '/'): hashlib.sha1(p.read_bytes()).hexdigest()[:12] for p in files()}


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', ctypes.c_ulong), ('cntUsage', ctypes.c_ulong), ('th32ProcessID', ctypes.c_ulong), ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', ctypes.c_ulong), ('cntThreads', ctypes.c_ulong), ('th32ParentProcessID', ctypes.c_ulong), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', ctypes.c_ulong), ('szExeFile', ctypes.c_wchar * 260)]


def exe_names():
    """names of running programs, from the Windows API rather than tasklist, which would be one more process every poll"""
    k = ctypes.windll.kernel32; k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    snap = k.CreateToolhelp32Snapshot(2, 0); e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e); names = set()
    ok = k.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(e))
    while ok:
        names.add(e.szExeFile.lower()); ok = k.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(e))
    k.CloseHandle(ctypes.c_void_p(snap))
    return names


def beamng_open():
    return 'beamng.drive.x64.exe' in exe_names()


def hand_build():
    """a build3.py started outside the queue, which would fight ours for RAM and the level folder"""
    out = subprocess.run(['powershell', '-NoProfile', '-Command', "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { $_.CommandLine }"],
                         capture_output=True, text=True, creationflags=NOWIN).stdout
    return any('build3.py' in l for l in out.splitlines())


def key(r):
    return r['chunk'], tuple(sorted(r['env'].items()))


def runner_pid():
    d = read_json(LOCK)
    return d['pid'] if d and alive(d['pid']) else None


def start_runner():
    if runner_pid():
        return
    flags = NOWIN | 0x00000200          # a hidden console that the build and its scripts inherit, and CREATE_NEW_PROCESS_GROUP
    args = dict(cwd=PIPE, stdin=subprocess.DEVNULL, stdout=open(Q / 'runner.out', 'a'), stderr=subprocess.STDOUT, close_fds=True)
    try:
        subprocess.Popen([sys.executable, '-u', str(PIPE / 'buildq.py'), 'run'], creationflags=flags | 0x01000000, **args)   # break away from the session's job
    except OSError:
        subprocess.Popen([sys.executable, '-u', str(PIPE / 'buildq.py'), 'run'], creationflags=flags, **args)


def set_state(cur, **kw):
    cur.update(kw); write_json(CURRENT, cur)


def run():
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if runner_pid():
            return
        LOCK.unlink(missing_ok=True); fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, json.dumps({'pid': os.getpid(), 'since': now()}).encode()); os.close(fd)
    try:
        while True:
            reqs = pending()
            if not reqs:
                CURRENT.unlink(missing_ok=True)
                return
            k = key(reqs[0]); group = [r for r in reqs if key(r) == k]       # the oldest request's kind goes first
            cur = {'state': 'waiting', 'chunk': k[0], 'env': dict(k[1]), 'requests': group}
            while True:                                                       # settle, and keep BeamNG's level alone
                group = [r for r in pending() if key(r) == k]                 # all cancelled: let the next kind go, rather than wait on BeamNG
                if not group:
                    break
                quiet = time.time() - max(p.stat().st_mtime for p in files())
                if quiet < SETTLE:
                    set_state(cur, why=f'pipeline/ changed {quiet:.0f} s ago, waiting for {SETTLE} s of quiet')
                elif beamng_open() and not any(r.get('beamng_ok') for r in group):          # cheap, so before the PowerShell check
                    set_state(cur, why='BeamNG is open; close it, or submit with --beamng-ok')
                elif 'BUILDQ_CMD' not in os.environ and hand_build():
                    set_state(cur, why='a build3.py started by hand is running')
                else:
                    break
                time.sleep(POLL)
            group = [r for r in pending() if key(r) == k]                     # anything submitted while waiting rides along
            if not group:
                continue
            rid = 'b' + time.strftime('%Y%m%d-%H%M%S')
            shutil.rmtree(SNAP, ignore_errors=True)
            shutil.copytree(PIPE, SNAP, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.tmp'))
            man = manifest(); prev = next((h for h in reversed(history()) if h['chunk'] == k[0] and h.get('manifest')), None)
            changed = (sorted(f for f in man if prev['manifest'].get(f) != man[f]) + sorted(f'{f} (deleted)' for f in prev['manifest'] if f not in man)) if prev                 else [f'first queued build of {k[0]}']
            for r in group:
                (PENDING / f"{r['id']}.json").unlink(missing_ok=True)
            out = RUNS / f'{rid}.out'; env = dict(os.environ, FH1_CHUNK=k[0], PYTHONUNBUFFERED='1', **dict(k[1]))
            t0 = time.time()
            with open(out, 'w', encoding='utf8') as f_:
                p_ = subprocess.Popen([sys.executable, '-u', BUILD], cwd=SNAP, env=env, stdout=f_, stderr=subprocess.STDOUT, creationflags=NOWIN)
                set_state(cur, state='running', why='', run=rid, pid=p_.pid, started=now(), out=str(out), changed=changed, requests=group)
                code = p_.wait()
            text = out.read_text(encoding='utf8', errors='replace').splitlines()
            log = next((l[4:].strip() for l in text if l.startswith('log ')), None)
            row = {'run': rid, 'chunk': k[0], 'env': dict(k[1]), 'started': cur['started'], 'minutes': round((time.time() - t0) / 60, 1),
                   'result': 'ok' if code == 0 else f'failed ({code})', 'tail': text[-3:], 'out': str(out), 'log': log,
                   'requests': group, 'changed': changed, 'manifest': man}
            with open(HISTORY, 'a') as h_:
                h_.write(json.dumps(row) + '\n')
    finally:
        LOCK.unlink(missing_ok=True)


def submit(a):
    env = dict(e.split('=', 1) for e in a.env)
    if 'FH1_CHUNK' in env:
        sys.exit('use --chunk, not --env FH1_CHUNK')
    r = {'id': 'q' + time.strftime('%Y%m%d-%H%M%S') + f'{time.time() % 1:.3f}'[1:] + f'-{os.getpid() % 1000:03d}', 'by': a.by, 'note': a.message, 'chunk': a.chunk,
         'env': env, 'beamng_ok': a.beamng_ok, 'submitted': now()}
    write_json(PENDING / f"{r['id']}.json", r); start_runner()
    cur = read_json(CURRENT); ahead = cur and cur.get('state') == 'running'
    print(f"queued {r['id']} ({a.chunk}). " + ('A build is running; this goes in the next one.' if ahead else 'It starts once pipeline/ has been quiet for %d s.' % SETTLE))
    print(f'check: python buildq.py status   or wait: python buildq.py wait {r["id"]}')


def fmt_req(r):
    return f"{r['id']}  [{r['by']}] {r['note']}" + (f"  env {r['env']}" if r['env'] else '')


def status(a):
    cur = read_json(CURRENT); pid = runner_pid()
    if cur and pid:
        if cur['state'] == 'running':
            text = Path(cur['out']).read_text(encoding='utf8', errors='replace').splitlines() if Path(cur['out']).exists() else []
            mins = (time.time() - time.mktime(time.strptime(cur['started'], '%Y-%m-%d %H:%M:%S'))) / 60
            print(f"RUNNING {cur['run']}  {cur['chunk']}{'  ' + str(cur['env']) if cur['env'] else ''}  for {mins:.0f} min, output {cur['out']}")
            print('  last line: ' + (text[-1][:150] if text else '(none yet)'))
            print('  changed since the last build of this chunk: ' + (', '.join(cur['changed']) or 'nothing'))
        else:
            print(f"WAITING to build {cur['chunk']}: {cur.get('why', '')}")
            cur['requests'] = [r for r in pending() if key(r) == (cur['chunk'], tuple(sorted(cur['env'].items())))]
        for r in cur['requests']:
            print('  covers ' + fmt_req(r))
    else:
        print('no build running' + (' (stale runner state cleared)' if cur else ''))
        if cur:
            CURRENT.unlink(missing_ok=True)
    q = [r for r in pending() if not (cur and pid and cur['state'] == 'waiting' and r in cur['requests'])]      # the rest go in later builds
    if q:
        print('QUEUED (requests with the same chunk and flags fold into one build of the newest code)')
        for r in q:
            print('  ' + fmt_req(r))
        if not pid:
            print('  no runner alive: starting one'); start_runner()
    print('RECENT')
    for h in history(a.n)[::-1]:
        print(f"  {h['run']}  {h['chunk']}  {h['result']}  {h['minutes']} min  started {h['started']}  log {h['log']}")
        for r in h['requests']:
            print('     ' + fmt_req(r))
        print('     files changed: ' + (', '.join(h['changed']) or 'none'))
        if h['result'] != 'ok':
            print('     ' + '\n     '.join(h['tail']))


def wait(a):
    while True:
        h = next((h for h in history() if any(r['id'] == a.id for r in h['requests'])), None)
        if h:
            print(f"{h['run']} {h['result']} after {h['minutes']} min, log {h['log']}")
            print('\n'.join(h['tail']))
            sys.exit(0 if h['result'] == 'ok' else 1)
        if not runner_pid() and not (PENDING / f'{a.id}.json').exists():
            sys.exit(f'{a.id} is neither queued nor in the history')
        if not runner_pid():
            start_runner()
        time.sleep(POLL)


def cancel(a):
    p = PENDING / f'{a.id}.json'
    if not p.exists():
        sys.exit(f'{a.id} is not queued (a running build can only be stopped as a whole: buildq.py stop)')
    p.unlink(); print('cancelled', a.id)


def stop(a):
    cur = read_json(CURRENT)
    if not cur or cur.get('state') != 'running' or not alive(cur['pid']):
        sys.exit('no build running')
    subprocess.run(['taskkill', '/PID', str(cur['pid']), '/T', '/F'], capture_output=True, creationflags=NOWIN); print('stopped', cur['run'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); sp = ap.add_subparsers(dest='cmd', required=True)
    s = sp.add_parser('submit'); s.add_argument('-m', '--message', required=True, help='what changed, for the other sessions')
    s.add_argument('--by', default='unnamed', help='who is asking: a short session or task name'); s.add_argument('--chunk', default='colorado')
    s.add_argument('--env', action='append', default=[], help='extra FH1_* flags, KEY=VALUE'); s.add_argument('--beamng-ok', action='store_true', help="build even while BeamNG is open")
    s = sp.add_parser('status'); s.add_argument('-n', type=int, default=5, help='recent builds to show')
    s = sp.add_parser('wait'); s.add_argument('id')
    s = sp.add_parser('cancel'); s.add_argument('id')
    sp.add_parser('stop'); sp.add_parser('run')
    a = ap.parse_args()
    {'submit': submit, 'status': status, 'wait': wait, 'cancel': cancel, 'stop': stop, 'run': lambda a: run()}[a.cmd](a)
