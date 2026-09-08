#!/usr/bin/env python3
"""V3 Stage 1: generate golden contract fixtures from the real engine.

Builds a deterministic synthetic HOME, runs cleaner.py as a subprocess
against it exactly as an agent would, normalizes the machine-dependent
parts, and writes tests/fixtures/*.json. TestContractFixtures regenerates
and diffs on every run, so the committed fixtures ARE the JSON contract in
executable form — and the parity oracle for swift/MacCleanerKit.

Determinism notes (each earned, don't relax casually):
- PATH is a stub dir holding only `du`, so no cmd target ever finds its
  tool and no estimate command runs — cmd targets appear (with --all) but
  never execute anything machine-dependent.
- Sizes are real `du -skx` output over synthetic files whose sizes are
  fixed multiples of 4 KiB, so APFS block rounding is identity on every
  APFS machine (dev laptops and CI runners alike).
- The tmp/leftovers scanner roots point at empty sandbox dirs; simulators
  is left out of enabled_categories (xcrun output can't be sandboxed).
- Timestamps and disk lines are replaced with placeholders; every absolute
  sandbox path is rewritten to $HOME.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "cleaner.py"
BLOCK = 4096

CATEGORIES = ["xcode", "node", "python", "caches", "tmp", "leftovers", "simulators"]

# path (relative to fake HOME) -> size in 4KiB blocks
SEED_FILES = {
    "Library/Developer/Xcode/DerivedData/App-abc/Build/x.o": 8,
    "Library/Developer/Xcode/DerivedData/App-abc/Index.noindex/i": 2,
    "Library/Developer/CustomDerivedData/blob": 4,           # xcode-derived-data-custom glob
    ".npm/_cacache/content-v2/aa": 6,
    "Library/Caches/pip/wheels/w.whl": 3,
    "Library/Caches/com.spotify.client/Browser/Cache/f_0001": 5,
    "Library/Caches/com.spotify.client/Data/d0": 2,
    "Library/Caches/com.spotify.client/Login Data": 1,       # must NOT be offered
    "Library/Caches/*electron-updater-not-a-glob-match/x": 1,
    "Library/Caches/app.electron-updater/pending/Setup.dmg": 7,
}

# tmp-scanner scenarios, seeded under the sandboxed MACCLEANER_TMP_ROOT.
# Directory name -> (shape builder marker, aged?, expected?) — documented in
# each builder below. Ages use utime(now - 5 days); the config's
# tmp_min_age_days default (1) then admits aged dirs and refuses fresh ones.
TMP_AGE = 5 * 86400

def _seed_tmp_root(tmp: Path):
    import time as _t
    old = _t.time() - TMP_AGE

    def aged(*paths):
        for p in paths:
            os.utime(p, (old, old))

    def derived_shape(d: Path):
        (d / "Build" / "Intermediates.noindex").mkdir(parents=True)
        (d / "Build" / "Intermediates.noindex" / "o.o").write_bytes(b"x" * (2 * BLOCK))

    # offered: top-level DerivedData shape
    d = tmp / "ws-derived-top"; derived_shape(d); aged(d)
    # offered: repo clone with manifest + build artifacts
    c = tmp / "ws-clone"
    (c / ".git").mkdir(parents=True)
    (c / "package.json").write_bytes(b"{}")
    (c / "node_modules").mkdir()
    (c / "node_modules" / "m.js").write_bytes(b"x" * (3 * BLOCK))
    aged(c)
    # offered: the NESTED case — workspace itself is not junk, its child is
    # (Build/ + Index.noindex/ + one corroborating marker, no info.plist)
    w = tmp / "ws-nested"
    (w / "derived" / "Build").mkdir(parents=True)
    (w / "derived" / "Index.noindex").mkdir()
    (w / "derived" / "ModuleCache.noindex").mkdir()
    (w / "derived" / "Build" / "big.o").write_bytes(b"x" * (4 * BLOCK))
    (w / "run.log").write_bytes(b"keep me")
    aged(w, w / "derived")
    # offered: the .xcactivitylog signature
    x = tmp / "xcactivity-ws"
    (x / "Logs" / "Build").mkdir(parents=True)
    (x / "Logs" / "Build" / "1.xcactivitylog").write_bytes(b"x" * BLOCK)
    aged(x)
    # refused: too young (mtime = now)
    y = tmp / "ws-young"; derived_shape(y)
    # refused: active-session prefix, however old
    cl = tmp / "claude-session"; derived_shape(cl); aged(cl)
    # refused: symlink, never followed
    os.symlink(str(d), str(tmp / "link-trap"))
    # refused: plain file
    (tmp / "plainfile").write_bytes(b"x")
    # refused: .git clone with manifest but NO build artifacts
    g = tmp / "clean-checkout"
    (g / ".git").mkdir(parents=True)
    (g / "package.json").write_bytes(b"{}")
    aged(g)


LEFTOVER_AGE = 10 * 86400   # > app_leftover_min_age_days (7) by margin


def _seed_leftover_lib(lib: Path, apps: Path):
    """Leftovers sandbox: one orphan spanning several roots, one candidate
    owned by an installed app (case-insensitive), one owned via wrapper
    nesting, Apple/self exclusions, wrong shapes, and a symlink trap.
    mdfind is absent from the stub PATH, so the Spotlight pass degrades to a
    no-op in BOTH engines. All sizes 4 KiB multiples."""
    import time as _t
    old = _t.time() - LEFTOVER_AGE

    def aged(*paths):
        for p in paths:
            os.utime(p, (old, old))

    # roots
    for r in ("Caches", "Preferences", "Saved Application State",
              "HTTPStorages", "WebKit"):
        (lib / r).mkdir(parents=True, exist_ok=True)
    # the orphan, present in four roots with per-root shapes
    d = lib / "Caches" / "com.gone.app"; d.mkdir()
    (d / "blob").write_bytes(b"x" * (2 * BLOCK)); aged(d)
    w = lib / "WebKit" / "com.gone.app"; w.mkdir()
    (w / "blob").write_bytes(b"x" * BLOCK); aged(w)
    pl = lib / "Preferences" / "com.gone.app.plist"
    pl.write_bytes(b"x" * BLOCK); aged(pl)
    sv = lib / "Saved Application State" / "com.gone.app.savedState"; sv.mkdir()
    (sv / "data.data").write_bytes(b"x" * BLOCK); aged(sv)
    # a second orphan: binarycookies FILE shape
    ck = lib / "HTTPStorages" / "com.cookie.only.binarycookies"
    ck.write_bytes(b"x" * BLOCK); aged(ck)
    # excluded: Apple domain, however orphaned
    ap = lib / "Caches" / "com.apple.something"; ap.mkdir(); aged(ap)
    # owned: installed app (opposite case in its Info.plist)
    owned = lib / "Caches" / "com.installed.tool"; owned.mkdir(); aged(owned)
    sub = lib / "Caches" / "com.installed.tool.helper"; sub.mkdir(); aged(sub)
    # owned via wrapper-nested .app
    wr = lib / "Caches" / "com.wrapped.app"; wr.mkdir(); aged(wr)
    # wrong shapes: file where dir expected; dir where file expected
    (lib / "Caches" / "com.file.shaped").write_bytes(b"x"); aged(lib / "Caches" / "com.file.shaped")
    bad = lib / "Preferences" / "com.dir.shaped.plist"; bad.mkdir(); aged(bad)
    # symlink: never a candidate
    os.symlink(str(d), str(lib / "Caches" / "com.linked.app"))
    # too young: refused by the age gate
    yg = lib / "Caches" / "com.too.young"; yg.mkdir()
    # installed apps
    plist = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
             '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
             '<plist version="1.0"><dict><key>CFBundleIdentifier</key>'
             '<string>%s</string></dict></plist>')
    t = apps / "Tool.app" / "Contents"; t.mkdir(parents=True)
    (t / "Info.plist").write_text(plist % "com.installed.TOOL")
    v = apps / "Vendor Wrapper" / "Wrapped.app" / "Contents"; v.mkdir(parents=True)
    (v / "Info.plist").write_text(plist % "com.wrapped.app")


def _write_xcrun_stub(stub: Path, root: Path):
    """Canned `xcrun simctl` for the sandbox: fixed ancient timestamps (so
    staleness never depends on when fixtures are generated), a booted device
    that must be skipped, an injection-shaped udid that must be dropped, a
    device with no timestamps whose aged dataPath exercises the mtime
    fallback, and the Xcode-26 UUID-keyed runtime shape with one unused
    runtime. dataPaths point into the sandbox at 4 KiB seeds."""
    dp1 = root / "simdata" / "dev1"
    dp2 = root / "simdata" / "dev2"
    for d in (dp1, dp2):
        d.mkdir(parents=True, exist_ok=True)
        (d / "disk.img").write_bytes(b"x" * (2 * BLOCK))
    import time as _t
    old = _t.time() - TMP_AGE * 20
    for d in (dp1, dp2):
        os.utime(d, (old, old))
    devices = {
        "devices": {
            "com.apple.CoreSimulator.SimRuntime.iOS-26-5": [
                {"udid": "AAAAAAAA-0000-0000-0000-000000000001", "name": "Stale One",
                 "state": "Shutdown", "lastBootedAt": "2019-01-01T00:00:00Z",
                 "dataPath": str(dp1)},
                {"udid": "AAAAAAAA-0000-0000-0000-000000000002", "name": "Booted",
                 "state": "Booted", "lastBootedAt": "2019-01-01T00:00:00Z"},
                {"udid": "AAAAAAAA-0000-0000-0000-000000000003", "name": "Mtime Fallback",
                 "state": "Shutdown", "dataPath": str(dp2)},
                {"udid": "evil;rm -rf /", "name": "Injection",
                 "state": "Shutdown", "lastBootedAt": "2019-01-01T00:00:00Z"},
            ],
        }
    }
    runtimes = {
        "11111111-2222-3333-4444-555555555555": {
            "identifier": "11111111-2222-3333-4444-555555555555",
            "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.iOS-18-1",
            "state": "Ready", "sizeBytes": 8000000000, "deletable": True},
        "66666666-7777-8888-9999-000000000000": {
            "identifier": "66666666-7777-8888-9999-000000000000",
            "runtimeIdentifier": "com.apple.CoreSimulator.SimRuntime.iOS-26-5",
            "state": "Ready", "sizeBytes": 5000000000, "deletable": True},
    }
    devices_json = json.dumps(devices).replace("'", "'\\''")
    runtimes_json = json.dumps(runtimes).replace("'", "'\\''")
    lines = [
        "#!/bin/bash",
        "# Uses ONLY bash builtins: the sandbox PATH holds just du/xcrun, so",
        "# forking cat/sed/etc. here fails silently with output nobody sees.",
        'case "$*" in',
        '  *"list devices"*) printf \'%s\\n\' \'' + "'PLACEHOLDER_D'" + '\' ;;',
        '  *"runtime list"*) printf \'%s\\n\' \'' + "'PLACEHOLDER_R'" + '\' ;;',
        "  *) exit 1 ;;",
        "esac",
    ]
    script = "\n".join(lines) + "\n"
    script = script.replace("'PLACEHOLDER_D'", devices_json)
    script = script.replace("'PLACEHOLDER_R'", runtimes_json)
    (stub / "xcrun").write_text(script)
    os.chmod(stub / "xcrun", 0o755)


def build_sandbox(root: Path):
    home = root / "home"
    for rel, blocks in SEED_FILES.items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * (blocks * BLOCK))
    for extra in ["tmp-root", "leftover-lib", "apps", "agents", "state", "bin"]:
        (root / extra).mkdir(parents=True, exist_ok=True)
    _seed_tmp_root(root / "tmp-root")
    _seed_leftover_lib(root / "leftover-lib", root / "apps")
    _write_xcrun_stub(root / "bin", root)
    cfg = {
        "enabled_categories": CATEGORIES,
        "skip_paths": [],
        "known_categories": None,  # replaced below
    }
    # Stamp known_categories with the full list so load_config's migration
    # doesn't re-enable everything we deliberately left out.
    all_cats = json.loads(subprocess.run(
        [sys.executable, str(ENGINE), "categories", "--json"],
        capture_output=True, text=True, timeout=120,
        env=base_env(root)).stdout)["categories"]
    cfg["known_categories"] = [c["name"] for c in all_cats]
    (root / "state" / "config.json").write_text(json.dumps(cfg))
    return home

def base_env(root: Path):
    stub = root / "bin"
    stub.mkdir(exist_ok=True)
    if not (stub / "du").exists():
        os.symlink("/usr/bin/du", stub / "du")
    return {
        "HOME": str(root / "home"),
        "PATH": str(stub),
        "MACCLEANER_CONFIG": str(root / "state" / "config.json"),
        "MACCLEANER_LOG": str(root / "state" / "report.log"),
        "MACCLEANER_SNAPSHOTS": str(root / "state" / "snapshots.log"),
        "MACCLEANER_ALERTS": str(root / "state" / "alerts.json"),
        "MACCLEANER_TMP_ROOT": str(root / "tmp-root"),
        "MACCLEANER_LEFTOVER_LIBRARY_ROOT": str(root / "leftover-lib"),
        "MACCLEANER_INSTALLED_APPS_DIRS": str(root / "apps"),
        "MACCLEANER_LAUNCH_AGENTS_DIR": str(root / "agents"),
        "MACCLEANER_SYSTEM_APPLICATIONS_DIR": str(root / "apps"),
    }

def run(root: Path, *args):
    r = subprocess.run([sys.executable, str(ENGINE), *args],
                       capture_output=True, text=True, timeout=300,
                       env=base_env(root))
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"engine failed: {args} (exit {r.returncode})")
    return json.loads(r.stdout)

def normalize(obj, home: str):
    """Sandbox paths -> $HOME; volatile fields -> placeholders."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("timestamp", "first_run", "last_run"):
                out[k] = "<TS>" if v else v
            elif k in ("disk", "disk_after", "disk_stats", "disk_history"):
                out[k] = "<DISK>"
            else:
                out[k] = normalize(v, home)
        return out
    if isinstance(obj, list):
        return [normalize(v, home) for v in obj]
    if isinstance(obj, str):
        return obj.replace(home, "$HOME")
    return obj

