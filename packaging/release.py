"""Cut a kn5port release:  python packaging/release.py [patch|minor|major|X.Y.Z] [--dry-run]

Bumps kn5port/__init__.py (the one place the version lives), runs the tests, commits "Release vX.Y.Z",
tags it and pushes main and the tag together. The tag starts .github/workflows/release.yml, which builds
kn5port.exe on GitHub's Windows runner and publishes it on the releases page with notes from the commits.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "kn5port" / "__init__.py"
VERSION_RE = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.M)


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def next_version(current, bump):
    major, minor, patch = current
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    if bump == "patch":
        return major, minor, patch + 1
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", bump)
    if not m:
        sys.exit(f"expected patch, minor, major or X.Y.Z, got {bump!r}")
    new = tuple(map(int, m.groups()))
    if new <= current:
        sys.exit(f"{'.'.join(map(str, new))} is not newer than {'.'.join(map(str, current))}")
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bump", nargs="?", default="patch", help="patch (default), minor, major or an exact X.Y.Z")
    ap.add_argument("--dry-run", action="store_true", help="check everything and show what would happen, change nothing")
    ap.add_argument("--skip-tests", action="store_true", help="don't run pytest first (the release workflow still does)")
    a = ap.parse_args()

    text = INIT.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        sys.exit(f"no __version__ = \"X.Y.Z\" line in {INIT}")
    current = tuple(map(int, m.groups()))
    new = next_version(current, a.bump)
    version = ".".join(map(str, new))
    tag = f"v{version}"

    # Releases come from main, as pushed, with nothing else mixed in.
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("releases are cut from main; switch to it first")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        sys.exit(f"uncommitted changes to tracked files; commit or stash them first:\n{dirty}")
    git("fetch", "--quiet", "--tags", "origin")
    if git("rev-list", "--count", "main..origin/main") != "0":
        sys.exit("origin/main has commits you don't; pull first")
    if git("tag", "--list", tag):
        sys.exit(f"tag {tag} already exists")

    last = git("describe", "--tags", "--abbrev=0", "--match", "v*", check=False)
    log = git("log", "--oneline", f"{last}..HEAD" if last else "HEAD")
    print(f"{'.'.join(map(str, current))} -> {version}   ({len(log.splitlines())} commits since {last or 'the start'})")

    if not a.skip_tests:
        print("running tests...")
        if subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT).returncode:
            sys.exit("tests failed; nothing was changed")

    if a.dry_run:
        print(f"dry run: would set __version__ to {version}, commit, tag {tag} and push main + {tag}")
        return

    INIT.write_text(VERSION_RE.sub(f'__version__ = "{version}"', text), encoding="utf-8")
    git("add", str(INIT.relative_to(ROOT)))
    git("commit", "--quiet", "-m", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"kn5port {version}")
    # --atomic: either both land or neither, so there's never a tag without its commit on main
    r = subprocess.run(["git", "push", "--atomic", "origin", "main", tag], cwd=ROOT)
    if r.returncode:
        sys.exit(f"push failed; the release commit and tag {tag} exist locally only. Fix and run: "
                 f"git push --atomic origin main {tag}")

    url = git("remote", "get-url", "origin").removesuffix(".git")
    print(f"\npushed {tag}. GitHub is building kn5port.exe now (about 5 minutes):\n"
          f"  build:   {url}/actions/workflows/release.yml\n  release: {url}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
