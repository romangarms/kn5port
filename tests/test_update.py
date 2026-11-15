import hashlib

import pytest

from kn5port import update


def release(tag="v9.0.0", data=b"MZ" + b"\0" * 100, name="kn5port.exe", url="https://example/x.exe"):
    return {"tag_name": tag, "html_url": f"https://github.com/{update.REPO}/releases/tag/{tag}", "body": " notes ",
            "assets": [{"name": "other.zip", "browser_download_url": "nope", "size": 1},
                       {"name": name, "browser_download_url": url, "size": len(data),
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest()}]}


def test_clean_notes():
    body = ("Download `kn5port.exe` below and run it. Fully restart BeamNG.\r\n\r\n"
            "## Changes\n- Per-layout **kn5s**: only `the` chosen layout\n- Road layer lift\n\n"
            "## What's Changed\n* Fix a thing by @someone in https://github.com/romangarms/kn5port/pull/3\n\n"
            "## New Contributors\n* @someone made their first contribution in https://github.com/x/pull/3\n\n"
            "**Full Changelog**: https://github.com/romangarms/kn5port/compare/v1.0.0...v1.0.1")
    assert update.clean_notes(body) == "• Per-layout kn5s: only the chosen layout\n• Road layer lift\n\n• Fix a thing"
    assert update.clean_notes(None) == ""


def test_parse_version():
    assert update.parse_version("v1.2.3") == (1, 2, 3)
    assert update.parse_version("1.10.0") > update.parse_version("1.9.9")
    assert update.parse_version("v1.2") is None and update.parse_version(None) is None


def test_describe():
    d = update.describe(release("v1.0.2"), current="1.0.1")
    assert d["newer"] and d["version"] == "1.0.2" and d["notes"] == "notes"
    assert d["asset"]["url"] == "https://example/x.exe" and len(d["asset"]["sha256"]) == 64
    assert not update.describe(release("v1.0.1"), current="1.0.1")["newer"]
    assert not update.describe(release("v1.0.0"), current="1.0.1")["newer"]
    assert update.describe(release(name="kn5port.zip"), current="1.0.0")["asset"] is None


def _asset(tmp_path, data, sha=None):
    src = tmp_path / "src.exe"
    src.write_bytes(data)
    return {"url": src.as_uri(), "size": len(data), "sha256": sha or hashlib.sha256(data).hexdigest()}


def test_download_checks(tmp_path):
    good = b"MZ" + bytes(range(256)) * 10
    out = update.download(_asset(tmp_path, good), tmp_path / "new.exe")
    assert out.read_bytes() == good
    with pytest.raises(RuntimeError, match="checksum"):
        update.download(_asset(tmp_path, good, sha="0" * 64), tmp_path / "bad.exe")
    with pytest.raises(RuntimeError, match="Windows program"):
        update.download(_asset(tmp_path, b"<html>"), tmp_path / "html.exe")
    assert not (tmp_path / "bad.exe").exists() and not list(tmp_path.glob("*.part"))


def test_swap_and_cleanup(tmp_path):
    exe = tmp_path / "kn5port.exe"; exe.write_bytes(b"old")
    new = tmp_path / ("kn5port.exe" + update.DOWNLOAD_SUFFIX); new.write_bytes(b"new")
    old = update.swap(exe, new)
    assert exe.read_bytes() == b"new" and old.read_bytes() == b"old" and not new.exists()
    (tmp_path / "kn5port.exe.download.part").write_bytes(b"x")
    keep = tmp_path / "kn5port.exe.config"; keep.write_bytes(b"x")
    update.cleanup(exe, wait=0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kn5port.exe", "kn5port.exe.config"]


def test_updater_not_frozen(monkeypatch):
    monkeypatch.setattr(update, "fetch_latest", lambda timeout=10: release("v99.0.0"))
    u = update.Updater()
    u.check(background=False)
    v = u.view()
    assert v["state"] == "available" and v["latest"] == "99.0.0" and not v["can_install"]
    with pytest.raises(RuntimeError):
        u.install()                     # running from source never replaces anything