def seed_report_log(root: Path):
    log = [
        {"timestamp": "2026-08-24T09:00:00", "total_freed_bytes": 3 * BLOCK,
         "total_freed_human": "12.0 KB", "disk_after": "x",
         "items": [{"id": "pip-cache", "label": "pip cache", "freed": 3 * BLOCK,
                    "status": "deleted"}]},
        {"timestamp": "2026-08-30T09:00:00", "total_freed_bytes": 14 * BLOCK,
         "total_freed_human": "56.0 KB", "disk_after": "x",
         "items": [{"id": "npm-cache", "label": "npm cache", "freed": 6 * BLOCK,
                    "status": "deleted"},
                   {"id": "xcode-derived-data", "label": "Xcode DerivedData",
                    "freed": 8 * BLOCK, "status": "deleted"}]},
    ]
    (root / "state" / "report.log").write_text(json.dumps(log))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "tests" / "fixtures"))
    args = ap.parse_args()
    outdir = Path(args.out); outdir.mkdir(parents=True, exist_ok=True)

    root = Path(tempfile.mkdtemp(prefix="mc-fixtures-"))
    try:
        home = build_sandbox(root)
        seed_report_log(root)
        fixtures = {
            "scan.json": run(root, "scan", "--json", "--all"),
            "categories.json": run(root, "categories", "--json"),
            "dry_run.json": run(root, "clean", "--dry-run", "--yes", "--json",
                                "--targets", "npm-cache,spotify-browser-cache"),
            "report_stats.json": run(root, "report", "--stats", "--json"),
            "schedule_status.json": run(root, "schedule", "status", "--json"),
        }
        for name, data in fixtures.items():
            (outdir / name).write_text(
                json.dumps(normalize(data, str(home)), indent=2, sort_keys=True) + "\n")
        print(f"wrote {len(fixtures)} fixtures to {outdir}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

if __name__ == "__main__":
    main()
