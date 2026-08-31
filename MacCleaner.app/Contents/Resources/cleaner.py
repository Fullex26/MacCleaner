#!/usr/bin/env python3
"""
MacCleaner - macOS Developer Storage Cleanup Tool

Human usage:
  maccleaner                     # Welcome / help screen
  maccleaner scan                # Show what can be cleaned + sizes
  maccleaner clean               # Interactive cleanup (TUI checklist)
  maccleaner projects            # Find stale build artifacts in your project folders
  maccleaner report              # Show cleanup history
  maccleaner doctor              # Environment / install health check
  maccleaner config show         # Show config
  maccleaner categories          # List categories and targets

Agent usage (machine-readable, see AGENTS.md):
  maccleaner scan --json
  maccleaner clean --targets npm-cache,pip-cache --yes --json
  maccleaner clean --yes --json            # all safe targets
  maccleaner projects --json
  maccleaner doctor --json

Legacy flags (--preview, --clean, --report, --json, --config-*) still work.

Exit codes: 0 success, 1 runtime error, 2 usage error.
In --json mode: JSON goes to stdout, human messages to stderr.
"""

import os
import re
import sys
import json
import math
import glob as globmod
import time
import shutil
import tempfile
import argparse
import subprocess
import datetime
import plistlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# ── Optional rich output ──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None

HOME = Path.home()


def _is_inside_app_bundle(path: Path) -> bool:
    """True if any component of `path` ends in `.app` — a macOS app bundle.

    Structural check, not a writability probe: a normal user-owned `.app`'s
    Contents/Resources is `drwxr-xr-x`, so `os.access(..., os.W_OK)` alone
    never catches "this directory lives inside a bundle"."""
    return any(part.endswith(".app") for part in path.parts)


def _resolve_state_path(env_var: str, filename: str, script_dir: Path = None) -> Path:
    """Resolve the path for a mutable state file (report.log / snapshots.log /
    alerts.json).

    `env_var` (MACCLEANER_LOG / MACCLEANER_SNAPSHOTS / MACCLEANER_ALERTS)
    always wins when set.
    Otherwise prefer the directory beside cleaner.py — the normal installed
    case, `~/mac-cleaner/`. Fall back to
    `~/Library/Application Support/MacCleaner/` (creating it if needed) when
    either:
      - that directory isn't writable, or
      - it's inside a `.app` bundle (e.g. cleaner.py running from a signed
        .app bundle's Contents/Resources — the fallback engine path for
        someone who downloaded the release without running install.sh).
        Bundle directories are user-owned and writable, so this case is
        detected structurally rather than via os.access; without it, history
        would be written inside the bundle (invalidating its ad-hoc
        signature, and wiped on the next app update) — and under App
        Translocation the writability probe alone would fire only until the
        user drags the app out of ~/Downloads, silently flipping the write
        location afterward.
    This keeps disk trends working for app-only users who never ran
    install.sh.
    """
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    script_dir = Path(script_dir) if script_dir is not None else Path(__file__).parent
    if not _is_inside_app_bundle(script_dir) and os.access(script_dir, os.W_OK):
        return script_dir / filename
    fallback_dir = HOME / "Library/Application Support/MacCleaner"
    try:
        fallback_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback_dir / filename


def _resolve_config_path(script_dir: Path = None) -> Path:
    """Resolve CONFIG_PATH -- like _resolve_state_path (used for the other
    three state files), but with one extra rule ahead of it: an EXISTING
    sibling config.json wins even when the script directory isn't writable.
    Without this, a shared/admin-owned install (e.g. /opt/mac-cleaner,
    owned by an admin, readable but not writable by this user) would
    silently abandon its shared config and read fresh per-user Application
    Support defaults instead -- a regression vs 2.4 behavior (finding F6).

    Three cases, in order:
      1. MACCLEANER_CONFIG env override -> always wins (same as every
         other state file).
      2. Not inside a .app bundle AND a sibling config.json already exists
         -> the sibling, even if the directory itself isn't writable
         (reads still work; a later `config set` write may fail exactly as
         it did before this fix -- acceptable, and no worse than 2.4).
      3. Otherwise -> _resolve_state_path's normal bundle-routes-to-App-
         Support / writable-or-fallback rule (unchanged, same as every
         other state file)."""
    override = os.environ.get("MACCLEANER_CONFIG")
    if override:
        return Path(override)
    script_dir = Path(script_dir) if script_dir is not None else Path(__file__).parent
    if not _is_inside_app_bundle(script_dir):
        sibling = script_dir / "config.json"
        if sibling.exists():
            return sibling
    return _resolve_state_path("MACCLEANER_CONFIG", "config.json", script_dir=script_dir)


LOG_PATH = _resolve_state_path("MACCLEANER_LOG", "report.log")
SNAPSHOTS_PATH = _resolve_state_path("MACCLEANER_SNAPSHOTS", "snapshots.log")
ALERTS_PATH = _resolve_state_path("MACCLEANER_ALERTS", "alerts.json")
CONFIG_PATH = _resolve_config_path()
SNAPSHOT_CAP = 365
VERSION = "2.15.0"

# ── Default config ─────────────────────────────────────────────────────────────
ALL_CATEGORIES = [
    "xcode", "docker", "node", "python", "caches", "logs", "homebrew",
    "go", "rust", "ruby", "cocoapods", "gradle", "maven",
    "ai", "ide", "browsers", "system",
    "flutter", "php", "vms",
    "tmp", "simulators",
    "leftovers",
]

# Frozen snapshot of the category list as of v2.4 — the baseline for the
# known_categories migration below. A config saved before known_categories
# existed is assumed to know exactly these; anything in ALL_CATEGORIES
# beyond this list is "new since the user last saved" and gets auto-enabled.
# Append-only: never edit this list again; future releases only grow
# ALL_CATEGORIES.
V24_CATEGORIES = [
    "xcode", "docker", "node", "python", "caches", "logs", "homebrew",
    "go", "rust", "ruby", "cocoapods", "gradle", "maven",
    "ai", "ide", "browsers", "system",
    "flutter", "php", "vms",
]

CATEGORY_DESCRIPTIONS = {
    "xcode":     "Xcode build products, device support, simulators, SwiftPM/Carthage",
    "docker":    "Docker unused images, containers, and build cache",
    "node":      "npm/pnpm/yarn/bun/deno package caches",
    "python":    "pip/uv/poetry/ruff caches, pyenv shims",
    "caches":    "App caches (Claude, Cursor, Chrome, Slack, Discord, Spotify, ...)",
    "logs":      "Oversized log folders in ~/Library/Logs",
    "homebrew":  "Homebrew download cache and unused dependencies",
    "go":        "Go module and build caches",
    "rust":      "Cargo registry and git caches",
    "ruby":      "Stale gem versions",
    "cocoapods": "CocoaPods cache",
    "gradle":    "Gradle build caches",
    "maven":     "Maven local repository",
    "ai":        "Downloaded AI models (Hugging Face, PyTorch, Ollama — re-downloadable) and Codex session transcripts (not re-downloadable) — review carefully",
    "ide":       "Editor caches (VS Code, JetBrains)",
    "browsers":  "Browser caches (Arc, Brave, Edge, Firefox)",
    "system":    "Trash and iOS device backups — review carefully",
    "flutter":   "Dart & Flutter pub package cache",
    "php":       "Composer package cache",
    "vms":       "VM disks and container runtimes (Colima, Vagrant, minikube) — review carefully",
    "tmp":       "Stale build artifacts in /private/tmp left by tools and AI coding sessions — review carefully",
    "simulators": "Stale iOS simulator devices and unused runtime images (via simctl) — review carefully",
    "leftovers": "Cache, preference, and saved-state files left behind by apps you've already deleted — review carefully",
}

DEFAULT_CONFIG = {
    "enabled_categories": list(ALL_CATEGORIES),
    "skip_paths": [],
    "log_threshold_mb": 100,
    "auto_approve": False,
    "delete_mode": "rm",  # "rm" = delete immediately, "trash" = move to ~/.Trash
    "project_roots": ["~/Documents", "~/Developer", "~/Projects", "~/Code", "~/dev"],
    "project_min_age_days": 30,
    "project_git_check": True,
    "tmp_min_age_days": 1,           # /tmp dirs younger than this are never offered
    "simulator_stale_days": 30,      # simulators not booted for this long count as stale
    "app_leftover_min_age_days": 7,  # orphaned app data younger than this is never offered
    "notifications": True,           # notify when a scheduled clean finishes
    "low_disk_alerts": True,         # warn when free space drops below the threshold
    "low_disk_threshold_gb": 10,     # the low-disk warning threshold
    "full_refresh_hours": 6,         # how often the app runs a full scan (app-side)
    "show_in_dock": False,           # app-side: show a Dock icon as well as the menu bar
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # A torn write (crash mid-save, two Settings clicks racing before
            # save_config became atomic) can leave invalid JSON on disk. That
            # must never traceback the CLI, the app's bridge call, or the
            # launchd agent — fall back to defaults and let the next save_config
            # (now atomic) heal the file on disk.
            print(f"Warning: config.json is corrupt ({e}); using defaults", file=sys.stderr)
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
            cfg["known_categories"] = list(ALL_CATEGORIES)
            return cfg
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        # Auto-enable categories added since this config was last saved.
        # Without this, a category added in a new release never appears for
        # existing installs (enabled_categories is a saved list, not merged).
        known = set(cfg.get("known_categories", V24_CATEGORIES))
        for c in ALL_CATEGORIES:
            if c not in known and c not in cfg["enabled_categories"]:
                cfg["enabled_categories"].append(c)
        cfg["known_categories"] = list(ALL_CATEGORIES)
        return cfg
    # Fresh install (no config.json on disk yet): stamp known_categories here.
    # Do NOT add this to DEFAULT_CONFIG — the setdefault loop above runs
    # BEFORE the migration block, so a DEFAULT_CONFIG entry would apply
    # known_categories=ALL_CATEGORIES to old configs and silently disable the
    # migration for every pre-v2.5 install. Users who disable new categories on
    # fresh install must have that choice survive a config reload.
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    cfg["known_categories"] = list(ALL_CATEGORIES)
    return cfg


def save_config(cfg):
    """Atomic write (temp file + os.replace) so a torn write (two Settings
    clicks racing, or a crash mid-save) can never leave invalid JSON on disk
    for load_config to trip over.

    When `config sync` is on, CONFIG_PATH is a symlink into iCloud Drive —
    resolve it first, because os.replace() onto the symlink itself would
    silently swap it for a plain local file and end syncing on the first
    settings change."""
    target = CONFIG_PATH
    try:
        if target.is_symlink():
            target = Path(os.path.realpath(target))
    except OSError:
        pass
    _atomic_write_json(target, cfg)


def _icloud_config_dir():
    """Where the synced config lives. iCloud Drive needs no entitlements, so
    this works for a plain CLI + non-sandboxed app. MACCLEANER_ICLOUD_DIR
    overrides for tests."""
    override = os.environ.get("MACCLEANER_ICLOUD_DIR")
    if override:
        return Path(override)
    return HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "MacCleaner"


def run_config_sync(action, json_mode=False):
    """`config sync on|off|status` — keep config.json in iCloud Drive with a
    symlink at CONFIG_PATH, so the CLI, the app, and the launchd agents all
    read and write the shared copy unchanged.

    Rules: `on` adopts an existing iCloud copy as the shared truth (that is
    the point — a second Mac joining sync wants the first Mac's settings),
    backing the local file up beside itself first. `off` is local-only: the
    symlink becomes a real file with the current content, and the iCloud
    copy stays for other Macs. The config file is ~1 KB, small enough that
    iCloud's "optimize storage" eviction effectively never takes it."""
    icloud_cfg = _icloud_config_dir() / "config.json"

    def is_enabled():
        try:
            return (CONFIG_PATH.is_symlink()
                    and os.path.realpath(CONFIG_PATH) == os.path.realpath(icloud_cfg))
        except OSError:
            return False

    def state():
        return {"enabled": is_enabled(),
                "config_path": str(CONFIG_PATH),
                "icloud_path": str(icloud_cfg)}

    if action == "status":
        st = state()
        if json_mode:
            print(json.dumps({"version": VERSION, "sync": st}, indent=2))
        else:
            print(f"Config sync: {'on' if st['enabled'] else 'off'}")
            print(f"  config:  {st['config_path']}")
            print(f"  iCloud:  {st['icloud_path']}")
        return st

    if action == "on":
        if is_enabled():
            print("Config sync is already on.")
            return state()
        icloud_cfg.parent.mkdir(parents=True, exist_ok=True)
        local_is_file = CONFIG_PATH.exists() and not CONFIG_PATH.is_symlink()
        if icloud_cfg.exists():
            # iCloud copy wins; keep the local settings recoverable beside it.
            if local_is_file:
                shutil.copy2(CONFIG_PATH, CONFIG_PATH.parent / (CONFIG_PATH.name + ".pre-sync.bak"))
                CONFIG_PATH.unlink()
        elif local_is_file:
            shutil.move(str(CONFIG_PATH), str(icloud_cfg))
        else:
            _atomic_write_json(icloud_cfg, load_config())
        if CONFIG_PATH.is_symlink():
            CONFIG_PATH.unlink()
        os.symlink(str(icloud_cfg), str(CONFIG_PATH))
        print(f"Config sync on — settings now live in iCloud Drive:\n  {icloud_cfg}")
        return state()

    if action == "off":
        if not is_enabled():
            print("Config sync is not on — nothing to do.")
            return state()
        current = load_config()
        CONFIG_PATH.unlink()
        _atomic_write_json(CONFIG_PATH, current)
        print("Config sync off — settings are local again "
              "(the iCloud copy remains for other Macs).")
        return state()

    raise ValueError(f"unknown sync action: {action}")


# ── Size helpers ───────────────────────────────────────────────────────────────
def get_size(path: Path) -> int:
    """Return size in bytes of a path (file or directory)."""
    if not path.exists():
        return 0
    try:
        result = subprocess.run(
            # -x: never descend into a mounted volume beneath `path`. Without
            # it du counts a mounted disk image's CONTENTS on top of the image
            # file itself -- the same bytes twice. That flaw made one directory
            # measure 106 GB against a real 11 GB when checked by hand. No
            # current target path contains a mount point, but nothing checks
            # that when a target is added, so measure correctly by default.
            ["du", "-skx", str(path)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            return int(result.stdout.split()[0]) * 1024
    except Exception:
        pass
    return 0


def fmt_size(bytes_val: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


# ── Notifications ──────────────────────────────────────────────────────────────
def _escape_applescript(text: str) -> str:
    """Escape a Python string for embedding in an AppleScript string literal."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _notify_argv(title: str, message: str) -> list:
    """The osascript argv for a notification. Separate from _notify so tests can
    assert the constructed command as data instead of posting a real alert."""
    script = (f'display notification "{_escape_applescript(message)}" '
              f'with title "{_escape_applescript(title)}"')
    return ["osascript", "-e", script]


def _notify(title: str, message: str) -> bool:
    """Post a macOS notification. Returns True if it was posted.

    Never raises: a missing or failing osascript warns on stderr and leaves the
    caller's exit code alone — a notification failure must not turn a
    successful clean into a failed one. Attribution is generic until the app is
    signed; the SwiftUI app posts properly attributed notifications itself."""
    try:
        r = subprocess.run(_notify_argv(title, message),
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            print(f"Warning: notification failed: {r.stderr.strip()}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"Warning: could not post notification: {e}", file=sys.stderr)
        return False


def disk_free() -> str:
    """Human-readable disk summary, built from disk_stats() (shutil, the data
    volume) so it always agrees with the disk_stats key shipped alongside it.
    Previously this shelled out to `df -h /` separately, which reports the
    read-only system volume -- a different number that could visibly
    contradict disk_stats in the same JSON payload (and, since v2.6, next to
    each other in the app's disk ring)."""
    try:
        ds = disk_stats()
        return (f"Used: {fmt_size(ds['used_bytes'])} / {fmt_size(ds['total_bytes'])} "
                f"({ds['percent_used']:.0f}%)")
    except Exception:
        return "unknown"


def disk_stats() -> dict:
    usage = shutil.disk_usage("/")
    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "used_bytes": usage.used,
        "percent_used": round(usage.used / usage.total * 100, 1),
    }


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


# ── Estimate parsers for cmd-based targets ─────────────────────────────────────
def _parse_brew_estimate(output):
    m = re.search(r'free approximately ([0-9.]+)\s*(KB|MB|GB|TB)', output)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2)
    return int(val * {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[unit])


# `docker system df`'s TYPE column has multi-word entries ("Local Volumes",
# "Build Cache"), which shifts a naive whitespace-split column index -- the
# old parser read SIZE instead of RECLAIMABLE for one-word rows and dropped
# two-word rows entirely. Local Volumes is deliberately excluded here even
# once parsed correctly: docker-prune's cmd never passes --volumes (volumes
# commonly hold real data, e.g. databases, so removing them isn't "safe"),
# so counting volume space as reclaimable would advertise bytes this
# specific safe target can never actually free.
_DOCKER_DF_RECLAIMABLE_TYPES = ("Images", "Containers", "Build Cache")


def _parse_docker_estimate(output):
    total = 0
    for line in output.strip().splitlines()[1:]:
        line = line.strip()
        type_name = next((t for t in _DOCKER_DF_RECLAIMABLE_TYPES if line.startswith(t)), None)
        if type_name is None:
            continue
        rest = line[len(type_name):].split()
        # rest = [TOTAL, ACTIVE, SIZE, RECLAIMABLE, ...optional "(NN%)"]
        if len(rest) < 4:
            continue
        m = re.match(r'([0-9.]+)(B|KB|MB|GB|TB)', rest[3])
        if m:
            val, unit = float(m.group(1)), m.group(2)
            total += int(val * {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[unit])
    return total


def _parse_du_estimate(output):
    try:
        return int(output.split()[0]) * 1024
    except Exception:
        return 0


def _parse_conda_estimate(output):
    """Sum the '(N.N GB)' sizes in `conda clean --all --dry-run` output."""
    total = 0
    for m in re.finditer(r'\(([0-9.]+)\s*(B|KB|MB|GB|TB)\)', output):
        val, unit = float(m.group(1)), m.group(2)
        total += int(val * {"B": 1, "KB": 1024, "MB": 1024**2,
                            "GB": 1024**3, "TB": 1024**4}[unit])
    return total


def _run_estimate(estimate_cmd, parser):
    try:
        r = subprocess.run(estimate_cmd, shell=True, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return 0
        return {
            "brew_dry_run": _parse_brew_estimate,
            "docker_df":    _parse_docker_estimate,
            "du_path":      _parse_du_estimate,
            "conda_dry_run": _parse_conda_estimate,
        }.get(parser, lambda _: 0)(r.stdout)
    except Exception:
        return 0


# ── Target definitions ─────────────────────────────────────────────────────────
def get_targets(config, all_categories=False):
    skip = [Path(os.path.expanduser(p)) for p in config.get("skip_paths", [])]
    enabled = set(ALL_CATEGORIES) if all_categories else set(config["enabled_categories"])
    targets = []

    def add(category, tid, label, path, safe=True, cmd=None,
            estimate_cmd=None, estimate_parser=None, desc="", empty_only=False,
            paths=None):
        """`paths` is the multi-path form (consumed by _target_paths), for a
        target whose regenerable content sits in two or more sibling
        subdirectories that must be named individually because their shared
        parent also holds live state -- see spotify-browser-cache. It is
        mutually exclusive with `path`/glob patterns."""
        if category not in enabled:
            return
        p, pattern, multi = None, None, None
        if paths is not None:
            multi = [Path(os.path.expanduser(str(x))) for x in paths]
            # Same prefix rule as the single-path branch, applied per entry so
            # a skip_paths entry can retire part of a multi-path target
            # without silently taking the whole thing with it.
            multi = [x for x in multi
                     if not any(str(x).startswith(str(s)) for s in skip)]
            if not multi:
                return
        elif path is not None:
            if "*" in str(path):
                pattern = os.path.expanduser(str(path))
            else:
                p = Path(os.path.expanduser(str(path)))
                if any(str(p).startswith(str(s)) for s in skip):
                    return
        targets.append({
            "id": tid,
            "category": category,
            "label": label,
            "description": desc,
            "path": p,
            "paths": multi,
            "glob": pattern,
            "skip": [str(s) for s in skip] if pattern else [],
            "safe": safe,
            "cmd": cmd,
            "estimate_cmd": estimate_cmd,
            "estimate_parser": estimate_parser,
            "empty_only": empty_only,
        })

    # Xcode
    add("xcode", "xcode-derived-data", "Xcode DerivedData", "~/Library/Developer/Xcode/DerivedData",
        desc="Intermediate build products; Xcode rebuilds them on demand")
    # Xcode's default DerivedData is the target just above; a project pointed
    # at a custom location (Xcode > Settings > Locations, or -derivedDataPath)
    # writes to ~/Library/Developer/<Name>DerivedData instead, which nothing
    # here used to cover. On a real machine that meant 6.2 GB of pure build
    # output sitting beside a "190 MB reclaimable" report. The glob matches
    # direct children of ~/Library/Developer only, so it cannot reach the
    # default location one level deeper inside Xcode/ -- no double-counting.
    add("xcode", "xcode-derived-data-custom", "Xcode DerivedData (custom locations)",
        "~/Library/Developer/*DerivedData*",
        desc="Build output from projects using a custom DerivedData path; "
             "rebuilt on the next build, exactly like the default location")
    add("xcode", "xcode-previews", "Xcode Previews", "~/Library/Developer/Xcode/UserData/Previews",
        desc="SwiftUI preview build products")
    add("xcode", "xcode-ios-device-support", "Xcode iOS DeviceSupport", "~/Library/Developer/Xcode/iOS DeviceSupport",
        desc="Debug symbols for previously connected iOS devices; re-extracted on next connect")
    add("xcode", "xcode-watchos-device-support", "Xcode watchOS DeviceSupport", "~/Library/Developer/Xcode/watchOS DeviceSupport",
        desc="Debug symbols for previously connected watches")
    add("xcode", "xcode-archives", "Xcode Archives", "~/Library/Developer/Xcode/Archives", safe=False,
        desc="App Store submission archives — keep if you need dSYMs for released builds")
    add("xcode", "xcode-simulator-unavailable", "Simulator unavailable devices", None,
        cmd="xcrun simctl delete unavailable 2>/dev/null || true",
        desc="Simulators whose runtime is no longer installed")
    add("xcode", "xcode-simulator-runtimes", "Simulator runtimes", "~/Library/Developer/CoreSimulator/Volumes", safe=False,
        desc="Downloaded simulator OS images — re-download from Xcode if needed")
    add("xcode", "xcode-cache", "Xcode cache", "~/Library/Caches/com.apple.dt.Xcode",
        desc="Xcode's general cache")
    add("xcode", "xcode-coresimulator-caches", "CoreSimulator caches", "~/Library/Developer/CoreSimulator/Caches",
        desc="Simulator dyld caches; regenerated on next simulator boot")
    add("xcode", "swiftpm-cache", "SwiftPM cache", "~/Library/Caches/org.swift.swiftpm",
        desc="Swift Package Manager checkouts and manifests; re-fetched on next resolve")
    add("xcode", "carthage-cache", "Carthage cache", "~/Library/Caches/org.carthage.CarthageKit",
        desc="Carthage dependency builds")

    # Xcode (v2.1 addition — place with the other xcode adds)
    add("xcode", "xcode-doc-cache", "Xcode documentation cache", "~/Library/Developer/Xcode/DocumentationCache",
        desc="Downloaded documentation indexes; re-fetched on demand")
    add("xcode", "xcodebuildmcp-workspaces", "XcodeBuildMCP workspaces",
        "~/Library/Developer/XcodeBuildMCP",
        desc="Workspace scratch data written by the XcodeBuildMCP tool; regenerated on next build")

    # Docker
    add("docker", "docker-prune", "Docker unused data", None,
        cmd="docker system prune -f --filter 'until=168h' 2>/dev/null || true",
        estimate_cmd="docker system df 2>/dev/null",
        estimate_parser="docker_df",
        desc="Unused containers/images/networks older than a week (docker system prune)")

    # Node
    add("node", "npm-cache", "npm cache", "~/.npm/_cacache",
        desc="npm package download cache; re-fetched on demand")
    add("node", "npx-cache", "npx cache", "~/.npm/_npx",
        desc="Packages downloaded by npx one-off runs")
    add("node", "pnpm-store", "pnpm store", None,
        cmd="pnpm store prune 2>/dev/null || true",
        estimate_cmd="pnpm store path 2>/dev/null | xargs du -sk 2>/dev/null",
        estimate_parser="du_path",
        desc="Unreferenced packages in the pnpm content-addressable store")
    add("node", "yarn-cache", "yarn cache", "~/.yarn/cache",
        desc="Yarn package cache")
    add("node", "bun-cache", "bun cache", "~/.bun/install/cache",
        desc="Bun package download cache")
    add("node", "deno-cache", "deno cache", "~/Library/Caches/deno",
        desc="Deno remote module and npm caches")
    add("node", "node-gyp-cache", "node-gyp cache", "~/Library/Caches/node-gyp",
        desc="Node.js headers downloaded for native module builds")

    # Node (v2.1 additions)
    add("node", "yarn-global-cache", "Yarn classic global cache", "~/Library/Caches/Yarn",
        desc="Yarn v1 global package cache (separate from ~/.yarn/cache)")
    add("node", "npm-logs", "npm logs", "~/.npm/_logs",
        desc="npm debug log files")

    # Node (v2.10 addition)
    add("node", "typescript-cache", "TypeScript server cache", "~/Library/Caches/typescript",
        desc="Auto-downloaded @types packages the TS language server caches for editors; "
             "re-fetched on demand")

    # Python
    add("python", "pip-cache", "pip cache", "~/Library/Caches/pip",
        desc="pip download/wheel cache")
    add("python", "uv-cache", "uv cache", "~/Library/Caches/uv",
        desc="uv package cache; re-fetched on demand")
    add("python", "poetry-cache", "poetry cache", "~/Library/Caches/pypoetry",
        desc="Poetry package cache")
    add("python", "ruff-cache", "ruff cache", "~/Library/Caches/ruff",
        desc="Ruff lint cache")
    add("python", "pyenv-shims", "pyenv shims cache", "~/.pyenv/shims", safe=False,
        desc="pyenv shim binaries — regenerate with 'pyenv rehash' after deleting")

    # Python (v2.1 additions)
    add("python", "conda-clean", "Conda caches", None,
        cmd="conda clean --all --yes 2>/dev/null || true",
        estimate_cmd="conda clean --all --dry-run 2>/dev/null",
        estimate_parser="conda_dry_run",
        desc="Unused conda packages, tarballs, and index caches (conda clean --all)")

    # AI models
    add("ai", "huggingface-hub", "Hugging Face hub cache", "~/.cache/huggingface", safe=False,
        desc="Downloaded models/datasets — can be very large; re-downloaded on demand")
    add("ai", "torch-hub", "PyTorch hub cache", "~/.cache/torch", safe=False,
        desc="Downloaded PyTorch models and weights")
    add("ai", "ollama-models", "Ollama models", "~/.ollama/models", safe=False,
        desc="Local Ollama models — re-pull with 'ollama pull' if needed")

    # AI models (v2.1 additions)
    add("ai", "lm-studio-models", "LM Studio models", "~/.lmstudio/models", safe=False,
        desc="Downloaded LM Studio models — re-download from the app if needed")
    add("ai", "whisper-models", "Whisper models", "~/.cache/whisper", safe=False,
        desc="Downloaded OpenAI Whisper models")
    add("ai", "codex-sessions", "Codex session transcripts", "~/.codex/sessions", safe=False,
        desc="OpenAI Codex CLI conversation history — delete only if you don't need past sessions")
    add("ai", "codex-archived-sessions", "Codex archived sessions", "~/.codex/archived_sessions", safe=False,
        desc="Codex CLI sessions already archived by the tool — old conversation history")

    # AI (v2.10 additions)
    add("ai", "ollama-updates", "Ollama update downloads", "~/Library/Caches/ollama/updates",
        desc="An Ollama app update already downloaded and waiting to install; discarding it "
             "just means the updater fetches it again. Distinct from ollama-models, "
             "which holds the models themselves")
    add("ai", "codex-sparkle-updates", "Codex update downloads",
        "~/Library/Caches/com.openai.codex/org.sparkle-project.Sparkle",
        desc="A Codex/ChatGPT app update Sparkle has already downloaded and extracted, staged "
             "to install on next quit. Removing it discards that pending update — no data is "
             "lost and it re-downloads on the next check; best done while the app is closed")
    add("ai", "codex-runtimes", "Codex downloaded runtimes", "~/.cache/codex-runtimes", safe=False,
        desc="Executable runtimes Codex downloads and runs from — review-level because a running "
             "Codex session may be executing out of this directory right now, so removing it can "
             "break work in flight, not just delay the next start")
    add("ai", "antigravity-browser-profile", "Antigravity browser profile",
        "~/.gemini/antigravity-browser-profile", safe=False,
        desc="Browser profile for Gemini Antigravity — holds live session state and logins, "
             "not just cache; deleting signs you out of anything it was holding")

    # IDE / editors
    add("ide", "vscode-cache", "VS Code cache", "~/Library/Application Support/Code/Cache",
        desc="VS Code network/render cache")
    add("ide", "vscode-cached-data", "VS Code cached data", "~/Library/Application Support/Code/CachedData",
        desc="VS Code extension host cached data; rebuilt on launch")
    add("ide", "jetbrains-caches", "JetBrains caches", "~/Library/Caches/JetBrains",
        desc="IntelliJ/PyCharm/WebStorm caches and indexes; rebuilt on next open")

    # Browsers
    add("browsers", "arc-cache", "Arc cache", "~/Library/Caches/Arc",
        desc="Arc browser cache")
    add("browsers", "brave-cache", "Brave cache", "~/Library/Caches/BraveSoftware",
        desc="Brave browser cache")
    add("browsers", "edge-cache", "Edge cache", "~/Library/Caches/Microsoft Edge",
        desc="Microsoft Edge cache")
    add("browsers", "firefox-cache", "Firefox cache", "~/Library/Caches/Firefox/Profiles/*/cache2",
        desc="Firefox per-profile web cache")

    # App caches
    add("caches", "claude-cache", "Claude app cache", "~/Library/Application Support/Claude/Cache",
        desc="Claude desktop app cache")
    add("caches", "claude-vm-bundles", "Claude VM bundles", "~/Library/Application Support/Claude/vm_bundles",
        desc="Claude desktop VM images")
    add("caches", "claude-code-cache", "Claude Code Cache", "~/Library/Application Support/Claude/Code Cache",
        desc="Claude desktop code cache")
    add("caches", "claude-gpu-cache", "Claude GPU Cache", "~/Library/Application Support/Claude/GPUCache",
        desc="Claude desktop GPU shader cache")
    add("caches", "cursor-cache", "Cursor cache", "~/Library/Application Support/Cursor/Cache",
        desc="Cursor editor cache")
    add("caches", "chrome-cache", "Chrome cache", "~/Library/Application Support/Google/Chrome/Default/Cache",
        desc="Chrome default-profile web cache")
    add("caches", "chrome-optimization-model-store", "Chrome on-device models",
        "~/Library/Application Support/Google/Chrome/optimization_guide_model_store", safe=False,
        desc="Chrome's downloaded on-device ML prediction models — Chrome indexes them in "
             "'Local State', which this tool does not touch, so recovery is unverified")
    add("caches", "chrome-optimization-hint-cache", "Chrome optimization hints",
        "~/Library/Application Support/Google/Chrome/*/optimization_guide_hint_cache_store",
        desc="Chrome's per-profile page-optimization hint cache; regenerated on demand")
    add("caches", "spotify-cache", "Spotify cache", "~/Library/Application Support/Spotify/Data",
        desc="Spotify streamed-audio cache")
    add("caches", "slack-cache", "Slack cache", "~/Library/Application Support/Slack/Cache",
        desc="Slack app cache")
    add("caches", "slack-service-worker", "Slack service worker cache", "~/Library/Application Support/Slack/Service Worker/CacheStorage",
        desc="Slack service-worker cache storage")
    add("caches", "discord-cache", "Discord cache", "~/Library/Application Support/discord/Cache",
        desc="Discord app cache")
    add("caches", "electron-cache", "Electron cache", "~/Library/Caches/electron",
        desc="Electron framework download cache (used by app builds)")
    add("caches", "playwright-browsers", "Playwright browsers", "~/Library/Caches/ms-playwright", safe=False,
        desc="Playwright browser binaries — re-download with 'npx playwright install'")
    add("caches", "puppeteer-cache", "Puppeteer cache", "~/.cache/puppeteer", safe=False,
        desc="Puppeteer downloaded Chromium builds")

    # App caches (v2.1 additions — place with the other caches adds)
    add("caches", "cypress-cache", "Cypress binary cache", "~/Library/Caches/Cypress", safe=False,
        desc="Cypress browser/runner binaries — re-download with 'cypress install'")
    add("caches", "teams-cache", "Microsoft Teams cache", "~/Library/Caches/com.microsoft.teams2",
        desc="Microsoft Teams (new) app cache")
    add("caches", "zoom-updater", "Zoom installer cache", "~/Library/Application Support/zoom.us/AutoUpdater",
        desc="Downloaded Zoom update installers")
    add("caches", "terraform-plugin-cache", "Terraform plugin cache", "~/.terraform.d/plugin-cache",
        desc="Cached provider plugins; re-downloaded on 'terraform init'")
    add("caches", "expo-cache", "Expo cache", "~/.expo/cache",
        desc="Expo CLI download cache")

    # App caches (v2.10 additions — keep above general-caches, which is the
    # broad review-level sweep of the same directory)
    add("caches", "chrome-http-cache", "Chrome HTTP cache", "~/Library/Caches/Google/Chrome",
        desc="Chrome's on-disk HTTP and compiled-code cache, per profile; refills as you browse. "
             "Holds no cookies, history, or logins — those live under Application Support")
    # NOT ~/Library/Caches/com.spotify.client itself: that root is the desktop
    # app's embedded-Chromium profile, holding Default/Login Data,
    # Default/Cookies, Browser/Cookies, Local State and the WidevineCdm DRM
    # module beside the caches. Naming the two regenerable subdirectories
    # recovers ~83% of the space with none of that risk, which is what lets
    # this stay safe=True (an unattended --yes would otherwise sign the user
    # out of the in-app browser and drop the DRM module).
    add("caches", "spotify-browser-cache", "Spotify browser cache", None,
        paths=["~/Library/Caches/com.spotify.client/Browser/Cache",
               "~/Library/Caches/com.spotify.client/Data"],
        desc="Spotify desktop's embedded-browser cache and its streamed-audio cache; "
             "both re-fetched on demand. Offline downloads are not here — they live "
             "under Application Support and are never touched")
    add("caches", "clang-module-cache", "Clang module cache", "~/.cache/clang",
        desc="Precompiled C/C++/ObjC/Swift module cache used by clang and SourceKit; "
             "rebuilt on the next compile")
    add("caches", "electron-updater-pending", "Electron app update downloads",
        "~/Library/Caches/*electron-updater/pending",
        desc="Update installers Electron apps have already downloaded and staged. "
             "Removing one discards an update that was ready to install — the app "
             "re-downloads it on its next check, but a pending 'restart to update' "
             "may need re-triggering. Only matches apps whose updater directory ends "
             "in 'electron-updater' (Squirrel and ToDesktop apps are unaffected)")

    add("caches", "general-caches", "General app caches", "~/Library/Caches", safe=False, empty_only=True,
        desc="Everything in ~/Library/Caches — broad; review before deleting")

    # Homebrew
    add("homebrew", "brew-cleanup", "Homebrew cache", None,
        cmd="brew cleanup --prune=all 2>/dev/null || true",
        estimate_cmd="brew cleanup --dry-run 2>/dev/null",
        estimate_parser="brew_dry_run",
        desc="Old downloads and outdated formula versions (brew cleanup)")
    add("homebrew", "brew-autoremove", "Homebrew unused deps", None,
        cmd="brew autoremove 2>/dev/null || true",
        desc="Formulae installed as dependencies that nothing needs anymore")

    # Go
    add("go", "go-module-cache", "Go module cache", "~/go/pkg/mod",
        desc="Downloaded Go modules; re-fetched on demand")
    add("go", "go-pkg-cache", "Go pkg cache", "~/go/pkg/cache",
        desc="Legacy Go package cache location")
    add("go", "go-build-cache", "Go build cache", "~/Library/Caches/go-build",
        desc="Go compiler build cache; rebuilt on demand")

    # Rust / Cargo
    add("rust", "cargo-registry", "Cargo registry", "~/.cargo/registry",
        desc="Downloaded crate sources and archives")
    add("rust", "cargo-git", "Cargo git cache", "~/.cargo/git",
        desc="Cargo git dependency checkouts")

    # Rust (v2.1 addition — place with the other rust adds)
    add("rust", "sccache-cache", "sccache cache", "~/Library/Caches/Mozilla.sccache",
        desc="Shared compilation cache; rebuilt on demand")

    # Rust (v2.10 addition)
    add("rust", "rustup-downloads", "rustup download cache", "~/.rustup/downloads",
        desc="Partial or interrupted toolchain downloads (rustup normally deletes these "
             "after unpacking, so it's usually empty); avoid during a 'rustup update'. "
             "The installed toolchains under ~/.rustup/toolchains are never touched")

    # Ruby
    add("ruby", "gem-cleanup", "Ruby gem cleanup", None,
        cmd="gem cleanup 2>/dev/null || true",
        desc="Old versions of installed gems (gem cleanup)")

    # CocoaPods
    add("cocoapods", "cocoapods-cache", "CocoaPods cache", "~/Library/Caches/CocoaPods",
        desc="Downloaded pod specs and archives")

    # Gradle / Android
    add("gradle", "gradle-caches", "Gradle caches", "~/.gradle/caches",
        desc="Gradle dependency and build caches")

    # Maven
    add("maven", "maven-repo", "Maven local repo", "~/.m2/repository", safe=False,
        desc="All Maven artifacts — offline builds break until re-downloaded")

    # System
    add("system", "trash", "Empty Trash", "~/.Trash", safe=False, empty_only=True,
        desc="Permanently delete everything in the Trash")
    add("system", "ios-backups", "iOS device backups", "~/Library/Application Support/MobileSync/Backup", safe=False,
        desc="Local iPhone/iPad backups — only delete if backed up to iCloud or elsewhere")

    # Flutter / Dart
    add("flutter", "dart-pub-cache", "Dart pub cache", "~/.pub-cache",
        desc="Dart & Flutter packages; repopulated by 'dart pub get' / 'flutter pub get'")

    # PHP
    add("php", "composer-cache", "Composer cache", "~/.composer/cache",
        desc="Composer package download cache")

    # VMs / container runtimes
    add("vms", "colima-vm", "Colima VM", "~/.colima", safe=False,
        desc="Colima VM disks — deleting removes the VM including its containers and images")
    add("vms", "vagrant-boxes", "Vagrant boxes", "~/.vagrant.d/boxes", safe=False,
        desc="Downloaded Vagrant base boxes — large re-downloads")
    add("vms", "minikube-cache", "minikube cache", "~/.minikube/cache",
        desc="Cached minikube images and binaries; re-fetched on demand")

    # Logs (dynamic)
    threshold = config.get("log_threshold_mb", 100) * 1024 * 1024
    log_dir = HOME / "Library/Logs"
    if "logs" in enabled and log_dir.exists():
        for item in sorted(log_dir.iterdir()):
            size = get_size(item)
            if size > threshold:
                targets.append({
                    "id": f"log-{slugify(item.name)}",
                    "category": "logs",
                    "label": f"Log: {item.name}",
                    "description": f"Oversized log folder (>{config.get('log_threshold_mb', 100)} MB)",
                    "path": item,
                    "glob": None,
                    "safe": True,
                    "cmd": None,
                    "estimate_cmd": None,
                    "estimate_parser": None,
                    "empty_only": False,
                    "size": size,
                })

    return targets


def collect_targets(config, all_categories=False, categories=None, target_ids=None):
    """Static targets plus dynamic scanner targets (tmp, simulators).
    scan/clean/dry-run call this; `categories` deliberately keeps calling
    get_targets() — dynamic per-dir IDs are unstable and the completions'
    live-ID pipeline must not see them.

    categories/target_ids are optional SELECTION HINTS from the CLI
    invocation (--category / --targets): when a hint proves a scanner's
    output can't be selected, the scanner is skipped — a targeted
    `clean --targets npm-cache` shouldn't pay two simctl calls and a /tmp
    walk (popover one-click clean latency, v2.6). Hints never widen
    anything: enabled_categories still gates as before, and hints=None
    behaves exactly like pre-2.6."""
    targets = get_targets(config, all_categories=all_categories)
    enabled = set(ALL_CATEGORIES) if all_categories else set(config["enabled_categories"])

    def _wanted(cat, prefix):
        if cat not in enabled:
            return False
        if categories is not None and cat not in set(categories):
            return False
        if target_ids is not None and not any(str(t).startswith(prefix) for t in target_ids):
            return False
        return True

    if _wanted("tmp", "tmp-"):
        targets += tmp_to_targets(scan_tmp_artifacts(config))
    if _wanted("simulators", "simulator-"):
        targets += scan_simulator_targets(config)
    if _wanted("leftovers", "leftover-"):
        targets += app_leftovers_to_targets(scan_app_leftovers(config))
    return targets


def _target_paths(t):
    """Concrete filesystem paths for a target (glob patterns expanded).

    Glob matches are filtered against skip_paths here because the prefix
    check in add() can only see the pattern, not its expansions."""
    if t.get("glob"):
        skip = t.get("skip", [])
        return [Path(p) for p in sorted(globmod.glob(t["glob"]))
                if not any(p.startswith(s) for s in skip)]
    if t.get("paths"):
        return t["paths"]
    if t.get("path"):
        return [t["path"]]
    return []


# ── Core actions ───────────────────────────────────────────────────────────────
def measure_targets(targets):
    """Attach size and existence info to each target (parallel du)."""
    def measure(t):
        if "size" in t and t["size"] is not None:
            t.setdefault("exists", True)
            return
        paths = _target_paths(t)
        existing = [p for p in paths if p.exists()]
        if existing:
            t["size"] = sum(get_size(p) for p in existing)
            t["exists"] = True
        elif t.get("precomputed_bytes") is not None:
            t["size"] = t["precomputed_bytes"]
            t["exists"] = True
        elif t.get("cmd"):
            t["size"] = _run_estimate(t["estimate_cmd"], t.get("estimate_parser")) if t.get("estimate_cmd") else 0
            t["exists"] = True  # command-based targets are always runnable
        else:
            t["size"] = 0
            t["exists"] = False

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(measure, targets))
    return targets


def _safe_to_delete(path: Path) -> bool:
    """Only paths strictly inside the user's home are deletable.

    Resolves the PARENT directory (not the leaf) before the containment
    check: path.absolute() alone is purely lexical, so a target that is
    lexically inside $HOME but reaches its parent through a symlinked
    ancestor (e.g. a glob match under a symlinked cache subdirectory) could
    smuggle a delete through to somewhere physically outside $HOME. The
    leaf itself is deliberately left unresolved -- _remove() unlinks a
    symlink leaf rather than following it, so a symlink whose own directory
    entry is inside $HOME stays safe to remove even if it points somewhere
    outside $HOME; only the link is ever removed, never its target."""
    try:
        p = Path(path)
        if p.name in ("", ".."):
            return False
        rp = p.parent.resolve() / p.name
    except Exception:
        return False
    home = HOME.resolve()
    if str(rp).rstrip("/") in ("", "/", str(home).rstrip("/")):
        return False
    return str(rp).startswith(str(home) + os.sep)


def _tmp_scan_path_allowed(path):
    """The single, narrow carve-out to the home-only rule: a path is
    deletable outside $HOME only when it is a direct child of the tmp scan
    root, OR one level below that (resolved, so /tmp symlinking to
    /private/tmp is handled) — and delete_target additionally requires the
    target to carry the tmp_scan marker that only
    scan_tmp_artifacts()/tmp_to_targets() set. There is deliberately no
    config key that widens this.

    The grandchild level exists because 2.14.0's nested scan offers the
    build tree INSIDE a workspace (/private/tmp/<repo>-<task-id>/derived)
    rather than the workspace itself, deliberately, so the run logs and
    .xcresults beside it survive. Without this it offered those targets and
    then refused every one of them at delete time as "outside home" —
    reporting reclaimable space it could never reclaim. Exactly two levels:
    depth is what bounds the blast radius here, and nothing generates a
    deeper path."""
    try:
        rp = Path(path).resolve()
        root = TMP_SCAN_ROOT.resolve()
    except OSError:
        return False
    if rp == root:
        return False
    return rp.parent == root or rp.parent.parent == root


def _remove(path: Path):
    if path.is_symlink():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _move_to_trash(path: Path):
    trash = HOME / ".Trash"
    trash.mkdir(exist_ok=True)
    dest = trash / path.name
    counter = 0
    # Must be unique: shutil.move into an existing dir would nest inside it
    while dest.exists() or dest.is_symlink():
        counter += 1
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = trash / f"{path.name}-maccleaner-{stamp}-{counter}"
    shutil.move(str(path), str(dest))


def delete_target(t, mode="rm"):
    """Delete a target. Returns (bytes_freed, error_message_or_None)."""
    if t.get("cmd"):
        try:
            # cmd strings are either static literals from get_targets() (need
            # `|| true` and pipes) or assembled by scanners (scan_simulator_
            # targets) exclusively from simctl identifiers that passed
            # _SIMCTL_UDID_RE/_SIMCTL_RUNTIME_ID_RE — never raw user input,
            # so shell=True is safe here
            subprocess.run(t["cmd"], shell=True, capture_output=True, timeout=600)
            return 0, None  # Can't easily measure cmd-based targets
        except Exception as e:
            return 0, str(e)

    # The Trash target must hard-delete (moving Trash to Trash is a no-op)
    if t.get("id") == "trash":
        mode = "rm"

    freed = 0
    errors = []
    for path in _target_paths(t):
        if not path.exists() and not path.is_symlink():
            continue
        if not _safe_to_delete(path):
            if not (t.get("tmp_scan") and _tmp_scan_path_allowed(path)):
                errors.append(f"refused (outside home): {path}")
                continue
        freed += get_size(path)
        try:
            if t.get("empty_only"):
                for child in list(path.iterdir()):
                    if _safe_to_delete(child):
                        _move_to_trash(child) if mode == "trash" else _remove(child)
            elif mode == "trash":
                _move_to_trash(path)
            else:
                _remove(path)
        except Exception as e:
            errors.append(f"{path}: {e}")
    return freed, ("; ".join(errors) if errors else None)


# ── Config commands ─────────────────────────────────────────────────────────────
def cmd_config_set_category(cfg, action, category):
    if category not in ALL_CATEGORIES:
        print(f"Unknown category '{category}'. Valid: {', '.join(ALL_CATEGORIES)}", file=sys.stderr)
        sys.exit(1)
    cats = cfg["enabled_categories"]
    if action == "enable" and category not in cats:
        cats.append(category)
        save_config(cfg)
        print(f"Enabled '{category}'.")
    elif action == "disable" and category in cats:
        cats.remove(category)
        save_config(cfg)
        print(f"Disabled '{category}'.")
    else:
        print(f"Category '{category}' already {'enabled' if action == 'enable' else 'disabled'}.")


def cmd_config_set_key(cfg, key, value):
    if key not in DEFAULT_CONFIG:
        print(f"Unknown config key '{key}'. Valid: {', '.join(DEFAULT_CONFIG)}", file=sys.stderr)
        sys.exit(1)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value
    cfg[key] = parsed
    save_config(cfg)
    print(f"Set {key} = {json.dumps(parsed)}")


# ── TUI checklist ───────────────────────────────────────────────────────────────
def run_tui_clean(targets):
    """Interactive curses-based checklist. Returns (selected_list, confirmed)."""
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return None, False

    import curses

    # Default: safe items checked, review items unchecked
    checked = [t["safe"] for t in targets]

    def _tui(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)   # safe + selected
        curses.init_pair(2, curses.COLOR_YELLOW, -1)  # review
        curses.init_pair(3, curses.COLOR_CYAN, -1)    # cursor highlight
        curses.init_pair(4, curses.COLOR_WHITE, -1)   # normal

        cursor = 0
        confirmed = False

        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            header = " MacCleaner — Space=toggle  a=all  n=none  Enter=confirm  q=cancel"
            stdscr.addstr(0, 0, header[:w-1], curses.A_BOLD)
            stdscr.addstr(1, 0, "─" * (w - 1))

            list_start = 2
            list_end = h - 3
            visible = list_end - list_start
            offset = max(0, cursor - visible + 1)

            for i, t in enumerate(targets[offset:offset + visible]):
                idx = i + offset
                row = list_start + i
                if row >= list_end:
                    break

                mark = "x" if checked[idx] else " "
                cat = t["category"][:8].ljust(8)
                label = t["label"][:32].ljust(32)
                size_str = (fmt_size(t["size"]) if t.get("size")
                            else "~unknown" if t.get("estimate_cmd")
                            else "cmd-based")
                size_str = size_str[:10].rjust(10)
                review = "  REVIEW" if not t["safe"] else ""
                line = f" [{mark}] {cat}  {label}  {size_str}{review}"

                if idx == cursor:
                    attr = curses.color_pair(3) | curses.A_BOLD
                elif checked[idx] and t["safe"]:
                    attr = curses.color_pair(1)
                elif not t["safe"]:
                    attr = curses.color_pair(2)
                else:
                    attr = curses.color_pair(4)

                try:
                    stdscr.addstr(row, 0, line[:w-1], attr)
                except curses.error:
                    pass

            selected_count = sum(checked)
            total_bytes = sum(t.get("size", 0) for t, c in zip(targets, checked) if c)
            footer = f" Selected: {selected_count}/{len(targets)}  Est. to free: {fmt_size(total_bytes)}"
            try:
                stdscr.addstr(h - 2, 0, "─" * (w - 1))
                stdscr.addstr(h - 1, 0, footer[:w-1], curses.A_BOLD)
            except curses.error:
                pass

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP, ord('k')):
                cursor = max(0, cursor - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                cursor = min(len(targets) - 1, cursor + 1)
            elif key == ord(' '):
                checked[cursor] = not checked[cursor]
            elif key == ord('a'):
                checked[:] = [True] * len(targets)
            elif key == ord('n'):
                checked[:] = [False] * len(targets)
            elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
                confirmed = True
                break
            elif key in (ord('q'), 27):  # q or Esc
                break

        return confirmed

    try:
        confirmed = curses.wrapper(_tui)
    except curses.error:
        return None, False

    if not confirmed:
        return [], False

    selected = [t for t, c in zip(targets, checked) if c]
    return selected, True


# ── Output modes ───────────────────────────────────────────────────────────────
def show_welcome():
    if RICH:
        console.print(Panel(
            "[bold]🧹 MacCleaner[/bold] — Free up space on your Mac\n\n"
            "  [bold cyan]maccleaner scan[/bold cyan]           — see what can be cleaned\n"
            "  [bold cyan]maccleaner clean[/bold cyan]          — start cleaning (interactive)\n"
            "  [bold cyan]maccleaner projects[/bold cyan]       — find stale build artifacts in project folders\n"
            "  [bold cyan]maccleaner report[/bold cyan]         — show cleanup history\n"
            "  [bold cyan]maccleaner doctor[/bold cyan]         — check environment health\n\n"
            "  [dim]maccleaner clean --yes                — auto-clean all safe items[/dim]\n"
            "  [dim]maccleaner clean --targets npm-cache  — clean specific targets[/dim]\n"
            "  [dim]maccleaner scan --category xcode      — scope to one category[/dim]\n"
            "  [dim]maccleaner scan --json                — machine-readable (see AGENTS.md)[/dim]",
            title=f"Welcome — v{VERSION}",
            border_style="cyan"
        ))
    else:
        print(f"\n{'='*56}")
        print(f"🧹 MacCleaner v{VERSION} — Free up space on your Mac")
        print(f"{'='*56}")
        print("  maccleaner scan       — see what can be cleaned")
        print("  maccleaner clean      — start cleaning (interactive)")
        print("  maccleaner projects   — find stale build artifacts")
        print("  maccleaner report     — show cleanup history")
        print("  maccleaner doctor     — check environment health")
        print("  maccleaner scan --json  — machine-readable output")
        print(f"{'='*56}\n")


def print_scan(targets, show_all=False):
    targets = measure_targets(targets)
    total = reclaimable_total(targets)
    visible = [t for t in targets
               if show_all or t["size"] > 0 or t.get("cmd")]

    if RICH:
        table = Table(title="🧹 MacCleaner Scan", show_lines=False)
        table.add_column("Category", style="cyan", width=10)
        table.add_column("Item", style="white")
        table.add_column("ID", style="dim")
        table.add_column("Size", style="yellow", justify="right")
        table.add_column("Safe?", justify="center")

        for t in sorted(visible, key=lambda x: x["size"], reverse=True):
            size_str = (fmt_size(t["size"]) if t["size"]
                        else "~unknown" if t.get("estimate_cmd")
                        else "cmd-based")
            safe_str = "✅" if t["safe"] else "⚠️"
            table.add_row(t["category"], t["label"], t["id"], size_str, safe_str)

        console.print(table)
        hidden = len(targets) - len(visible)
        hidden_note = f"\n[dim]{hidden} empty/not-installed targets hidden — use --all to show[/dim]" if hidden else ""
        console.print(Panel(
            f"[bold green]Total reclaimable: {fmt_size(total)}[/bold green]\n"
            f"[dim]Current disk: {disk_free()}[/dim]{hidden_note}\n\n"
            f"[bold]→ Run [cyan]maccleaner clean[/cyan] to start cleaning[/bold]",
            title="Summary"
        ))
    else:
        print(f"\n{'='*72}")
        print(f"MacCleaner Scan — {disk_free()}")
        print(f"{'='*72}")
        for t in sorted(visible, key=lambda x: x["size"], reverse=True):
            size_str = (fmt_size(t["size"]) if t["size"]
                        else "~unknown" if t.get("estimate_cmd")
                        else "cmd")
            safe = "safe" if t["safe"] else "REVIEW"
            print(f"  [{t['category']:9}] {t['label']:<38} {size_str:>10}  {safe}")
        hidden = len(targets) - len(visible)
        if hidden:
            print(f"\n  ({hidden} empty/not-installed targets hidden — use --all to show)")
        print(f"\n  Total reclaimable: {fmt_size(total)}")
        print(f"\n  → Run 'maccleaner clean' to start cleaning")
        print(f"{'='*72}\n")


def reclaimable_total(targets):
    """Bytes a user could actually free, counting each byte once.

    A plain sum over targets double-counts: 27 targets sit inside
    `general-caches` (the review-level sweep of all of ~/Library/Caches), so
    their bytes appeared in both. On a real machine that overstated the
    headline "total reclaimable" by 2.5 GB. Nobody can free the same byte
    twice, so a target whose every path lies inside another target's path
    contributes nothing extra and is skipped.

    cmd-based targets (brew cleanup, docker prune) have no path and always
    count -- there is nothing to nest them inside."""
    owned = []
    for t in targets:
        owned.extend(str(p) for p in _target_paths(t))
    total = 0
    for t in targets:
        paths = [str(p) for p in _target_paths(t)]
        if paths and all(
            any(p != q and p.startswith(q.rstrip(os.sep) + os.sep) for q in owned)
            for p in paths
        ):
            continue  # fully contained in another target -- already counted
        total += t.get("size") or 0
    return total


def scan_json(targets, extra=None):
    targets = measure_targets(targets)
    total = reclaimable_total(targets)
    output = {
        "version": VERSION,
        "timestamp": datetime.datetime.now().isoformat(),
        "disk": disk_free(),
        "disk_stats": disk_stats(),
        "total_reclaimable_bytes": total,
        "total_reclaimable_human": fmt_size(total),
        "targets": [
            {
                "id": t["id"],
                "category": t["category"],
                "label": t["label"],
                "description": t["description"],
                "size_bytes": t["size"],
                "size_human": fmt_size(t["size"]),
                "safe": t["safe"],
                "exists": t.get("exists", False),
            }
            for t in sorted(targets, key=lambda x: x["size"], reverse=True)
        ]
    }
    if extra:
        output.update(extra)
    print(json.dumps(output, indent=2))


def run_clean(targets, auto_approve=False, mode="rm", json_mode=False, explicit=False,
              snapshot_scope="partial", notify=False):
    """Clean targets. explicit=True means the selection was made via --targets."""
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print

    targets = measure_targets(targets)
    # Drop targets with nothing to clean (keep cmd-based, which are always runnable)
    targets = [t for t in targets if t.get("exists") or t.get("cmd")]
    total_freed = 0
    results = []

    # Sort: safe items first, largest first within each group
    safe_targets = sorted([t for t in targets if t["safe"]], key=lambda x: x["size"], reverse=True)
    review_targets = sorted([t for t in targets if not t["safe"]], key=lambda x: x["size"], reverse=True)
    ordered = safe_targets + review_targets

    say(f"\n🧹 MacCleaner — {disk_free()}\n")

    def do_delete(t):
        nonlocal total_freed
        freed, err = delete_target(t, mode=mode)
        total_freed += freed
        status = "error" if err else ("trashed" if mode == "trash" and not t.get("cmd") else "deleted")
        results.append({"id": t["id"], "label": t["label"], "freed": freed, "status": status,
                        **({"error": err} if err else {})})
        return freed, err

    def mark_skipped(t):
        results.append({"id": t["id"], "label": t["label"], "freed": 0, "status": "skipped"})

    use_prompt_loop = True
    if not auto_approve and not explicit and not json_mode:
        selected, confirmed = run_tui_clean(ordered)
        if selected is None:
            pass  # TUI unavailable, fall through to y/N loop
        elif not confirmed:
            say("Cancelled.")
            return 0, results
        else:
            use_prompt_loop = False
            selected_ids = {t["id"] for t in selected}
            for t in ordered:
                if t["id"] in selected_ids:
                    size_str = fmt_size(t["size"]) if t["size"] else "~unknown"
                    say(f"  ✅ Deleting: {t['label']} ({size_str})")
                    do_delete(t)
                else:
                    mark_skipped(t)

    if use_prompt_loop:
        # y/N loop — auto_approve, explicit --targets, --json, or TUI fallback
        for t in ordered:
            size_str = (fmt_size(t["size"]) if t["size"]
                        else "~unknown" if t.get("estimate_cmd")
                        else "cmd-based")
            safe_label = "" if t["safe"] else " ⚠️  REVIEW BEFORE DELETING"

            # --yes deletes safe targets; explicitly named targets are consented
            if auto_approve and (t["safe"] or explicit):
                say(f"  ✅ Cleaning: {t['label']} ({size_str})")
                _, err = do_delete(t)
                if err:
                    say(f"    ⚠️  {err}")
            elif auto_approve:
                mark_skipped(t)  # review target under --yes without explicit selection
            else:
                prompt_str = f"  Delete [{t['category']}] {t['label']} ({size_str}){safe_label}? [y/N] "
                try:
                    ans = input(prompt_str).strip().lower()
                except EOFError:
                    mark_skipped(t)
                    continue
                if ans == "y":
                    _, err = do_delete(t)
                    say(f"    → {'Error: ' + err if err else 'Deleted ✓'}")
                else:
                    mark_skipped(t)
                    say(f"    → Skipped")

    write_log(total_freed, results)

    if snapshot_scope == "full":
        cleaned = {r["id"] for r in results if r["status"] in ("deleted", "trashed")}
        remaining = [t for t in targets if t["id"] not in cleaned]
        record_snapshot(*snapshot_fields(remaining))
    else:
        record_snapshot()

    if notify and load_config().get("notifications", True):
        cleaned = sum(1 for r in results if r["status"] in ("deleted", "trashed"))
        # write_log already ran, so the 7-day digest includes this run — the
        # weekly scheduled clean's notification doubles as the weekly report.
        _notify(*_clean_notification(total_freed, cleaned))

    if json_mode:
        print(json.dumps({
            "version": VERSION,
            "timestamp": datetime.datetime.now().isoformat(),
            "delete_mode": mode,
            "freed_bytes": total_freed,
            "freed_human": fmt_size(total_freed),
            "disk_after": disk_free(),
            "items": results,
        }, indent=2))
    else:
        say(f"\n{'='*50}")
        say(f"  Total freed: {fmt_size(total_freed)}")
        say(f"  Disk now:    {disk_free()}")
        say(f"{'='*50}\n")

    return total_freed, results


def run_dry_run(targets, mode="rm", json_mode=False):
    """Resolve exactly what a real clean of `targets` would delete. Deletes
    nothing, writes no report.log entry, records no snapshot."""
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print
    targets = measure_targets(targets)
    targets = [t for t in targets if t.get("exists") or t.get("cmd")]
    items, total = [], 0

    for t in sorted(targets, key=lambda x: x.get("size") or 0, reverse=True):
        if t.get("cmd"):
            size = t.get("size") or 0
            items.append({"id": t["id"], "label": t["label"], "freed": size,
                          "status": "would-run", "cmd": t["cmd"], "paths": []})
            total += size
            continue
        paths = []
        for p in _target_paths(t):
            if not p.exists() and not p.is_symlink():
                continue
            if t.get("empty_only"):
                # Guarded the same way delete_target guards the equivalent loop:
                # a PermissionError previewing one target's children must not
                # abort the whole preview when the real delete would tolerate it.
                try:
                    children = list(p.iterdir())
                except (PermissionError, FileNotFoundError, OSError):
                    continue
                paths.extend(c for c in sorted(children) if _safe_to_delete(c))
            # Same carve-out delete_target applies: a tmp_scan-marked target's
            # direct-child-of-TMP_SCAN_ROOT path previews correctly too, so
            # dry-run actually resolves what a real clean would delete.
            elif _safe_to_delete(p) or (t.get("tmp_scan") and _tmp_scan_path_allowed(p)):
                paths.append(p)
        entries = [{"path": str(p), "size_bytes": get_size(p)} for p in paths]
        size = sum(e["size_bytes"] for e in entries)
        total += size
        items.append({"id": t["id"], "label": t["label"], "freed": size,
                      "status": "would-delete", "paths": entries})

    if json_mode:
        print(json.dumps({
            "version": VERSION,
            "timestamp": datetime.datetime.now().isoformat(),
            "dry_run": True,
            "delete_mode": mode,
            "freed_bytes": total,
            "freed_human": fmt_size(total),
            "disk_after": disk_free(),
            "items": items,
        }, indent=2))
    else:
        say("\n🧪 Dry run — nothing will be deleted\n")
        for it in items:
            say(f"  {it['label']} ({fmt_size(it['freed'])})")
            if it.get("cmd"):
                say(f"      would run: {it['cmd']}")
            for e in it["paths"]:
                say(f"      would delete: {e['path']} ({fmt_size(e['size_bytes'])})")
        say(f"\n  Would free: {fmt_size(total)}\n")
    return total, items


# ── Stale project artifacts ─────────────────────────────────────────────────────
ARTIFACT_MANIFESTS = {
    "node_modules":  ["package.json"],
    ".venv":         ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"],
    "venv":          ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"],
    "target":        ["Cargo.toml"],
    "build":         ["build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"],
    "Pods":          ["Podfile"],
    ".next":         ["package.json"],
    ".nuxt":         ["package.json"],
    ".turbo":        ["package.json"],
    ".parcel-cache": ["package.json"],
    ".pytest_cache": [],
    ".mypy_cache":   [],
    ".ruff_cache":   [],
}
PROJECT_SCAN_MAX_DEPTH = 5

# ── AI-session and /tmp build artifacts ───────────────────────────────────────
TMP_SCAN_ROOT = Path(os.environ.get("MACCLEANER_TMP_ROOT", "/private/tmp"))
# Active AI-session scratch dirs — never offered even when old (Claude Code
# keeps per-session scratchpads under /private/tmp/claude-<uid>/).
TMP_ACTIVE_PREFIXES = ("claude-",)
TMP_CLONE_MANIFESTS = {
    "package.json", "Cargo.toml", "pyproject.toml", "go.mod", "Gemfile",
    "pubspec.yaml", "composer.json", "Package.swift", "pom.xml",
    "build.gradle", "build.gradle.kts", "requirements.txt",
}
TMP_CLONE_ARTIFACTS = {
    "build", "Build", ".build", "DerivedData", "node_modules", "target",
    "dist", ".next", "Pods", ".venv", "venv",
}

# ── Storage Insights (read-only, never wired into the delete pipeline) ────────
STORAGE_INSIGHTS_MIN_BYTES = 100 * 1024 * 1024
STORAGE_INSIGHTS_MAX_RESULTS = 100
# Widened in 2.13.0 from the original three. ~/Library is the single largest
# thing on a typical developer Mac (85 GB on the machine this was built
# against, against ~35 GB of Documents), and /Applications is where the
# biggest individual items live -- neither was reachable before, so the
# "largest items" view could not see most of the disk.
STORAGE_INSIGHTS_ROOTS_DEFAULT = ":".join([
    f"{HOME}/Documents", f"{HOME}/Downloads", f"{HOME}/Desktop",
    f"{HOME}/Library", f"{HOME}/Applications", "/Applications",
])
_STORAGE_INSIGHTS_SKIP_DIRS = set(ARTIFACT_MANIFESTS) | TMP_CLONE_ARTIFACTS
# Directory suffixes macOS treats as a single opaque document/app, not a
# folder. Reported whole and never descended into: /Applications holds ZERO
# loose files over the size floor but a dozen multi-GB .app bundles, so a
# file-only scan sees nothing there at all. Listing the binaries inside an
# app would also invite deleting one, which breaks the app.
STORAGE_INSIGHTS_BUNDLE_SUFFIXES = (
    ".app", ".framework", ".xcarchive", ".photoslibrary", ".imovielibrary",
    ".fcpbundle", ".tvlibrary", ".theater", ".logicx", ".band", ".sparsebundle",
    ".pkg", ".bundle", ".plugin", ".kext", ".qlgenerator", ".mdimporter",
)


def _disk_bytes(st):
    """Bytes a file actually occupies on disk.

    `st_size` is the APPARENT size and lies for sparse files: Docker's
    Docker.raw reports 1.0 TB apparent against 9.97 GB allocated, which would
    put a phantom terabyte at the top of a "largest items" list on a 460 GB
    disk. `st_blocks` is always 512-byte units regardless of filesystem block
    size, and is what `du` and Finder report. It is also correct (smaller)
    for APFS-compressed files, where apparent size overstates too."""
    return st.st_blocks * 512


def _is_bundle_dir(name):
    return name.endswith(STORAGE_INSIGHTS_BUNDLE_SUFFIXES)


def _bundle_size(root):
    """Total bytes of a bundle, stat-only.

    Deliberately NOT `du`/get_size(): this keeps the whole scan inside the
    same os.scandir/os.stat contract the rest of scan_storage_insights uses,
    which is what makes the "never opens file contents" guarantee (and so the
    "never triggers an iCloud download of an evicted file" guarantee)
    provable by mocking builtins.open. Symlinks are never followed and never
    counted -- a symlink inside a bundle usually points back into the same
    bundle (Frameworks/Versions/Current), so following them would both
    double-count and risk a cycle. Unreadable subtrees contribute 0 rather
    than aborting: a partial size for one app beats losing the whole scan."""
    total, stack = 0, [root]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                else:
                    total += _disk_bytes(e.stat(follow_symlinks=False))
            except OSError:
                continue
    return total


def _storage_insights_roots():
    raw = os.environ.get("MACCLEANER_STORAGE_INSIGHTS_ROOTS", STORAGE_INSIGHTS_ROOTS_DEFAULT)
    return [Path(p) for p in raw.split(":") if p]


# ── Storage map (whole-disk browser) ───────────────────────────────────────────
# Deliberately separate from the get_targets()/collect_targets()/delete_target()
# family: entries here have no `id` and no `safe` flag, are never passed to
# delete_target(), and may sit anywhere on the disk including outside $HOME.
# This is a READ-ONLY view. It exists because MacCleaner's cleanup engine is
# scoped to rebuildable caches inside $HOME, which makes it structurally unable
# to answer "where did my 400 GB go" -- most of a real disk is applications,
# personal files and system data that a cache cleaner should never touch but a
# user still needs to see.

# Longest prefix wins, so more specific paths must come first.
def _storage_category(path):
    """Bucket a path for display. Presentation only -- nothing downstream
    changes behaviour based on this, so an unrecognised path is just 'other'."""
    p = str(Path(path))
    home = str(Path.home())
    rules = [
        (home + "/Library/Developer", "developer"),
        (home + "/Library/Caches", "caches"),
        (home + "/Library/Containers", "appdata"),
        (home + "/Library/Application Support", "appdata"),
        (home + "/Library", "appdata"),
        (home + "/Documents", "documents"),
        (home + "/Desktop", "documents"),
        (home + "/Downloads", "documents"),
        (home + "/Movies", "media"),
        (home + "/Music", "media"),
        (home + "/Pictures", "media"),
        ("/Applications", "applications"),
        (home + "/Applications", "applications"),
        ("/Library/Developer", "developer"),
        ("/Library/Caches", "caches"),
        ("/System", "system"),
        ("/Library", "system"),
        ("/private", "system"),
        ("/usr", "system"),
    ]
    best = ("other", -1)
    for prefix, cat in rules:
        if (p == prefix or p.startswith(prefix + os.sep)) and len(prefix) > best[1]:
            best = (cat, len(prefix))
    return best[0]


def _du_children(root):
    """`du -xkd 1` -- immediate children plus the root's own total, in bytes.

    The `-x` is the whole point and must never be dropped: without it du
    descends into any mounted disk image beneath `root` and counts that
    image's *contents* on top of the image file itself, reporting the same
    bytes twice. Measured by hand without -x, /Library/Developer/CoreSimulator
    on a real machine read 106 GB when it actually held 11 GB -- a threefold
    overstatement that sent a whole storage investigation down the wrong path.
    """
    try:
        r = subprocess.run(["du", "-xkd", "1", str(root)],
                           capture_output=True, text=True, timeout=600)
    except Exception as e:
        print("Warning: could not measure %s: %s" % (root, e), file=sys.stderr)
        return {}, 0
    # du reports partial results plus stderr noise for unreadable subtrees;
    # a non-zero exit is normal there and the readable numbers are still good.
    sizes, total = {}, 0
    root_s = str(root)
    for line in r.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        try:
            kb = int(parts[0])
        except ValueError:
            continue
        path = parts[1]
        if path == root_s:
            total = kb * 1024
        else:
            sizes[path] = kb * 1024
    return sizes, total


def scan_storage_map(root=None, min_bytes=0):
    """One level of children beneath `root`, largest first, with real sizes.

    Read-only: it stats and measures, and never deletes, moves or opens file
    contents. Unreadable subtrees are reported at whatever size du managed
    rather than failing the whole scan."""
    root = Path(os.path.expanduser(str(root))) if root else Path.home()
    empty = {"root": str(root), "total_bytes": 0, "total_human": fmt_size(0),
             "category": _storage_category(root), "children": []}
    if not root.exists() or not root.is_dir():
        return empty

    sizes, total = _du_children(root)
    children = []
    for path, size in sizes.items():
        if size < min_bytes:
            continue
        p = Path(path)
        # Only direct children -- du -d 1 shouldn't return anything deeper,
        # but a path with an embedded newline would confuse the parse above.
        if p.parent != root:
            continue
        children.append({
            "path": path,
            "name": p.name,
            "size_bytes": size,
            "size_human": fmt_size(size),
            "kind": "dir" if p.is_dir() and not p.is_symlink() else
                    ("link" if p.is_symlink() else "file"),
            "category": _storage_category(p),
        })

    # du -d 1 lists directories only; loose files in the root are measured here.
    listed = {c["path"] for c in children}
    try:
        for entry in os.scandir(root):
            if entry.path in listed:
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if size < min_bytes:
                continue
            children.append({
                "path": entry.path,
                "name": entry.name,
                "size_bytes": size,
                "size_human": fmt_size(size),
                "kind": "link" if entry.is_symlink() else "file",
                "category": _storage_category(Path(entry.path)),
            })
    except OSError:
        pass

    children.sort(key=lambda c: -c["size_bytes"])
    return {"root": str(root), "total_bytes": total, "total_human": fmt_size(total),
            "category": _storage_category(root), "children": children}


def scan_storage_insights(config):
    """Read-only scan of the configured roots (default ~/Documents,
    ~/Downloads, ~/Desktop) for files >= STORAGE_INSIGHTS_MIN_BYTES.

    stat()-only -- never opens file contents, so it can never trigger an
    iCloud download of an evicted file. Skips known dev-artifact
    directories (the same names scan_projects treats as noise) and never
    descends into a .app bundle. Iterative (explicit stack), not
    recursive, so an unusually deep directory tree can't hit Python's
    recursion limit. Returns up to STORAGE_INSIGHTS_MAX_RESULTS entries,
    largest first.

    Symlink handling deliberately draws a line between trusted entry
    points and untrusted discovered paths, the same distinction
    _safe_to_delete draws between a resolved parent and an unresolved
    leaf:
      - Configured roots (the 3 defaults, or whatever
        MACCLEANER_STORAGE_INSIGHTS_ROOTS names) are trusted, user/test
        -controlled entry points and ARE followed if they are themselves
        symlinks -- via the plain `r.is_dir()` filter below, which
        follows symlinks by default. This is required for macOS's
        "Desktop & Documents Folders" iCloud sync, which replaces
        ~/Documents and ~/Desktop with symlinks into
        ~/Library/Mobile Documents/com~apple~CloudDocs/...; without
        following the root symlink the scanner would silently return
        nothing for the most common way people use those folders.
      - Everything discovered DURING the walk (any directory or file
        found via os.scandir while traversing) is untrusted and is never
        followed -- both symlinked directories and symlinked files hit
        during the walk are skipped entirely (`e.is_symlink()` checked
        first, before any other classification, for every entry).

    This function is architecturally outside the delete pipeline: no
    `safe` field, no category, no target id, never passed to
    get_targets()/collect_targets()/delete_target()."""
    hits = []
    stack = [r for r in _storage_insights_roots() if r.is_dir()]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name in _STORAGE_INSIGHTS_SKIP_DIRS:
                        continue
                    if _is_bundle_dir(e.name):
                        # One row for the whole bundle; never descend.
                        size = _bundle_size(e.path)
                        if size >= STORAGE_INSIGHTS_MIN_BYTES:
                            st = e.stat(follow_symlinks=False)
                            hits.append({"path": Path(e.path), "size_bytes": size,
                                         "mtime": st.st_mtime, "is_bundle": True})
                        continue
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    size = _disk_bytes(st)
                    if size >= STORAGE_INSIGHTS_MIN_BYTES:
                        hits.append({"path": Path(e.path), "size_bytes": size,
                                     "mtime": st.st_mtime, "is_bundle": False})
            except OSError:
                continue
    hits.sort(key=lambda h: h["size_bytes"], reverse=True)
    return hits[:STORAGE_INSIGHTS_MAX_RESULTS]


def _relative_days(mtime):
    """Coarse relative-time bucket for a stat() mtime -- 'today',
    'yesterday', or 'N days ago'. Deliberately simple (no weeks/months):
    this is only used for the plain-text CLI table; the app formats the
    raw mtime its own JSON carries with RelativeDateTimeFormatter."""
    days = (time.time() - mtime) / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


def show_storage_map(root=None, min_bytes=0, json_mode=False):
    r = scan_storage_map(root, min_bytes=min_bytes)
    if json_mode:
        print(json.dumps({"version": VERSION, **r}, indent=2))
        return r
    if not r["children"]:
        print(f"Nothing to show in {r['root']}.")
        return r
    if RICH:
        table = Table(title=f"{r['root']}  —  {r['total_human']}", show_lines=False)
        table.add_column("Size", style="green", justify="right")
        table.add_column("Name", style="cyan")
        table.add_column("Kind", style="magenta")
        table.add_column("Category", style="yellow")
        for c in r["children"]:
            table.add_row(c["size_human"], c["name"], c["kind"], c["category"])
        console.print(table)
    else:
        print(f"\n{'='*66}")
        print(f"{r['root']}  —  {r['total_human']}")
        print(f"{'='*66}")
        for c in r["children"]:
            marker = "/" if c["kind"] == "dir" else ("@" if c["kind"] == "link" else " ")
            name = c["name"] if len(c["name"]) <= 40 else c["name"][:37] + "..."
            print(f"{c['size_human']:>10}  {name}{marker:<2} [{c['category']}]")
        print()
    return r


def show_storage_insights(config, json_mode=False):
    hits = scan_storage_insights(config)
    if json_mode:
        print(json.dumps({
            "version": VERSION,
            "roots": [str(r) for r in _storage_insights_roots()],
            "min_bytes": STORAGE_INSIGHTS_MIN_BYTES,
            "entries": [
                {"path": str(h["path"]), "size_bytes": h["size_bytes"],
                 "size_human": fmt_size(h["size_bytes"]), "mtime": h["mtime"],
                 "is_bundle": bool(h.get("is_bundle"))}
                for h in hits
            ],
        }, indent=2))
        return
    floor_human = fmt_size(STORAGE_INSIGHTS_MIN_BYTES)
    home = str(HOME)
    roots_text = ", ".join(
        str(r).replace(home, "~") for r in _storage_insights_roots())
    if not hits:
        print(f"No files found at or above {floor_human} in {roots_text}.")
        return
    if RICH:
        table = Table(title=f"Largest Items (>={floor_human})", show_lines=False)
        table.add_column("Size", style="green", justify="right")
        table.add_column("Kind", style="magenta")
        table.add_column("Path", style="cyan")
        table.add_column("Modified", style="yellow")
        for h in hits:
            table.add_row(fmt_size(h["size_bytes"]),
                          "app/bundle" if h.get("is_bundle") else "file",
                          str(h["path"]).replace(home, "~"),
                          _relative_days(h["mtime"]))
        console.print(table)
    else:
        print(f"\n{'='*60}")
        print(f"Large Files (>={floor_human})")
        print(f"{'='*60}")
        for h in hits:
            print(f"  {fmt_size(h['size_bytes']):>10}  {_relative_days(h['mtime']):>12}  {h['path']}")
        print(f"{'='*60}\n")

# ── Simulator dynamic targets ─────────────────────────────────────────────────
# Device UDIDs and runtime identifiers from `simctl ... -j` output end up
# interpolated into shell cmd strings (delete_target runs cmd targets with
# shell=True) — anything that fails these shapes is dropped rather than ever
# reaching a shell, so a malformed/hostile simctl response can't inject.
_SIMCTL_UDID_RE = re.compile(r"[0-9A-Fa-f-]{8,}\Z")
# Anchored to the real shapes genuine identifiers carry -- a bare
# character-class check let "all", "--outdated", "--unusable" (all real
# `xcrun simctl runtime delete` arguments, "all" deletes every runtime image
# on the machine) through as if they were valid runtime identifiers (F3).
#
# TWO shapes, because simctl's `runtime list -j` moved the goalposts: older
# Xcode put the reverse-DNS string in "identifier", while Xcode 26 puts an
# image UUID there and moves the reverse-DNS string to "runtimeIdentifier".
# Accepting only the first form meant every runtime failed validation and was
# silently dropped on current Xcode, so simulator-unused-runtimes could never
# fire at all -- indistinguishable, from the outside, from "nothing to clean".
# Both alternatives are still strictly shell-safe character sets.
_SIMCTL_RUNTIME_ID_RE = re.compile(
    r"(?:com\.apple\.CoreSimulator\.SimRuntime\.[A-Za-z0-9.-]+"
    r"|[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\Z")


def _git_info(project_dir):
    """Git activity signals for a project dir, or None when unknowable.

    Returns {"dirty": bool, "unpushed": bool}. A repo with no remotes counts
    as unpushed — its commits exist nowhere else. Any git failure (not a repo,
    git missing, timeout) degrades to None rather than blocking the scan."""
    def run(*args):
        # --no-optional-locks: don't take .git/index.lock or touch index mtimes —
        # a concurrent `git add`/`git commit` by the user must not fail because
        # a read-only scan is holding the lock (this is what editors do too).
        # -c core.fsmonitor=: a hostile checked-out repo can't use a repo-local
        # fsmonitor hook to execute code during this read-only scan.
        return subprocess.run(
            ["git", "-C", str(project_dir), "--no-optional-locks", "-c", "core.fsmonitor=", *args],
            capture_output=True, text=True, timeout=2)
    try:
        r = run("rev-parse", "--is-inside-work-tree")
        if r.returncode != 0 or r.stdout.strip() != "true":
            return None
        status = run("status", "--porcelain")
        if status.returncode != 0:
            return None
        rev_list = run("rev-list", "--count", "--branches", "--not", "--remotes")
        if rev_list.returncode != 0:
            return None
        dirty = bool(status.stdout.strip())
        count = rev_list.stdout.strip()
        return {"dirty": dirty, "unpushed": count not in ("", "0")}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _git_flagged(t):
    """True when a project target has uncommitted or unpushed work."""
    git = t.get("git")
    return bool(git and (git.get("dirty") or git.get("unpushed")))


def _filter_git_flagged(targets, bypass):
    """Exclude git-flagged (dirty/unpushed) project targets, unless `bypass`
    (an explicit --targets selection, which counts as consent). Shared by the
    `--yes` clean path and the `--dry-run` preview so they can never drift —
    both print the same stderr note naming each skipped project."""
    if bypass:
        return targets
    flagged = [t for t in targets if _git_flagged(t)]
    if flagged:
        print("Skipping projects with uncommitted or unpushed work "
              "(select explicitly with --targets to clean them):",
              file=sys.stderr)
        for t in flagged:
            print(f"  {t['id']}  {t['label']}", file=sys.stderr)
    return [t for t in targets if not _git_flagged(t)]


def scan_projects(config, roots=None, min_age_days=None):
    """Find stale build-artifact directories under the configured project roots."""
    root_strs = roots if roots else config.get("project_roots", [])
    roots = []
    for r in root_strs:
        p = Path(os.path.expanduser(r))
        if p.exists() and p.is_dir():
            roots.append(p)
    min_age = config.get("project_min_age_days", 30) if min_age_days is None else min_age_days
    now = time.time()
    found = {}

    def walk(dir_path, depth):
        if depth > PROJECT_SCAN_MAX_DEPTH:
            return
        try:
            entries = list(os.scandir(dir_path))
        except (PermissionError, FileNotFoundError, OSError):
            return
        names = {e.name for e in entries}
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = e.name
            if name == ".git":
                continue
            if name in ARTIFACT_MANIFESTS:
                required = ARTIFACT_MANIFESTS[name]
                if not required or any(m in names for m in required):
                    try:
                        age_days = int((now - e.stat(follow_symlinks=False).st_mtime) / 86400)
                    except OSError:
                        continue
                    if age_days >= min_age and e.path not in found:
                        found[e.path] = {
                            "path": e.path,
                            "kind": name,
                            "project": str(Path(e.path).parent),
                            "age_days": age_days,
                        }
                continue  # never descend into artifact-named dirs
            if name.startswith("."):
                continue
            walk(e.path, depth + 1)

    for root in roots:
        walk(root, 0)

    hits = list(found.values())
    with ThreadPoolExecutor(max_workers=8) as pool:
        sizes = list(pool.map(lambda h: get_size(Path(h["path"])), hits))
    for h, s in zip(hits, sizes):
        h["size_bytes"] = s
    if config.get("project_git_check", True):
        with ThreadPoolExecutor(max_workers=8) as pool:
            infos = list(pool.map(lambda h: _git_info(h["project"]), hits))
        for h, info in zip(hits, infos):
            h["git"] = info
    else:
        for h in hits:
            h["git"] = None
    hits.sort(key=lambda h: h["size_bytes"], reverse=True)
    return hits, [str(r) for r in roots], min_age


def projects_to_targets(hits):
    targets = []
    for h in hits:
        git = h.get("git")
        flags = [name for name, on in (("dirty", git and git.get("dirty")),
                                       ("unpushed", git and git.get("unpushed"))) if on]
        badge = "".join(f" [{f}]" for f in flags)
        rel = os.path.relpath(h["path"], str(HOME))
        targets.append({
            "id": f"project-{slugify(rel)}",
            "category": "projects",
            "label": f"{h['kind']} — {os.path.relpath(h['project'], str(HOME))}{badge}",
            "description": f"Stale {h['kind']} ({h['age_days']} days old)"
                           + (f" — git: {', '.join(flags)}" if flags else ""),
            "path": Path(h["path"]),
            "glob": None,
            "safe": False,
            "cmd": None,
            "estimate_cmd": None,
            "estimate_parser": None,
            "empty_only": False,
            "size": h["size_bytes"],
            "exists": True,
            "git": git,
        })
    return targets


def _classify_tmp_dir(p):
    """Classify a top-level /tmp dir by CONTENT (never by name).

    Returns "derived-data", "repo-clone", or None. Name-based matching was
    rejected in the v2.5 design: session dirs are named after projects
    ("underbark-pr74-...") and won't generalize across users."""
    try:
        if (p / "Build" / "Intermediates.noindex").is_dir():
            return "derived-data"
        logs = p / "Logs" / "Build"
        if logs.is_dir():
            try:
                with os.scandir(logs) as entries:
                    if any(f.name.endswith(".xcactivitylog") for f in entries):
                        return "derived-data"
            except OSError:
                pass
        # Build/ + Index.noindex/ plus one more Xcode-only marker. The old
        # rule additionally REQUIRED info.plist, which Xcode does not always
        # write under a custom -derivedDataPath -- a real 4.2 GB tree with
        # Build/, Index.noindex/, ModuleCache.noindex/, Logs/ and
        # SDKStatCaches.noindex/ was rejected for lacking that one file. The
        # combination below is still distinctive to DerivedData; no ordinary
        # source or document folder carries it.
        if (p / "Build").is_dir() and (p / "Index.noindex").is_dir():
            corroborating = ("info.plist", "ModuleCache.noindex",
                             "SDKStatCaches.noindex", "CompilationCache.noindex",
                             "Logs", "SourcePackages")
            if any((p / c).exists() for c in corroborating):
                return "derived-data"
        if (p / ".git").exists():
            try:
                names = {e.name for e in os.scandir(p)}
            except OSError:
                return None
            has_manifest = (names & TMP_CLONE_MANIFESTS) or any(
                n.endswith(".xcodeproj") for n in names)
            if has_manifest and (names & TMP_CLONE_ARTIFACTS):
                return "repo-clone"
    except OSError:
        return None
    return None


def _running_command_lines():
    """Command lines of every running process, or None if they can't be read.

    None means "could not ask", which callers must NOT treat as "nothing is
    running" -- see _path_is_in_use."""
    try:
        r = subprocess.run(["ps", "-axo", "command="],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.splitlines()


def _own_process_tree():
    """Command lines of this process and its ancestors.

    Excluded from the in-use check because asking about a path tends to put
    that path into our own command line -- observed for real, where checking
    `/private/tmp/<ws>/DerivedData` matched the very shell doing the asking
    and reported the tree busy with nothing using it. A check must not see
    its own reflection."""
    own, pid = set(), os.getpid()
    for _ in range(12):
        try:
            r = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)],
                               capture_output=True, text=True, timeout=5)
            line = r.stdout.strip()
            if not line:
                break
            ppid, _, cmd = line.partition(" ")
            own.add(cmd.strip())
            pid = int(ppid)
        except Exception:
            break
        if pid <= 1:
            break
    return own


def _path_is_in_use(path, commands, own=None):
    """True when a running process names `path` on its command line.

    Age is not proof of idleness: a nested write does not update the parent
    directory's mtime, so a workspace can read as weeks-stale at its top
    level while a build writes inside it. Observed live -- a DerivedData root
    whose mtime receded (68s, 76s, 84s ago across three samples) while
    xcodebuild held seven open handles beneath it and was writing a new
    .xcresult. A peer session read the lock state as idle and proposed
    deleting exactly that tree.

    Matching is on the path followed by a boundary character (or end), so
    /tmp/ws in a command line does not shield /tmp/ws2.

    `commands is None` means the process list was unreadable. That is not
    evidence of idleness, but failing closed there would hide every candidate
    on any machine where `ps` is restricted. These targets are review-only
    and never auto-cleaned, so the age gate stays the guard and this returns
    False -- the same posture the leftovers scanner takes when mdfind is
    unavailable."""
    if not commands:
        return False
    if own is None:
        own = _own_process_tree()
    p = str(path)
    for line in commands:
        if line.strip() in own:
            continue
        idx = line.find(p)
        while idx != -1:
            end = idx + len(p)
            if end == len(line) or not (line[end].isalnum() or line[end] in "-_."):
                return True
            idx = line.find(p, idx + 1)
    return False


def _nested_build_dirs(parent, cutoff, commands=None, own=None):
    """Build trees sitting one level inside `parent`, content-classified.

    Never name-matched: the tree that motivated this was called `derived`,
    and a folder merely NAMED DerivedData with no build shape must not
    qualify. The same min-age cutoff as the top level applies -- a build may
    be writing into it right now, and the real case was zero days old."""
    found = []
    try:
        entries = list(os.scandir(parent))
    except OSError:
        return found
    uid = os.getuid()
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
            if st.st_uid != uid or st.st_mtime > cutoff:
                continue
            if _classify_tmp_dir(Path(e.path)) != "derived-data":
                continue
            if _path_is_in_use(Path(e.path), commands, own):
                continue
            found.append(Path(e.path))
        except OSError:
            continue
    return found


def scan_tmp_artifacts(config):
    """Top-level-only scan of TMP_SCAN_ROOT for stale build junk.

    Guards (all mandatory, see the v2.5 design doc): min-age via
    tmp_min_age_days, symlinks never followed or classified, other-owner
    dirs skipped, active AI-session prefixes skipped, plain files skipped,
    skip_paths honored (AGENTS.md documents it as "never touch" -- the
    static get_targets()/add() path already drops any target under a
    skip prefix, and this scanner must match that, finding F1)."""
    min_age = config.get("tmp_min_age_days", 1)
    cutoff = time.time() - min_age * 86400
    skip = [Path(os.path.expanduser(p)) for p in config.get("skip_paths", [])]
    hits = []
    try:
        entries = list(os.scandir(TMP_SCAN_ROOT))
    except OSError:
        return hits
    uid = os.getuid()
    # Read once for the whole scan, not per candidate.
    commands = _running_command_lines()
    own_cmds = _own_process_tree()
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
            if any(e.name.startswith(pre) for pre in TMP_ACTIVE_PREFIXES):
                continue
            st = e.stat(follow_symlinks=False)
            if st.st_uid != uid or st.st_mtime > cutoff:
                continue
            resolved = Path(e.path).resolve()
            if any(resolved == s.resolve() or str(resolved).startswith(str(s.resolve()) + os.sep)
                   for s in skip):
                continue
            if _path_is_in_use(Path(e.path), commands, own_cmds):
                continue
            kind = _classify_tmp_dir(Path(e.path))
            if kind:
                hits.append({"path": Path(e.path), "kind": kind, "mtime": st.st_mtime})
                continue
            # The directory itself isn't build junk -- but AI-coding sessions
            # produce a working directory that CONTAINS a build tree beside
            # logs and test results worth keeping. Offer just the build tree.
            # Exactly one level: an unbounded walk of every tmp tree would be
            # slow and far likelier to surface something live.
            for child in _nested_build_dirs(Path(e.path), cutoff, commands, own_cmds):
                hits.append({"path": child, "kind": "derived-data",
                             "mtime": child.stat().st_mtime})
        except OSError:
            continue
    hits.sort(key=lambda h: str(h["path"]))
    return hits


def tmp_to_targets(hits):
    targets, seen = [], set()
    for h in hits:
        base = "tmp-" + slugify(h["path"].name)
        tid, n = base, 2
        while tid in seen:
            tid, n = "%s-%d" % (base, n), n + 1
        seen.add(tid)
        kind_desc = ("Xcode-style derived build products"
                     if h["kind"] == "derived-data"
                     else "Stale repo clone containing build artifacts")
        targets.append({
            "id": tid,
            "category": "tmp",
            "label": "/tmp: %s" % h["path"].name,
            "description": "%s; left in /private/tmp by a tool or AI session" % kind_desc,
            "path": h["path"],
            "glob": None,
            "skip": [],
            "safe": False,
            "cmd": None,
            "estimate_cmd": None,
            "estimate_parser": None,
            "empty_only": False,
            "tmp_scan": True,
        })
    return targets


def _simctl_json(args):
    """Run `xcrun simctl <args> -j` and parse JSON; None when simctl is
    missing (no Xcode), errors, or emits garbage — callers degrade to no
    targets, matching how a missing docker degrades."""
    try:
        out = subprocess.run(["xcrun", "simctl"] + args + ["-j"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout)
    except Exception:
        return None


def _parse_simctl_date(s):
    # "2026-08-09T00:00:00Z" or with offset; simctl emits ISO8601
    try:
        return datetime.datetime.strptime(
            s.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def scan_simulator_targets(config):
    """Dynamic review-only targets for stale simulator devices and unused
    runtime images. Deletion goes through simctl (raw rm corrupts its
    registry) — so these are cmd targets whose commands are assembled from
    simctl's own enumeration. simctl handles the system-domain runtime
    cryptexes in /Library/Developer that the engine could never touch.

    Device timestamp field: older Xcode/simctl emits "lastBootedAt"; newer
    versions renamed it "lastUsedAt" (observed on Xcode's current simctl) —
    both are checked, falling back to the device's dataPath mtime when
    neither is present.

    Every udid/identifier that ends up in a cmd string is validated against
    _SIMCTL_UDID_RE/_SIMCTL_RUNTIME_ID_RE first and silently dropped (not
    included in the target at all) if it doesn't match — simctl's own JSON
    is untrusted input from delete_target's shell=True point of view."""
    targets = []
    data = _simctl_json(["list", "devices"])
    if not data:
        return targets
    stale_days = config.get("simulator_stale_days", 30)
    cutoff = time.time() - stale_days * 86400
    stale, stale_bytes, used_runtimes = [], 0, set()
    for runtime_id, devs in data.get("devices", {}).items():
        for d in devs:
            udid = d.get("udid")
            if not udid or not _SIMCTL_UDID_RE.fullmatch(udid):
                continue
            used_runtimes.add(runtime_id)
            if d.get("state") == "Booted":
                continue
            ts = _parse_simctl_date(d.get("lastBootedAt") or d.get("lastUsedAt"))
            if ts is None:
                try:
                    ts = os.stat(d.get("dataPath", "")).st_mtime
                except OSError:
                    continue
            if ts < cutoff:
                stale.append(d)
                dp = d.get("dataPath")
                if dp:
                    try:
                        stale_bytes += get_size(Path(dp))
                    except OSError:
                        pass
    if stale:
        udids = " ".join(d["udid"] for d in stale)
        names = ", ".join(d.get("name", "?") for d in stale[:4])
        more = "" if len(stale) <= 4 else " +%d more" % (len(stale) - 4)
        targets.append({
            "id": "simulator-stale-devices", "category": "simulators",
            "label": "Stale simulator devices (%d)" % len(stale),
            "description": "Not booted in %dd: %s%s — deleted via simctl"
                           % (stale_days, names, more),
            "path": None, "glob": None, "skip": [], "safe": False,
            "cmd": "xcrun simctl delete %s 2>/dev/null || true" % udids,
            "estimate_cmd": None, "estimate_parser": None,
            "empty_only": False, "precomputed_bytes": stale_bytes,
        })
    rt = _simctl_json(["runtime", "list"])
    if rt:
        # simctl has shipped at least three runtime-list shapes across
        # versions: {"runtimes": [...]}, a bare top-level [...], and (the
        # shape observed from current Xcode) a bare dict keyed by runtime
        # UUID with no "runtimes" wrapper at all — {uuid: {...}, ...}.
        if isinstance(rt, list):
            runtimes = rt
        elif isinstance(rt, dict) and "runtimes" in rt:
            runtimes = rt["runtimes"]
            if isinstance(runtimes, dict):
                runtimes = list(runtimes.values())
        elif isinstance(rt, dict):
            runtimes = list(rt.values())
        else:
            runtimes = []
        unused = [r for r in runtimes
                  if isinstance(r, dict)
                  and r.get("runtimeIdentifier", r.get("identifier")) not in used_runtimes
                  and r.get("state") == "Ready"
                  and r.get("identifier")
                  and _SIMCTL_RUNTIME_ID_RE.fullmatch(r["identifier"])]
        if unused:
            ids = [r["identifier"] for r in unused]
            size = sum(int(r.get("sizeBytes") or 0) for r in unused)
            targets.append({
                "id": "simulator-unused-runtimes", "category": "simulators",
                "label": "Unused simulator runtimes (%d)" % len(ids),
                "description": "Runtime images with no simulator devices — re-downloadable in Xcode",
                "path": None, "glob": None, "skip": [], "safe": False,
                # "; "-joined with per-command suppression (not "&&") so one
                # failing delete doesn't short-circuit and skip every later
                # identifier; trailing "; true" keeps the overall exit 0.
                "cmd": "; ".join("xcrun simctl runtime delete %s 2>/dev/null" % i
                                 for i in ids) + "; true",
                "estimate_cmd": None, "estimate_parser": None,
                "empty_only": False, "precomputed_bytes": size,
            })
    return targets


def _git_label(h):
    git = h.get("git")
    if not git:
        return "—"
    flags = [f for f, on in (("dirty", git.get("dirty")),
                             ("unpushed", git.get("unpushed"))) if on]
    return ", ".join(flags) if flags else "clean"


def print_projects(hits, roots, min_age):
    total = sum(h["size_bytes"] for h in hits)
    if RICH:
        table = Table(title=f"📦 Stale project artifacts (≥{min_age} days old)", show_lines=False)
        table.add_column("Artifact", style="cyan")
        table.add_column("Project", style="white")
        table.add_column("Age", justify="right")
        table.add_column("Git", style="magenta")
        table.add_column("Size", style="yellow", justify="right")
        for h in hits:
            table.add_row(h["kind"], os.path.relpath(h["project"], str(HOME)),
                          f"{h['age_days']}d", _git_label(h), fmt_size(h["size_bytes"]))
        console.print(table)
        console.print(Panel(
            f"[bold green]Total: {fmt_size(total)}[/bold green] across {len(hits)} artifacts\n"
            f"[dim]Roots scanned: {', '.join(roots)}[/dim]\n\n"
            f"[bold]→ Run [cyan]maccleaner projects --clean[/cyan] to remove them[/bold]",
            title="Summary"
        ))
    else:
        print(f"\n{'='*72}")
        print(f"Stale project artifacts (≥{min_age} days old)")
        print(f"{'='*72}")
        for h in hits:
            proj = os.path.relpath(h["project"], str(HOME))
            print(f"  {h['kind']:<14} {proj:<36} {h['age_days']:>4}d {_git_label(h):>10} {fmt_size(h['size_bytes']):>10}")
        print(f"\n  Total: {fmt_size(total)} across {len(hits)} artifacts")
        print(f"  Roots scanned: {', '.join(roots)}")
        print(f"\n  → Run 'maccleaner projects --clean' to remove them")
        print(f"{'='*72}\n")


# ── Doctor ──────────────────────────────────────────────────────────────────────
def _launchd_is_loaded(label: str):
    """Tri-state: True (loaded), False (launchd says no such service), or
    None (could not ask).

    A plist file on disk merely means it was written, not that bootstrap/load
    succeeded or that it's still loaded (finding I1) — so this asks launchd.
    But launchctl can fail for reasons that say nothing about the agent:
    no binary on PATH, no Aqua/GUI session (the common case for anything
    running over ssh, from a sandbox, or under another launchd job), or a
    timeout. Returning False for those made `doctor` report a perfectly
    healthy weekly schedule as broken and send the user to reinstall it.

    Only `launchctl list <label>`'s documented "no such service" answer —
    exit 113, or the "Could not find service" message on older releases
    that exit 1 — is proof of not-loaded. Everything else is None.
    """
    try:
        r = subprocess.run(["launchctl", "list", label],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if r.returncode == 0:
        return True
    if r.returncode == 113 or "could not find service" in (r.stderr or "").lower():
        return False
    return None


def _app_bundle_version(app_path: Path):
    """CFBundleShortVersionString from an app bundle's Info.plist, or None if
    the bundle/plist/key is missing or unreadable. Never raises."""
    try:
        with open(app_path / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString")
    except Exception:
        return None


INSTALLED_APPS_DIRS_DEFAULT = "/Applications:%s:/System/Applications" % (HOME / "Applications")


def _installed_apps_dirs():
    raw = os.environ.get("MACCLEANER_INSTALLED_APPS_DIRS", INSTALLED_APPS_DIRS_DEFAULT)
    return [Path(p) for p in raw.split(":") if p]


def _app_bundle_identifier(app_path):
    """CFBundleIdentifier from an app bundle's Info.plist, lowercased, or
    None if the bundle/plist/key is missing or unreadable. Never raises."""
    try:
        with open(app_path / "Contents" / "Info.plist", "rb") as f:
            bundle_id = plistlib.load(f).get("CFBundleIdentifier")
        return bundle_id.lower() if bundle_id else None
    except Exception:
        return None


def _collect_app_bundle_id(entry, ids):
    """If `entry` is a directory named "*.app" (symlink or not), read its
    bundle ID into `ids`. Broken/unreadable bundles are skipped, not fatal.

    Unlike the leftover-scanning/deletion path (which never follows
    symlinks -- that's a deletion-safety rule protecting real data), this
    is read-only enumeration of what's currently INSTALLED. Refusing to
    follow a symlinked "*.app" here (e.g. macOS's own Safari.app under a
    Cryptexes redirect, or a Nix/home-manager-style symlinked install)
    only ever drops a real app out of the installed set, which can only
    ever manufacture MORE false positives downstream -- never protect
    anything. So this check follows symlinks (finding I4)."""
    if not entry.name.endswith(".app"):
        return
    try:
        if not entry.is_dir():
            return
    except OSError:
        return
    bundle_id = _app_bundle_identifier(Path(entry.path))
    if bundle_id:
        ids.add(bundle_id)


def installed_bundle_ids():
    """Bundle IDs of every top-level .app in the configured app-root
    directories (MACCLEANER_INSTALLED_APPS_DIRS), PLUS .app bundles nested
    exactly one level inside a non-.app wrapper folder -- some vendors
    (Adobe and others) ship that way instead of placing the .app directly
    at the app-root top level (finding F2). Bounded to exactly one extra
    level, no further recursion. A missing root, a broken individual
    bundle, or an unreadable wrapper folder is skipped, never fatal to
    enumeration. A symlinked non-.app WRAPPER folder is never followed
    (deletion-adjacent structural traversal); a symlinked "*.app" itself
    IS followed, at either level, since reading its Info.plist here is
    read-only enumeration, not deletion (finding I4 -- see
    _collect_app_bundle_id)."""
    ids = set()
    for root in _installed_apps_dirs():
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for e in entries:
            if e.name.endswith(".app"):
                _collect_app_bundle_id(e, ids)
                continue
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
                sub_entries = list(os.scandir(e.path))
            except OSError:
                continue
            for se in sub_entries:
                _collect_app_bundle_id(se, ids)
    return ids


def _mdfind_confirms_installed(candidates):
    """Best-effort second confirmation signal, layered on top of (never
    replacing) installed_bundle_ids()'s directory walk: returns the subset
    of `candidates` (an iterable of lowercase bundle-ID strings, already
    shape-validated by _looks_like_bundle_id before they ever reach here --
    no shell-injection risk from the quoted query values) that Spotlight's
    metadata index reports as having a real .app bundle ANYWHERE on disk
    with that exact CFBundleIdentifier -- regardless of location, directory
    depth, or nesting inside another .app. This is authoritative in a way
    no hardcoded set of directory roots can be: vendors ship apps in
    arbitrarily deep/unusual locations (Adobe Creative Cloud four
    directories deep, printer utilities under /Library/Printers, Steam-
    bundled games under ~/Library/Application Support/Steam, ...) that
    installed_bundle_ids()'s bounded top-level-plus-one-wrapper-level walk
    structurally cannot reach.

    A single batched mdfind call handles every candidate at once (an
    OR'd query) -- never one subprocess call per candidate, which would be
    far too slow against the 60-90 candidates a real run can produce. The
    'c' suffix after each quoted value makes the comparison case-
    insensitive: CFBundleIdentifier is stored on disk in whatever case the
    vendor wrote it (e.g. "com.adobe.acc.AdobeCreativeCloud"), while our
    candidates are always lowercased, so a case-sensitive '==' would silently
    fail to match real hits.

    Any failure (mdfind missing, Spotlight disabled/not-yet-indexed,
    timeout, non-zero exit, empty candidate list) returns an empty set --
    this check only ever narrows the hit list further, it never blocks or
    crashes the scanner if Spotlight is unavailable."""
    candidates = [c for c in candidates if c]
    if not candidates:
        return set()
    query = " || ".join(
        "kMDItemCFBundleIdentifier == '%s'c" % c for c in candidates)
    try:
        result = subprocess.run(
            ["mdfind", query], capture_output=True, text=True, timeout=5)
    except Exception:
        return set()
    if result.returncode != 0:
        return set()
    confirmed = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        bid = _app_bundle_identifier(Path(line))
        if bid:
            confirmed.add(bid)
    return confirmed


def _leftover_library_root():
    return Path(os.environ.get("MACCLEANER_LEFTOVER_LIBRARY_ROOT", str(HOME / "Library")))


LEFTOVER_ROOTS = ("Caches", "Preferences", "Saved Application State", "HTTPStorages", "WebKit")
# "group.com.apple." covers Apple's app-group preference domains (e.g.
# group.com.apple.mail, group.com.apple.notes) -- a plain "com.apple."
# prefix check doesn't catch these since they don't start with it.
LEFTOVER_EXCLUDE_PREFIXES = ("com.apple.", "group.com.apple.")
LEFTOVER_EXCLUDE_EXACT = {"com.fullex.maccleaner"}
_BUNDLE_ID_SHAPE = re.compile(r'^[a-z0-9]+(\.[a-z0-9-]+)+$')


def _looks_like_bundle_id(name):
    return _BUNDLE_ID_SHAPE.match(name.lower()) is not None


def _leftover_excluded(bundle_id):
    if bundle_id in LEFTOVER_EXCLUDE_EXACT:
        return True
    return any(bundle_id.startswith(p) for p in LEFTOVER_EXCLUDE_PREFIXES)


def _owned_by_installed(candidate, installed):
    """True if `candidate` IS an installed bundle ID, or is a strict
    sub-domain of one (e.g. "com.hnc.discord.shipit" under installed
    "com.hnc.discord" -- Squirrel.Mac's ".ShipIt" updater domain and
    similar vendor sub-domain patterns are still genuinely owned by the
    installed app, just not an exact bundle-ID match). Still exact-prefix
    matching against real installed IDs, never fuzzy: a dot boundary is
    required, so "com.example.appfoo" is NOT considered owned by installed
    "com.example.app"."""
    if candidate in installed:
        return True
    return any(candidate.startswith(i + ".") for i in installed)


def _leftover_candidate(root_name, entry):
    """Return the bundle-id-shaped candidate stem for a Library subdirectory
    entry, or None if the entry doesn't match this root's real-world shape
    (finding F3: a wrong-shaped entry -- e.g. a plain file where a directory
    is expected -- must be skipped outright, never misinterpreted). Also
    centralizes suffix-stripping per root (finding F1): only Preferences
    used to strip a suffix before matching, so Saved Application State
    (".savedState" DIRECTORIES) and HTTPStorages (".binarycookies" FILES)
    never matched an installed app's real bundle ID at all.

    Real shapes per root:
      Caches                  -- directory, bare bundle id
      Preferences              -- file, "<bundle-id>.plist"
      Saved Application State  -- directory, "<bundle-id>.savedState"
      HTTPStorages              -- EITHER a directory (bare bundle id) OR a
                                    file, "<bundle-id>.binarycookies"
      WebKit                    -- directory, bare bundle id

    entry.is_dir()/is_file() with follow_symlinks=False also means a
    symlink (of either shape) never yields a candidate here -- symlinks are
    never followed."""
    name = entry.name
    try:
        is_dir = entry.is_dir(follow_symlinks=False)
        is_file = entry.is_file(follow_symlinks=False)
    except OSError:
        return None

    if root_name == "Preferences":
        if is_file and name.endswith(".plist"):
            return name[:-len(".plist")]
        return None
    if root_name == "Saved Application State":
        if is_dir and name.endswith(".savedState"):
            return name[:-len(".savedState")]
        return None
    if root_name == "HTTPStorages":
        if is_dir and not name.endswith(".binarycookies"):
            return name
        if is_file and name.endswith(".binarycookies"):
            return name[:-len(".binarycookies")]
        return None
    if root_name in ("Caches", "WebKit"):
        # Caches, WebKit: directory, bare bundle id.
        if is_dir:
            return name
        return None
    # Finding M3: an explicit, named fallthrough rather than an implicit
    # "anything else" branch -- if a 6th root is ever added to
    # LEFTOVER_ROOTS without a matching clause here, it must produce NO
    # candidates (fail loudly/silently-safe) rather than silently inherit
    # Caches/WebKit's rule, which could be the wrong shape entirely. This
    # function must be kept in lockstep with LEFTOVER_ROOTS.
    return None


def scan_app_leftovers(config):
    """Top-level scan of five bundle-ID-keyed ~/Library subdirectories for
    entries whose bundle ID has no matching installed app. Never fuzzy --
    only names shaped like a reverse-DNS bundle ID are considered, and only
    the locations Apple's own conventions key by bundle ID (see the v2.7
    design doc for why Application Support/Containers/LaunchAgents are
    deliberately out of scope). No new home-only carve-out is needed: every
    root here is already strictly inside $HOME. skip_paths is honored the
    same way scan_tmp_artifacts honors it (finding F4) -- AGENTS.md already
    promises "Cleaning respects enabled_categories and skip_paths"."""
    installed = installed_bundle_ids()
    library_root = _leftover_library_root()
    min_age = config.get("app_leftover_min_age_days", 7)
    cutoff = time.time() - min_age * 86400
    # Resolved once up front (not per-candidate, finding M2): this scanner
    # walks ~2900 entries across 5 roots, vs. scan_tmp_artifacts' single
    # shallow root, so re-resolving on every iteration is a real cost here.
    skip = [Path(os.path.expanduser(p)).resolve() for p in config.get("skip_paths", [])]

    by_id = {}
    for root_name in LEFTOVER_ROOTS:
        root = library_root / root_name
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for e in entries:
            candidate = _leftover_candidate(root_name, e)
            if candidate is None:
                continue
            candidate = candidate.lower()
            if not _looks_like_bundle_id(candidate):
                continue
            if _leftover_excluded(candidate):
                continue
            if _owned_by_installed(candidate, installed):
                continue
            try:
                is_symlink = e.is_symlink()
                if is_symlink:
                    continue
                st = e.stat(follow_symlinks=False)
                resolved = Path(e.path).resolve()
            except OSError:
                continue
            if any(resolved == s or str(resolved).startswith(str(s) + os.sep)
                   for s in skip):
                continue
            entry = by_id.setdefault(candidate, {"bundle_id": candidate, "paths": [],
                                                  "locations": [], "mtime": 0.0})
            entry["paths"].append(Path(e.path))
            entry["locations"].append(root_name)
            entry["mtime"] = max(entry["mtime"], st.st_mtime)

    # Spotlight second-opinion pass (3rd whole-branch review): the
    # directory walk above is the fast primary signal and is left
    # completely untouched; this only ever narrows the candidate set
    # further, for bundle IDs the walk couldn't place but Spotlight's index
    # (unbounded by location/depth/nesting) can still confirm are installed.
    mdfind_confirmed = _mdfind_confirms_installed(by_id.keys())

    hits = [h for h in by_id.values()
            if h["mtime"] <= cutoff and h["bundle_id"] not in mdfind_confirmed]
    hits.sort(key=lambda h: h["bundle_id"])
    return hits


def app_leftovers_to_targets(hits):
    targets, seen = [], set()
    for h in hits:
        base = "leftover-" + slugify(h["bundle_id"])
        tid, n = base, 2
        while tid in seen:
            tid, n = "%s-%d" % (base, n), n + 1
        seen.add(tid)
        targets.append({
            "id": tid,
            "category": "leftovers",
            "label": "App leftovers: %s" % h["bundle_id"],
            "description": "Found in: %s — no installed app matches this bundle ID; review before deleting"
                           % ", ".join(h["locations"]),
            "path": None,
            "paths": h["paths"],
            "glob": None,
            "skip": [],
            "safe": False,
            "cmd": None,
            "estimate_cmd": None,
            "estimate_parser": None,
            "empty_only": False,
        })
    return targets


# ── Doctor: system-pressure signals (2.8.0) ────────────────────────────────────
# The threshold is about DISK CONSUMED, not memory pressure: sysctl's `total`
# is exactly the size of the swapfiles macOS has materialised under
# /System/Volumes/VM, so 8 GiB of swapfiles is 8 GiB of storage a disk tool
# should mention. A used/total RATIO was tried first and rejected -- it is not
# a pressure discriminator at all: a healthy machine sits at 90-94% for weeks,
# while a laptop that swapped 800 MB exactly once reads 78% and a machine with
# three times as much real swap on disk reads 61%. The ratio is non-monotonic
# in the quantity that matters, and the "restart to free it" remedy it implied
# is durable for minutes. The ratio survives only as informational text.
SWAP_WARN_BYTES = 8 * 1024 ** 3
# macOS's per-user temp dir ($TMPDIR). Flagged well below the swap threshold
# because, unlike swap, this is dead weight: orphaned scratch left behind by
# tools that exited without cleaning up. Observed at 20 GB / ~15,000 entries
# on a working developer Mac.
SYSTEM_TEMP_WARN_BYTES = 5 * 1024 ** 3
# Either threshold flags. Size alone under-reports: a real machine showed
# 16,289 entries holding only 3 GB, and that same directory had been 20 GB a
# few hours earlier. The entry count is the durable signal that tools are
# leaking scratch there; the byte figure just depends on when you look.
SYSTEM_TEMP_WARN_ENTRIES = 2000
# Docker Desktop's VM disk image. Flagged from 5 GiB: it is routinely the
# largest single item MacCleaner can see and cannot reclaim.
DOCKER_IMAGE_WARN_BYTES = 5 * 1024 ** 3
DOCKER_RAW_PATHS = [
    HOME / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw",
    HOME / "Library/Containers/com.docker.docker/Data/vms/0/Docker.raw",
]

_SWAP_UNITS = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def _parse_swap_usage(output):
    """Parse `sysctl vm.swapusage` into {total_bytes, used_bytes, percent},
    or None if the text can't be read. Real format:

        vm.swapusage: total = 16384.00M  used = 15571.88M  free = 812.12M  (encrypted)

    Never raises -- doctor reports "could not determine" rather than failing
    the whole run."""
    fields = {}
    for name in ("total", "used"):
        m = re.search(r"\b%s\s*=\s*([0-9.]+)([BKMGT])" % name, output)
        if not m:
            return None
        try:
            fields[name] = float(m.group(1)) * _SWAP_UNITS[m.group(2)]
        except ValueError:
            # `[0-9.]+` also matches non-numbers like "1.2.3" or ".". Real
            # sysctl can't emit those (the kernel uses a fixed %.2f), but the
            # parser must degrade to None rather than raise -- _swap_usage()
            # calls it outside its own try, so a raise here would escape
            # run_doctor() and break doctor's exit-0 guarantee.
            return None
    try:
        total, used = int(fields["total"]), int(fields["used"])
    except (ValueError, OverflowError):
        # `[0-9.]+` is unbounded, and float() of a 400-digit run returns inf
        # WITHOUT raising -- the OverflowError lands here, on int(), outside
        # the except above. Same exit-0 reasoning as the ValueError case.
        return None
    # Swap disabled / freshly booted reports 0.00M total -- don't divide by it.
    percent = round(used / total * 100, 1) if total else 0.0
    return {"total_bytes": total, "used_bytes": used, "percent": percent}


def _swap_usage():
    """Current swap usage, or None when sysctl can't answer."""
    try:
        result = subprocess.run(["sysctl", "vm.swapusage"],
                                capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return _parse_swap_usage(result.stdout)


HELD_OPEN_WARN_BYTES = 500 * 1024 ** 2
HELD_OPEN_PROC_FLOOR_BYTES = 10 * 1024 ** 2
HELD_OPEN_MAX_NAMED = 3


def _parse_held_open_deleted(output):
    """Parse `lsof -nPw +c 0 +L1` into deleted-but-still-open regular files.

    Columns: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NLINK NODE NAME.

    Dedupes by (DEVICE, NODE): one deleted inode shows up once per holding
    process AND once per fd within a process, so a naive per-line sum
    massively overstates the total (measured 7778 MB vs a true 4717 MB on a
    real machine). Each inode is attributed to the first command seen
    holding it, so per-command figures sum exactly to total_bytes."""
    seen = {}
    for line in output.splitlines():
        f = line.split()
        if len(f) < 10 or f[4] != "REG":
            continue
        command, dev, size, nlink, node = f[0], f[5], f[6], f[7], f[8]
        # SIZE/OFF holds an offset ("0t4096") for some fds -- only real sizes.
        if not size.isdigit() or nlink != "0":
            continue
        key = (dev, node)
        if key in seen:
            continue
        # `+c 0` keeps full command names but escapes spaces as \x20.
        seen[key] = (command.replace("\\x20", " "), int(size))
    per = {}
    for command, size in seen.values():
        per[command] = per.get(command, 0) + size
    return {
        "total_bytes": sum(b for _, b in seen.values()),
        "by_command": sorted(per.items(), key=lambda kv: (-kv[1], kv[0])),
        # How many distinct volumes contributed. doctor's Disk row reports the
        # startup volume only, so a total that silently spans several mounts
        # would read as if it all sat on the boot disk. We deliberately do not
        # map device numbers back to mount points -- just say how many.
        "device_count": len({dev for dev, _node in seen}),
    }


def _held_open_deleted():
    """Deleted-but-still-open file usage, or None when lsof can't answer."""
    try:
        # `-b` avoids the kernel calls (lstat/readlink/stat) that can block
        # indefinitely on a wedged network mount; `-w` already suppresses the
        # warnings `-b` would otherwise emit. Measured identical output.
        result = subprocess.run(["lsof", "-b", "-nPw", "+c", "0", "+L1"],
                                capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    # lsof exits non-zero when it merely found nothing (or couldn't probe
    # some mount) while still printing usable rows -- judge by output.
    if not result.stdout:
        return None
    return _parse_held_open_deleted(result.stdout)


def _docker_disk_image():
    """Docker Desktop's disk image, sized by ALLOCATED blocks.

    The file is sparse -- 1.0 TB apparent against ~10 GB allocated on a real
    machine -- so apparent size would claim a terabyte on a 460 GB disk.

    Report-only. `docker system prune` frees space inside the VM, but the
    host-side image only shrinks when Docker's own TRIM runs, which requires
    Docker to be running. MacCleaner cannot reclaim it from outside, and
    truncating or deleting the image would destroy every container, image and
    volume in it. Returns None when Docker isn't installed."""
    for raw in DOCKER_RAW_PATHS:
        try:
            st = os.stat(raw)
        except OSError:
            continue
        running = False
        try:
            running = subprocess.run(["pgrep", "-f", "com.docker.backend"],
                                     capture_output=True, timeout=5).returncode == 0
        except Exception:
            pass
        return {"path": str(raw), "bytes": _disk_bytes(st), "running": running}
    return None


def _system_temp_usage():
    """Size and entry count of macOS's per-user temp dir ($TMPDIR).

    Report-only. This lives outside $HOME, so the engine will never delete
    from it -- and shouldn't: macOS clears it on restart, which is both safer
    and more complete than any sweep this tool could do. Returns None on any
    failure so the check simply disappears rather than guessing."""
    tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
    # Only the real per-user folder, never /tmp (that's the `tmp` scanner's
    # territory and IS cleanable).
    if "/var/folders/" not in tmpdir:
        return None
    root = Path(tmpdir)
    if not root.is_dir():
        return None
    try:
        r = subprocess.run(["du", "-skx", str(root)],
                           capture_output=True, text=True, timeout=120)
        kb = int(r.stdout.split()[0])
    except Exception:
        return None
    try:
        entries = sum(1 for _ in os.scandir(root))
    except OSError:
        entries = 0
    return {"path": str(root), "bytes": kb * 1024, "entries": entries}


def run_doctor(config, json_mode=False):
    checks = []

    def check(name, status, ok=True, advisory=False):
        # `advisory` marks a REPORT-ONLY observation: something real about the
        # machine that MacCleaner deliberately refuses to act on and offers no
        # remedy for. AGENTS.md tells scripting agents that top-level `ok` is
        # the one signal to branch on, and it has always meant "a
        # MacCleaner-owned problem with a fix" (bad config, unloaded agent).
        # Advisory entries are therefore excluded from the aggregate below.
        # The key is emitted ONLY when True so every pre-existing check entry
        # stays byte-identical -- purely additive, per the JSON contract.
        entry = {"name": name, "status": status, "ok": ok}
        if advisory:
            entry["advisory"] = True
        checks.append(entry)

    check("Python", sys.version.split()[0])
    check("rich", "installed (pretty output)" if RICH else "not installed (plain output — run 'maccleaner install-deps')")

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                json.load(f)
            check("Config", f"valid — {CONFIG_PATH}")
        except json.JSONDecodeError as e:
            check("Config", f"INVALID JSON — {CONFIG_PATH}: {e}", ok=False)
    else:
        check("Config", "not found — using defaults")

    install_dir = HOME / "mac-cleaner"
    check("Install", f"installed at {install_dir}" if (install_dir / "cleaner.py").exists()
          else "not installed to ~/mac-cleaner (running from source?)")

    try:
        st = _schedule_state()
        def _by(state):
            return [a["label"] for a in st["agents"] if a["load_state"] == state]
        loaded, not_loaded, unknown = _by("loaded"), _by("not_loaded"), _by("unknown")
        # "launchctl could not be asked" is NOT "the agent isn't loaded"
        # (2.14.1). Only a definitive not-loaded is a MacCleaner-owned,
        # fixable fault; an unverifiable answer is reported as exactly that
        # and never fails the check, or doctor sends users to reinstall a
        # schedule that is running perfectly well.
        unverified = (f" (could not verify with launchctl: {', '.join(unknown)}"
                      " — it may well be running; re-check from a normal"
                      " login shell)" if unknown else "")
        if not_loaded:
            check("Schedule",
                  f"plist present but not loaded: {', '.join(not_loaded)}"
                  " — run scheduler.sh weekly to reload" + unverified, ok=False)
        elif loaded:
            note = f"launchd: {', '.join(loaded)}" + unverified
            if st["legacy_cron"]:
                note += " (plus a legacy cron entry — run scheduler.sh weekly to clean up)"
            check("Schedule", note)
        elif unknown:
            check("Schedule",
                  f"could not verify with launchctl: {', '.join(unknown)}"
                  " — the plists are installed and may well be running;"
                  " re-check from a normal login shell")
        elif st["legacy_cron"]:
            check("Schedule", "legacy cron entry (run scheduler.sh weekly to migrate to launchd)")
        else:
            check("Schedule", "not scheduled (run scheduler.sh weekly)")
    except Exception:
        check("Schedule", "could not determine schedule")

    # A plist being "loaded" per launchctl only means launchd has it
    # registered — it says nothing about whether the interpreter or engine
    # script it points at still exists. If Homebrew evicts a version-pinned
    # python@X.Y (see _agent_python), the agent stays "loaded" forever but
    # silently never runs anything. This is the only surface that would ever
    # tell a user that's happened, so check it directly instead of trusting
    # launchd's idea of health.
    try:
        missing = []
        for label in (CLEAN_LABEL, WATCH_LABEL):
            plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
            if not plist_path.exists():
                continue
            try:
                with open(plist_path, "rb") as f:
                    args = plistlib.load(f).get("ProgramArguments", [])
            except Exception:
                continue  # unparseable plist — the Schedule check above already covers this
            for idx, what in ((0, "interpreter"), (1, "engine")):
                if len(args) > idx and not Path(args[idx]).exists():
                    missing.append(f"{label} {what} missing: {args[idx]}")
        if missing:
            check("Schedule paths", "; ".join(missing), ok=False)
    except Exception:
        pass

    system_apps_dir = Path(os.environ.get("MACCLEANER_SYSTEM_APPLICATIONS_DIR", "/Applications"))
    app_paths = [HOME / "Applications/MacCleaner.app", system_apps_dir / "MacCleaner.app"]
    installed_app = next((p for p in app_paths if p.exists()), None)
    check("Menu bar app", f"installed at {installed_app}" if installed_app else "not installed")

    # A6: Sparkle (v2.6) updates the app bundle but never touches the
    # installed engine at ~/mac-cleaner/cleaner.py -- the app delegates ALL
    # cleaning logic to that engine (MACCLEANER_ENGINE override aside), and
    # it takes priority over the bundled fallback. After an auto-update the
    # new UI can end up driving a stale engine with nothing to detect it.
    # Compare this engine's VERSION against the app bundle's
    # CFBundleShortVersionString and warn on a mismatch. Degrades silently
    # when the app isn't installed or its Info.plist can't be read/parsed --
    # this is a nice-to-have signal, not a hard requirement.
    if installed_app is not None:
        app_version = _app_bundle_version(installed_app)
        if app_version is not None and app_version != VERSION:
            check("Engine/App version",
                  f"engine {VERSION} != app {app_version} — re-run bash install.sh",
                  ok=False)

    for tool in ["brew", "docker", "xcrun", "node", "npm", "pnpm", "yarn", "bun", "deno",
                 "go", "cargo", "gem", "pod", "gradle", "mvn", "uv", "ollama",
                 "conda", "dart", "composer", "terraform", "colima", "vagrant", "minikube"]:
        present = shutil.which(tool) is not None
        check(f"tool: {tool}", "found" if present else "not found (its targets will be skipped)")

    ds = disk_stats()
    check("Disk", f"{fmt_size(ds['free_bytes'])} free of {fmt_size(ds['total_bytes'])} ({ds['percent_used']}% used)")

    # Swap on disk (2.8.0). Report-only and ADVISORY: macOS owns the swapfiles
    # entirely, and it grows and reclaims them on its own -- there is nothing
    # here a cleaner could safely delete, so this never becomes a target and
    # never fails the aggregate `ok`.
    swap = _swap_usage()
    if swap is None:
        check("Swap", "could not determine swap usage", advisory=True)
    elif swap["total_bytes"] == 0:
        check("Swap", "no swapfiles on disk", advisory=True)
    else:
        detail = (f"swapfiles use {fmt_size(swap['total_bytes'])} of disk "
                  f"({fmt_size(swap['used_bytes'])} of that currently paged in, "
                  f"{swap['percent']}%)")
        if swap["total_bytes"] >= SWAP_WARN_BYTES:
            check("Swap",
                  detail + " — macOS manages this and reclaims it as memory pressure drops",
                  ok=False, advisory=True)
        else:
            check("Swap", detail, advisory=True)

    # macOS per-user temp dir. Report-only for the same reason as Swap: real
    # disk is consumed, but the correct remedy is a restart (macOS clears
    # $TMPDIR at boot), not this tool reaching into a system path.
    stmp = _system_temp_usage()
    if stmp and (stmp["bytes"] >= SYSTEM_TEMP_WARN_BYTES
                 or stmp["entries"] >= SYSTEM_TEMP_WARN_ENTRIES):
        check("System temp",
              f"{fmt_size(stmp['bytes'])} across {stmp['entries']:,} entries in "
              f"macOS's temp folder — leftover scratch from tools that exited "
              f"without cleaning up. macOS clears this on restart; MacCleaner "
              f"deliberately never deletes here",
              ok=False, advisory=True)

    # Docker's disk image. Report-only for a different reason than the others:
    # the space IS reclaimable, just not by this tool -- only Docker's own
    # TRIM can shrink the host-side image, and only while Docker is running.
    dk = _docker_disk_image()
    if dk and dk["bytes"] >= DOCKER_IMAGE_WARN_BYTES:
        how = ("start Docker Desktop, then run `docker system prune`"
               if not dk["running"]
               else "run `docker system prune`")
        state = "Docker is not running, so nothing can shrink it right now; "
        if dk["running"]:
            state = ""
        check("Docker disk image",
              f"Docker's VM image holds {fmt_size(dk['bytes'])}. {state}"
              f"to reclaim it, {how} — Docker returns the freed space to the "
              f"disk itself. MacCleaner never touches this image: deleting or "
              f"truncating it would destroy every container, image and volume",
              ok=False, advisory=True)

    # Deleted-but-still-open files (2.8.0). Report-only: the space returns by
    # itself the moment the holding process exits, and killing someone's
    # process is not a cleanup action this tool should take.
    held = _held_open_deleted()
    if held and held["total_bytes"] >= HELD_OPEN_WARN_BYTES:
        named = [(c, b) for c, b in held["by_command"]
                 if b >= HELD_OPEN_PROC_FLOOR_BYTES]
        # Many small holders can clear the total threshold while no single
        # one clears the per-process floor -- still name the biggest.
        if not named:
            named = held["by_command"]
        shown = named[:HELD_OPEN_MAX_NAMED]
        detail = ", ".join(f"{c} ({fmt_size(b)})" for c, b in shown)
        # Count ALL remaining holders, not just those above the naming floor:
        # counting `named` alone made every sub-floor holder invisible in both
        # the names and the count.
        rest = len(held["by_command"]) - len(shown)
        if rest > 0:
            detail += f" +{rest} more process{'es' if rest > 1 else ''}"
        # The Disk row above covers the startup volume only, so flag it when
        # this total is spread across more than one mounted volume.
        volumes = ""
        if held.get("device_count", 1) > 1:
            volumes = f" across {held['device_count']} volumes"
        check("Held-open files",
              f"{fmt_size(held['total_bytes'])} of deleted files still held open"
              f"{volumes} by {detail} — freed once every process holding them exits",
              ok=False, advisory=True)

    # Advisory entries are report-only observations with no MacCleaner-side
    # remedy; folding them in would make `ok` mean "unhealthy, and nothing you
    # or the tool can do about it". See check() above.
    all_ok = all(c["ok"] for c in checks if not c.get("advisory"))

    if json_mode:
        print(json.dumps({"version": VERSION, "ok": all_ok, "checks": checks}, indent=2))
    elif RICH:
        table = Table(title="🩺 MacCleaner Doctor", show_lines=False)
        table.add_column("Check", style="cyan")
        table.add_column("Status", style="white")
        for c in checks:
            style = "" if c["ok"] else "[bold red]"
            table.add_row(c["name"], f"{style}{c['status']}")
        console.print(table)
    else:
        print(f"\n{'='*64}")
        print("MacCleaner Doctor")
        print(f"{'='*64}")
        for c in checks:
            flag = " " if c["ok"] else "!"
            print(f"  {flag} {c['name']:<16} {c['status']}")
        print(f"{'='*64}\n")

    # Returns the full result, not just the bool: a function that computed
    # fifteen checks throwing away fourteen of them made it impossible to
    # assert on any individual check without re-parsing printed JSON.
    # `main()` discards this either way, so `doctor` still always exits 0.
    return {"ok": all_ok, "checks": checks}


# ── Categories ──────────────────────────────────────────────────────────────────
def show_categories(config, json_mode=False):
    all_targets = get_targets(config, all_categories=True)
    by_cat = {}
    for t in all_targets:
        by_cat.setdefault(t["category"], []).append(t)
    enabled = set(config["enabled_categories"])

    if json_mode:
        print(json.dumps({
            "version": VERSION,
            "categories": [
                {
                    "name": cat,
                    "description": CATEGORY_DESCRIPTIONS.get(cat, ""),
                    "enabled": cat in enabled,
                    "targets": [{"id": t["id"], "label": t["label"], "safe": t["safe"]}
                                for t in by_cat.get(cat, [])],
                }
                for cat in ALL_CATEGORIES
            ]
        }, indent=2))
        return

    if RICH:
        table = Table(title="Categories", show_lines=False)
        table.add_column("Category", style="cyan")
        table.add_column("Enabled", justify="center")
        table.add_column("Targets", justify="right")
        table.add_column("Description", style="dim")
        for cat in ALL_CATEGORIES:
            table.add_row(cat, "✅" if cat in enabled else "—",
                          str(len(by_cat.get(cat, []))), CATEGORY_DESCRIPTIONS.get(cat, ""))
        console.print(table)
        console.print("[dim]Toggle with: maccleaner config enable/disable <category>[/dim]")
    else:
        print(f"\n{'='*72}")
        for cat in ALL_CATEGORIES:
            mark = "x" if cat in enabled else " "
            print(f"  [{mark}] {cat:<10} ({len(by_cat.get(cat, [])):>2} targets)  {CATEGORY_DESCRIPTIONS.get(cat, '')}")
        print(f"\n  Toggle with: maccleaner config enable/disable <category>")
        print(f"{'='*72}\n")


def _atomic_write_json(path: Path, data):
    """Dump JSON to a temp file beside `path`, then os.replace() it into place.

    os.replace() is atomic within a filesystem, so a concurrent reader (e.g. a
    cron `clean --yes` overlapping a menu bar app scan) always sees either the
    old complete file or the new complete file, never a partial write. On any
    failure the temp file is removed so it's never mistaken for real data."""
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── Report log ──────────────────────────────────────────────────────────────────
def write_log(total_freed: int, results: list):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "total_freed_bytes": total_freed,
        "total_freed_human": fmt_size(total_freed),
        "disk_after": disk_free(),
        "items": results
    }
    logs = []
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH) as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    logs = logs[-50:]  # Keep last 50 runs
    try:
        _atomic_write_json(LOG_PATH, logs)
    except Exception as e:
        print(f"Warning: could not write log: {e}", file=sys.stderr)


def _read_report_log():
    """report.log as a list, or [] on any problem — never raises. Shared by
    the weekly digest and the stats aggregation."""
    try:
        with open(LOG_PATH) as f:
            logs = json.load(f)
        return logs if isinstance(logs, list) else []
    except Exception:
        return []


def _weekly_digest_totals(now=None):
    """(bytes_freed, run_count) over the trailing 7 days of report.log.

    Feeds the scheduled clean's notification (Phase 6 "scheduled scan
    reports"): the scheduled clean is itself weekly, so its completion
    notification doubles as the weekly summary — no second agent, no plist
    change, existing installs pick it up with the engine update."""
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=7)
    freed = runs = 0
    for entry in _read_report_log():
        try:
            ts = datetime.datetime.fromisoformat(entry["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts >= cutoff:
            freed += int(entry.get("total_freed_bytes", 0) or 0)
            runs += 1
    return freed, runs


def _clean_notification(total_freed, cleaned):
    """(title, message) for the post-clean notification. Called after
    write_log, so the run being announced is already in the 7-day window."""
    week_bytes, week_runs = _weekly_digest_totals()
    title = f"MacCleaner freed {fmt_size(total_freed)}"
    message = (f"{cleaned} item{'s' if cleaned != 1 else ''} cleaned · "
               f"{fmt_size(week_bytes)} freed this week "
               f"({week_runs} run{'s' if week_runs != 1 else ''}) · "
               f"{fmt_size(disk_stats()['free_bytes'])} free")
    return title, message


_DYNAMIC_ID_CATEGORIES = (("tmp-", "tmp"), ("project-", "projects"),
                          ("leftover-", "leftovers"), ("simulator-", "simulators"))


def _aggregate_stats():
    """Local-first usage stats over report.log (Phase 6 "usage analytics").

    Everything is computed from this machine's own cleanup history and
    nothing ever leaves the machine — "opt-in" is running the command. An
    item counts as usage only when it actually freed bytes; skips and
    errors are not usage. Categories come from the current target table,
    with dynamic-family IDs (tmp-*/project-*/leftover-*/simulator-*)
    mapped by their stable prefix since those IDs are per-machine."""
    logs = _read_report_log()
    try:
        id_to_cat = {t["id"]: t["category"]
                     for t in get_targets(load_config(), all_categories=True)}
    except Exception:
        id_to_cat = {}

    def category_of(tid):
        if tid in id_to_cat:
            return id_to_cat[tid]
        for prefix, cat in _DYNAMIC_ID_CATEGORIES:
            if tid.startswith(prefix):
                return cat
        return "other"

    per_target = {}
    total = 0
    first_run = last_run = None
    for run in logs:
        ts = run.get("timestamp")
        if ts:
            first_run = first_run or ts
            last_run = ts
        total += int(run.get("total_freed_bytes", 0) or 0)
        for item in run.get("items", []):
            freed = int(item.get("freed", 0) or 0)
            if freed <= 0:
                continue
            tid = item.get("id", "?")
            d = per_target.setdefault(tid, {
                "id": tid, "label": item.get("label", tid),
                "category": category_of(tid),
                "freed_bytes": 0, "times_cleaned": 0})
            d["freed_bytes"] += freed
            d["times_cleaned"] += 1

    targets = sorted(per_target.values(),
                     key=lambda d: (-d["freed_bytes"], d["id"]))
    per_cat = {}
    for d in targets:
        pc = per_cat.setdefault(d["category"],
                                {"category": d["category"],
                                 "freed_bytes": 0, "times_cleaned": 0})
        pc["freed_bytes"] += d["freed_bytes"]
        pc["times_cleaned"] += d["times_cleaned"]
    categories = sorted(per_cat.values(),
                        key=lambda d: (-d["freed_bytes"], d["category"]))
    return {"runs": len(logs), "total_freed_bytes": total,
            "total_freed_human": fmt_size(total),
            "first_run": first_run, "last_run": last_run,
            "targets": targets, "categories": categories}


def show_stats(json_mode=False):
    stats = _aggregate_stats()
    if json_mode:
        print(json.dumps({"version": VERSION, "stats": stats}, indent=2))
        return
    if not stats["runs"]:
        print("No cleanup history yet — run 'maccleaner clean' first.")
        return
    print(f"── MacCleaner Usage Stats ── {stats['runs']} runs · "
          f"{stats['total_freed_human']} freed all-time")
    print()
    print("By category:")
    for c in stats["categories"]:
        print(f"  {c['category']:<14} {fmt_size(c['freed_bytes']):>10}  "
              f"({c['times_cleaned']}×)")
    print()
    print("Top targets:")
    for t in stats["targets"][:15]:
        print(f"  {t['label'][:40]:<40} {fmt_size(t['freed_bytes']):>10}  "
              f"({t['times_cleaned']}×)")


# ── Disk snapshots ──────────────────────────────────────────────────────────────
def load_snapshots():
    if not SNAPSHOTS_PATH.exists():
        return []
    try:
        with open(SNAPSHOTS_PATH) as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # A non-dict element would otherwise raise inside record_snapshot's
        # snaps[-1].get(...) dedupe check on every future run, forever (only
        # caught by the broad except there, with just a per-run warning).
        # Dropping non-dict entries here lets a partially malformed file
        # self-heal the same way a fully unparseable one does.
        cleaned = [e for e in data if isinstance(e, dict)]
        dropped = len(data) - len(cleaned)
        if dropped:
            noun = "entry" if dropped == 1 else "entries"
            print(f"Warning: discarded {dropped} malformed snapshot {noun} from {SNAPSHOTS_PATH}",
                  file=sys.stderr)
        return cleaned
    except Exception:
        print(f"Warning: corrupt or unparseable {SNAPSHOTS_PATH}, restarting", file=sys.stderr)
        return []


def record_snapshot(reclaimable_bytes=None, categories=None):
    """Append a disk snapshot; a snapshot recorded the same calendar day
    replaces the last one instead of adding a new entry.

    None reclaimable/categories = the run only measured part of the target set,
    so only the disk numbers are trustworthy."""
    try:
        ds = disk_stats()
        entry = {
            "ts": datetime.datetime.now().isoformat(),
            "disk_total_bytes": ds["total_bytes"],
            "disk_free_bytes": ds["free_bytes"],
            "reclaimable_bytes": reclaimable_bytes,
            "categories": categories,
        }
        snaps = load_snapshots()
        if snaps and snaps[-1].get("ts", "")[:10] == entry["ts"][:10]:
            snaps[-1] = entry
        else:
            snaps.append(entry)
        snaps = snaps[-SNAPSHOT_CAP:]
        _atomic_write_json(SNAPSHOTS_PATH, snaps)
    except Exception as e:
        print(f"Warning: could not write snapshot: {e}", file=sys.stderr)


def snapshot_fields(measured_targets):
    """(reclaimable_bytes, categories) sums from a fully measured target list."""
    total, cats = 0, {}
    for t in measured_targets:
        size = t.get("size") or 0
        total += size
        cats[t["category"]] = cats.get(t["category"], 0) + size
    return total, cats


def _nearest_snapshot(snaps, days_ago):
    goal = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    best, best_delta = None, None
    for s in snaps:
        try:
            ts = datetime.datetime.fromisoformat(s["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        delta = abs((ts - goal).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = s, delta
    return best


def format_disk_trend(snaps):
    """Lines comparing current free space with ~7d/~30d-ago snapshots, or None."""
    if len(snaps) < 2:
        return None
    now = datetime.datetime.now()
    now_free = disk_stats()["free_bytes"]
    lines = [f"Free now: {fmt_size(now_free)}"]
    seen = set()
    for days in (7, 30):
        s = _nearest_snapshot(snaps, days)
        if not s or s["ts"] in seen:
            continue
        seen.add(s["ts"])
        try:
            age = max(0, (now - datetime.datetime.fromisoformat(s["ts"])).days)
        except (TypeError, ValueError):
            continue
        delta = now_free - s["disk_free_bytes"]
        sign = "+" if delta >= 0 else "-"
        lines.append(f"{age}d ago: {fmt_size(s['disk_free_bytes'])} free "
                     f"({sign}{fmt_size(abs(delta))} free since)")
    return lines if len(lines) > 1 else None


def _print_disk_trend(snaps):
    lines = format_disk_trend(snaps)
    if not lines:
        return
    if RICH:
        console.print(Panel("\n".join(lines), title="💾 Disk trend", border_style="cyan"))
    else:
        print("  Disk trend:")
        for line in lines:
            print(f"    {line}")
        print()


# ── Low-disk alerts ────────────────────────────────────────────────────────────
LOW_DISK_RENOTIFY_HOURS = 24


def load_alerts():
    """Alert throttle state. A corrupt file self-heals, like snapshots.log."""
    if not ALERTS_PATH.exists():
        return {}
    try:
        with open(ALERTS_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        print(f"Warning: corrupt or unparseable {ALERTS_PATH}, restarting", file=sys.stderr)
        return {}


def save_alerts(alerts):
    try:
        _atomic_write_json(ALERTS_PATH, alerts)
    except Exception as e:
        print(f"Warning: could not write alert state: {e}", file=sys.stderr)


def _low_disk_decision(alerts, now, free_bytes, threshold_bytes):
    """(should_notify, new_low_disk_state) — pure, so the throttle is testable
    without touching the clock or the filesystem.

    Notify on an above->below transition, then at most once per
    LOW_DISK_RENOTIFY_HOURS while still below. Recovering to `above` clears the
    stamp so the next dip warns immediately."""
    prev = alerts.get("low_disk") or {}
    if free_bytes >= threshold_bytes:
        return False, {"state": "above", "last_notified": prev.get("last_notified")}

    stamp = {"state": "below", "last_notified": now.isoformat()}
    if prev.get("state") != "below":
        return True, stamp
    last = prev.get("last_notified")
    if not last:
        return True, stamp
    try:
        elapsed = now - datetime.datetime.fromisoformat(last)
    except (TypeError, ValueError):
        return True, stamp
    if elapsed >= datetime.timedelta(hours=LOW_DISK_RENOTIFY_HOURS):
        return True, stamp
    return False, {"state": "below", "last_notified": last}


def run_disk_check(config, json_mode=False, post=True):
    """Cheap enough to run hourly: one disk_usage call, no measurement, no
    snapshot. Always exits 0 — it is a monitor, not a check that fails.

    `post=False` (used by the app's own periodic check, which delivers via
    NotificationManager for a correctly-attributed icon instead of the
    generic-icon osascript path) skips posting here but still makes and
    persists the throttle decision, so the app and the standalone launchd
    disk-check agent share one 24h window and never double-notify."""
    ds = disk_stats()
    raw_threshold = config.get("low_disk_threshold_gb", 10)
    try:
        threshold_gb = float(raw_threshold)
        if not math.isfinite(threshold_gb):
            raise ValueError(f"non-finite threshold_gb {threshold_gb!r}")
        if threshold_gb < 0:
            raise ValueError(f"negative threshold_gb {threshold_gb!r}")
        threshold = int(threshold_gb * 1024**3)
    except (TypeError, ValueError, OverflowError):
        print(f"Warning: invalid low_disk_threshold_gb {raw_threshold!r}, "
              f"falling back to the default of 10 GB", file=sys.stderr)
        threshold_gb = 10
        threshold = int(threshold_gb * 1024**3)
    free = ds["free_bytes"]
    enabled = config.get("low_disk_alerts", True)

    alerts = load_alerts()
    should_notify, state = _low_disk_decision(alerts, datetime.datetime.now(), free, threshold)
    notified = False
    # A decision that doesn't depend on posting a notification (still-above, or
    # still-below-but-throttled) is accurate regardless of what happens below.
    persist_state = not should_notify
    if enabled and should_notify:
        if post:
            notified = _notify(
                f"Low disk space: {fmt_size(free)} free",
                f"Below your {fmt_size(threshold)} threshold — "
                f"open MacCleaner to reclaim space.")
            # Only stamp the throttle when the banner actually posted —
            # otherwise a failed notification would suppress retries for the
            # next 24h even though the user never saw anything (finding M5).
            persist_state = notified
        else:
            # The caller is taking over delivery; claim the throttle slot
            # so the launchd agent doesn't also fire for this same dip.
            persist_state = True
    if enabled and persist_state and alerts.get("low_disk") != state:
        # Skip the write entirely when nothing changed, so an hourly agent
        # isn't rewriting alerts.json every run for no reason (finding M3).
        alerts["low_disk"] = state
        save_alerts(alerts)

    result = {
        "free_bytes": free,
        "free_human": fmt_size(free),
        "threshold_bytes": threshold,
        "below_threshold": free < threshold,
        "notified": notified,
        "should_notify": bool(enabled and should_notify),
    }
    if json_mode:
        print(json.dumps({"version": VERSION, **result}, indent=2))
    else:
        status = "BELOW threshold" if result["below_threshold"] else "ok"
        print(f"Free: {result['free_human']} · threshold "
              f"{fmt_size(threshold)} · {status}")
    return result


# ── Scheduling (launchd) ───────────────────────────────────────────────────────
# Port of scheduler.sh (which is now a thin wrapper over this). launchd rather
# than cron: launchd runs a missed calendar job after the Mac wakes; cron
# silently skips it.
LAUNCH_AGENTS_DIR = Path(os.environ.get("MACCLEANER_LAUNCH_AGENTS_DIR",
                                        HOME / "Library/LaunchAgents"))
CLEAN_LABEL = "com.fullex.maccleaner.clean"
WATCH_LABEL = "com.fullex.maccleaner.diskwatch"
# A cron line belongs to MacCleaner only if it references the canonical
# install path — an unanchored "cleaner.py" match would catch a user's own
# db-cleaner.py job.
CRON_MARKER = "mac-cleaner/cleaner.py"
# Homebrew tools aren't on launchd's minimal default PATH; same list
# CleanerBridge.runEngine uses, so cmd targets behave identically under the
# app and under a scheduled agent.
AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Unversioned python3 locations, tried in order when PATH resolves to a
# virtualenv. `/usr/bin/python3` is last because it always exists on macOS,
# so reaching the end of this list (and the sys.base_prefix fallback after
# it) is nearly impossible in practice — which is the point. A module
# constant rather than a literal so tests can empty it to exercise the
# give-up path.
STABLE_PYTHON_CANDIDATES = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                            "/usr/bin/python3")
CRON_LOG_PATH = LOG_PATH.parent / "cron.log"   # beside report.log wherever that lives


def _is_venv_interpreter(python_path) -> bool:
    """True if `python_path` lives inside a virtualenv.

    Detected via the standard `pyvenv.cfg` marker one level above the
    interpreter's containing directory (e.g. `.venv/bin/python3` ->
    `.venv/pyvenv.cfg`). This is a property of the *candidate path itself*,
    not of the current process — `sys.prefix != sys.base_prefix` only tells
    you whether the process currently running this code is a venv; it can't
    vet an arbitrary `python3` resolved from PATH, which is exactly the
    candidate that matters here.

    Deliberately `.absolute()`, not `.resolve()`: a venv's `bin/python3` is
    itself almost always a symlink to the real base interpreter (e.g.
    `.venv/bin/python3 -> python3.14 -> /opt/homebrew/.../python3.14`), so
    fully resolving it walks straight past the venv and inspects the real
    interpreter's directory instead — silently defeating this check.
    """
    try:
        p = Path(python_path).absolute()
    except OSError:
        return False
    return (p.parent.parent / "pyvenv.cfg").exists()


def _agent_python() -> str:
    """Interpreter to embed in the scheduled agents' ProgramArguments.

    `sys.executable` is tempting but dangerous here: on Homebrew Python it's
    a version-pinned path (e.g. /opt/homebrew/opt/python@3.14/bin/python3.14).
    When Homebrew moves on to a newer python@X.Y formula, the old one becomes
    an orphaned dependency — and MacCleaner's own `brew-autoremove` target,
    run by the very launchd agent this builds, deletes the interpreter out
    from under itself. Both agents then silently never spawn again, and
    nothing notices: `_schedule_state()` only checks plist presence and
    `launchctl list`, so `schedule status` and `doctor` keep reporting
    "loaded" indefinitely (see doctor's ProgramArguments existence check,
    which is the one thing that would ever catch this).

    `shutil.which("python3")` instead resolves the stable, unversioned
    `python3` symlink on PATH — the same one the old bash `command -v
    python3` used, and what already-installed real plists contain. Do not
    "simplify" this back to `sys.executable`.

    A virtualenv interpreter is worse than either choice: `.venv` is itself
    one of MacCleaner's own `projects` artifact targets, so a scheduled agent
    could end up pointing at a directory this same tool prunes. Refuse to
    fall back to one — and crucially, check this on the `shutil.which()`
    candidate too, not just the `sys.executable` fallback: activating a venv
    puts `$VIRTUAL_ENV/bin` first on PATH, so `shutil.which("python3")` *is*
    the venv's python in the common case. Checking only the fallback branch
    left that case unchecked.
    """
    stable = shutil.which("python3")
    if stable:
        if not _is_venv_interpreter(stable):
            return stable
        # PATH resolved a venv interpreter. Prefer a stable unversioned
        # python3 from a well-known location before falling back to
        # sys.base_prefix: the base prefix of a Homebrew-created venv is
        # itself the version-pinned path this function exists to avoid
        # (…/python@3.14/Frameworks/…/3.14/bin/python3), so using it would
        # trade the venv hazard straight back for the brew-autoremove one.
        # /usr/bin/python3 is last because it always exists on macOS.
        for candidate in STABLE_PYTHON_CANDIDATES:
            if os.path.exists(candidate) and not _is_venv_interpreter(candidate):
                return candidate
        base_candidate = Path(sys.base_prefix) / "bin" / "python3"
        if base_candidate.exists() and not _is_venv_interpreter(str(base_candidate)):
            return str(base_candidate)
        raise RuntimeError(
            f"'python3' on PATH ({stable}) is a virtualenv interpreter, and "
            "no stable system python3 could be found either — refusing to "
            "schedule against a virtualenv. Deactivate the virtualenv (so "
            "PATH resolves to a real python3, e.g. via Homebrew) and try "
            "again."
        )
    if sys.prefix != sys.base_prefix:
        raise RuntimeError(
            "no stable 'python3' found on PATH, and the running interpreter "
            f"({sys.executable}) is a virtualenv — refusing to schedule "
            "against it. Put a 'python3' on PATH (deactivate the virtualenv, "
            "or install one via Homebrew) and try again."
        )
    return sys.executable


def _agent_plist(label, program_args, trigger):
    """Plist dict for one agent. `trigger` is e.g.
    {"StartInterval": 3600} or {"StartCalendarInterval": {...}}."""
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "EnvironmentVariables": {"PATH": AGENT_PATH},
        **trigger,
        "StandardOutPath": str(CRON_LOG_PATH),
        "StandardErrorPath": str(CRON_LOG_PATH),
    }


def _write_agent_plist(label, plist):
    """Write the plist atomically — same pattern as `_atomic_write_json`: dump
    to a temp file beside the target, then `os.replace()`. Two overlapping
    installs (a double-clicked Settings radio button, or the app and a
    terminal at once) can't leave launchd a truncated plist that fails to
    parse."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    target = LAUNCH_AGENTS_DIR / f"{label}.plist"
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            plistlib.dump(plist, f)
        # mkstemp creates 0600; every real plist in ~/Library/LaunchAgents
        # (including MacCleaner's own) is 0644. Restore that before the
        # atomic swap so this doesn't quietly change the on-disk permissions
        # of a file launchd has to read.
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, str(target))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


def _bootstrap_agent(label):
    """bootout → bootstrap, falling back to unload/load for older macOS.
    Returns (ok, error_message). The plist is already on disk either way."""
    plist = str(LAUNCH_AGENTS_DIR / f"{label}.plist")
    uid = os.getuid()
    try:
        _launchctl("bootout", f"gui/{uid}/{label}")
        r = _launchctl("bootstrap", f"gui/{uid}", plist)
        if r.returncode == 0:
            return True, None
        _launchctl("unload", plist)
        r2 = _launchctl("load", plist)
        if r2.returncode == 0:
            return True, None
        err = (r2.stderr or r.stderr or "").strip()
        return False, err
    except Exception as e:
        return False, str(e)


def _unload_agent(label):
    plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
    try:
        uid = os.getuid()
        _launchctl("bootout", f"gui/{uid}/{label}")
        _launchctl("unload", str(plist))
    except Exception:
        pass
    existed = plist.exists()
    plist.unlink(missing_ok=True)
    return existed


def _read_crontab():
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _strip_legacy_cron(say):
    """Remove any MacCleaner cron line, echoing it. Returns True if one was
    removed. Reports (not uses) the line's own cadence — the caller installs
    whatever the user actually asked for."""
    existing = _read_crontab()
    lines = [l for l in existing.splitlines() if CRON_MARKER in l]
    if not lines:
        return False
    detected = "weekly"
    fields = lines[0].split()
    if len(fields) >= 3 and fields[2] != "*":
        detected = "monthly"
    say(f"→ Found a legacy cron schedule (looked {detected}) — migrating to launchd and removing it:")
    for l in lines:
        say(f"    {l}")
    kept = "\n".join(l for l in existing.splitlines() if CRON_MARKER not in l)
    try:
        r = subprocess.run(["crontab", "-"], input=kept + ("\n" if kept else ""),
                           capture_output=True, text=True, timeout=10)
    except Exception as e:
        say(f"⚠️  Could not rewrite crontab: {e}")
        return False
    if r.returncode != 0:
        say(f"⚠️  Could not rewrite crontab (exit {r.returncode}): {r.stderr.strip()}")
        return False
    return True


def _schedule_state():
    """One source of truth for status/doctor: what's installed and loaded."""
    agents = []
    schedule = None
    for label in (CLEAN_LABEL, WATCH_LABEL):
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        present = plist_path.exists()
        state = _launchd_is_loaded(label) if present else False
        agents.append({"label": label,
                       "plist_present": present,
                       # `loaded` stays a plain bool: the Swift app decodes
                       # it as non-optional Bool. The tri-state is exposed
                       # additively as `load_state` (2.14.1).
                       "loaded": state is True,
                       "load_state": {True: "loaded", False: "not_loaded"}.get(
                           state, "unknown")})

        if label == CLEAN_LABEL and present:
            try:
                with open(plist_path, "rb") as f:
                    cal = plistlib.load(f).get("StartCalendarInterval", {})
                schedule = "monthly" if "Day" in cal else "weekly" if "Weekday" in cal else None
            except Exception:
                schedule = None
    agents = [a for a in agents if a["plist_present"]]
    return {"schedule": schedule, "agents": agents,
            "legacy_cron": CRON_MARKER in _read_crontab()}


def _print_schedule_status(state):
    print("── MacCleaner Scheduler Status ──")
    if not state["agents"]:
        print("❌ Not scheduled (run ./scheduler.sh weekly)")
    for a in state["agents"]:
        if a["load_state"] == "loaded":
            print(f"✅ {a['label']} (launchd)")
        elif a["load_state"] == "unknown":
            print(f"❔ {a['label']} — installed, but launchctl could not be "
                  f"asked here (re-check from a normal login shell)")
        else:
            print(f"⚠️  {a['label']} — plist present but not loaded "
                  f"(run ./scheduler.sh weekly or monthly to reload)")
    if state["legacy_cron"]:
        print("⚠️  A legacy cron entry is still present — run ./scheduler.sh weekly to migrate.")


def run_schedule_status(json_mode=False):
    state = _schedule_state()
    if json_mode:
        print(json.dumps({"version": VERSION, **state}, indent=2))
    else:
        _print_schedule_status(state)
    return state


def run_schedule_install(kind, json_mode=False):
    """Install/replace both agents. Returns True when both loaded."""
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print

    try:
        python = _agent_python()
    except RuntimeError as e:
        say(f"❌ {e}")
        if json_mode:
            print(json.dumps({"version": VERSION, "error": str(e)}, indent=2))
        return False

    migrated = _strip_legacy_cron(say)

    engine = Path(__file__).resolve()
    trigger = ({"StartCalendarInterval": {"Day": 1, "Hour": 9, "Minute": 0}}
               if kind == "monthly" else
               {"StartCalendarInterval": {"Weekday": 1, "Hour": 9, "Minute": 0}})
    jobs = [
        (CLEAN_LABEL, [python, str(engine), "clean", "--yes", "--notify"], trigger),
        (WATCH_LABEL, [python, str(engine), "disk-check"], {"StartInterval": 3600}),
    ]
    ok = True
    for label, args, trig in jobs:
        _write_agent_plist(label, _agent_plist(label, args, trig))
        loaded, err = _bootstrap_agent(label)
        if not loaded:
            ok = False
            print(f"⚠️  Could not load {label} with launchctl.{' (' + err + ')' if err else ''}",
                  file=sys.stderr)
            print(f"    The plist is written to {LAUNCH_AGENTS_DIR / (label + '.plist')} — "
                  f"load it manually with:\n"
                  f"    launchctl bootstrap gui/{os.getuid()} \"{LAUNCH_AGENTS_DIR / (label + '.plist')}\"",
                  file=sys.stderr)
            print(f"⚠️  {label} did not load — fix the issue above, then run "
                  f"./scheduler.sh {kind} again (or load the plist manually).",
                  file=sys.stderr)

    if json_mode:
        print(json.dumps({"version": VERSION, **_schedule_state(),
                          "migrated_cron": migrated}, indent=2))
    elif ok:
        print("✅ Scheduled: 1st of every month at 9am (launchd)" if kind == "monthly"
              else "✅ Scheduled: every Monday at 9am (launchd)")
        print("   Low-disk check: hourly")
        print(f"   Log: {CRON_LOG_PATH}")
    if not ok:
        print("❌ Scheduling incomplete — see the warning(s) above.", file=sys.stderr)
    return ok


def run_schedule_off(json_mode=False):
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print
    existing = _read_crontab()
    if CRON_MARKER in existing:
        for l in existing.splitlines():
            if CRON_MARKER in l:
                say("   Removing legacy cron entry:")
                say(f"     {l}")
        kept = "\n".join(l for l in existing.splitlines() if CRON_MARKER not in l)
        try:
            r = subprocess.run(["crontab", "-"], input=kept + ("\n" if kept else ""),
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                say(f"⚠️  Could not rewrite crontab (exit {r.returncode}): {r.stderr.strip()}")
        except Exception as e:
            say(f"⚠️  Could not rewrite crontab: {e}")
    removed_clean = _unload_agent(CLEAN_LABEL)
    removed_watch = _unload_agent(WATCH_LABEL)
    removed = removed_clean or removed_watch
    if json_mode:
        print(json.dumps({"version": VERSION, **_schedule_state(),
                          "removed": removed}, indent=2))
    else:
        print("✅ Removed MacCleaner launchd agents" if removed
              else "Nothing scheduled — nothing to remove.")


def show_report(limit=10, json_mode=False):
    disk_history = {"current": disk_stats(), "snapshots": load_snapshots()}
    if not LOG_PATH.exists():
        if json_mode:
            print(json.dumps({"version": VERSION, "runs": [], "disk_history": disk_history}))
        else:
            print("No cleanup history found. Run 'maccleaner clean' first.")
            _print_disk_trend(disk_history["snapshots"])
        return
    with open(LOG_PATH) as f:
        logs = json.load(f)

    if json_mode:
        print(json.dumps({"version": VERSION, "runs": logs[-limit:],
                          "disk_history": disk_history}, indent=2))
    elif RICH:
        table = Table(title="📊 Cleanup History", show_lines=True)
        table.add_column("Date", style="cyan")
        table.add_column("Freed", style="green", justify="right")
        table.add_column("Disk After", style="yellow")
        for entry in logs[-limit:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            table.add_row(ts, entry["total_freed_human"], entry["disk_after"])
        console.print(table)
        _print_disk_trend(disk_history["snapshots"])
    else:
        print(f"\n{'='*60}")
        print(f"Cleanup History (last {limit} runs)")
        print(f"{'='*60}")
        for entry in logs[-limit:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            print(f"  {ts}  Freed: {entry['total_freed_human']:>10}  {entry['disk_after']}")
        print(f"{'='*60}\n")
        _print_disk_trend(disk_history["snapshots"])


# ── CLI entry point ────────────────────────────────────────────────────────────
def translate_legacy(argv):
    """Map v1 flag spellings onto v2 subcommands so aliases/cron/apps keep working."""
    if not argv:
        return argv
    # Old subcommand names
    aliases = {"preview": "scan", "history": "report"}
    if argv[0] in aliases:
        return [aliases[argv[0]]] + argv[1:]
    if not argv[0].startswith("--"):
        return argv

    flags = set(argv)
    rest = [a for a in argv]

    def strip(flag):
        return [a for a in rest if a != flag]

    if "--install-deps" in flags:
        return ["install-deps"]
    if "--config-show" in flags:
        return ["config", "show"]
    for flag, action in (("--config-enable", "enable"), ("--config-disable", "disable")):
        if flag in flags:
            i = rest.index(flag)
            if i + 1 < len(rest):
                return ["config", action, rest[i + 1]]
    if "--clean" in flags:
        return ["clean"] + strip("--clean")
    if "--preview" in flags:
        return ["scan"] + strip("--preview")
    if "--report" in flags:
        return ["report"] + strip("--report")
    if "--json" in flags and all(a in ("--json", "--category") or rest[i-1] == "--category"
                                 for i, a in enumerate(rest)):
        return ["scan"] + rest  # bare --json [--category X] = menu bar app contract
    return argv


def parse_categories(values):
    cats = []
    for v in values or []:
        cats.extend(c.strip().lower() for c in v.split(",") if c.strip())
    return cats


def filter_targets(targets, categories=None, min_size_mb=None):
    if categories:
        targets = [t for t in targets if t["category"] in categories]
    if min_size_mb is not None:
        targets = measure_targets(targets)
        targets = [t for t in targets if t["size"] >= min_size_mb * 1024 * 1024]
    return targets


def build_parser():
    parser = argparse.ArgumentParser(
        prog="maccleaner",
        description="MacCleaner — macOS developer storage cleanup (see AGENTS.md for the machine interface)")
    parser.add_argument("--version", action="version", version=f"MacCleaner {VERSION}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="Show what can be cleaned + sizes")
    p_scan.add_argument("--category", action="append", help="Limit to category (repeatable or comma-separated)")
    p_scan.add_argument("--min-size", type=float, metavar="MB", help="Hide targets smaller than this")
    p_scan.add_argument("--all", action="store_true", help="Show empty/not-installed targets too")
    p_scan.add_argument("--json", action="store_true", help="Machine-readable output")

    p_clean = sub.add_parser("clean", help="Clean targets (interactive by default)")
    p_clean.add_argument("--yes", action="store_true", help="No prompts: clean all safe targets (or all --targets)")
    p_clean.add_argument("--targets", metavar="ID,ID", help="Clean only these target IDs (from scan)")
    p_clean.add_argument("--category", action="append", help="Limit to category")
    p_clean.add_argument("--min-size", type=float, metavar="MB", help="Skip targets smaller than this")
    p_clean.add_argument("--trash", action="store_true", help="Move to Trash instead of deleting")
    p_clean.add_argument("--json", action="store_true", help="Machine-readable results")
    p_clean.add_argument("--dry-run", action="store_true",
                         help="Show exactly what would be deleted, delete nothing")
    p_clean.add_argument("--notify", action="store_true",
                         help="Post a macOS notification when the clean finishes")

    p_proj = sub.add_parser("projects", help="Find stale build artifacts (node_modules, .venv, target, ...)")
    p_proj.add_argument("--roots", action="append", metavar="DIR", help="Roots to scan (default: config project_roots)")
    p_proj.add_argument("--min-age-days", type=int, metavar="N", help="Only artifacts untouched for N days (default 30)")
    p_proj.add_argument("--clean", action="store_true", help="Delete the found artifacts")
    p_proj.add_argument("--yes", action="store_true", help="With --clean: no prompts")
    p_proj.add_argument("--targets", metavar="ID,ID", help="With --clean: only these artifact IDs")
    p_proj.add_argument("--trash", action="store_true", help="Move to Trash instead of deleting")
    p_proj.add_argument("--json", action="store_true", help="Machine-readable output")
    p_proj.add_argument("--dry-run", action="store_true",
                        help="Show what --clean would delete, delete nothing (implies --clean)")

    p_report = sub.add_parser("report", help="Show cleanup history")
    p_report.add_argument("-n", "--limit", type=int, default=10, help="Number of runs to show")
    p_report.add_argument("--stats", action="store_true",
                          help="Aggregate usage stats (local-only) instead of the run list")
    p_report.add_argument("--json", action="store_true", help="Machine-readable output")

    p_doctor = sub.add_parser("doctor", help="Check environment / install health")
    p_doctor.add_argument("--json", action="store_true", help="Machine-readable output")

    p_config = sub.add_parser("config", help="Show or change configuration")
    csub = p_config.add_subparsers(dest="config_cmd")
    csub.add_parser("show", help="Print current config as JSON")
    csub.add_parser("path", help="Print config file path")
    c_en = csub.add_parser("enable", help="Enable a category")
    c_en.add_argument("category")
    c_dis = csub.add_parser("disable", help="Disable a category")
    c_dis.add_argument("category")
    c_set = csub.add_parser("set", help="Set a config key (value parsed as JSON when possible)")
    c_sync = csub.add_parser("sync", help="Sync config across Macs via iCloud Drive")
    c_sync.add_argument("sync_action", choices=["on", "off", "status"],
                        help="on: move config to iCloud Drive and symlink it; "
                             "off: make it local again; status: show state")
    c_sync.add_argument("--json", action="store_true", help="Machine-readable output (status)")
    c_set.add_argument("key")
    c_set.add_argument("value")

    p_cats = sub.add_parser("categories", help="List categories and their targets")
    p_cats.add_argument("--json", action="store_true", help="Machine-readable output")

    p_disk = sub.add_parser("disk-check",
                            help="Warn when free space is below the configured threshold (cheap; for launchd)")
    p_disk.add_argument("--json", action="store_true", help="Machine-readable output")
    p_disk.add_argument("--no-post", action="store_true",
                        help="Report should_notify without posting -- the caller (the app) delivers it instead")

    p_storage = sub.add_parser("storage-insights",
                               help="Read-only: largest files in Documents/Downloads/Desktop (never deletes)")
    p_storage.add_argument("--json", action="store_true", help="Machine-readable output")

    p_map = sub.add_parser("storage-map",
                           help="Read-only: browse where disk space is going, anywhere on the disk")
    p_map.add_argument("path", nargs="?", default=None,
                       help="Directory to inspect (default: your home folder)")
    p_map.add_argument("--min-size", type=float, default=0,
                       metavar="MB", help="Hide entries smaller than this")
    p_map.add_argument("--json", action="store_true", help="Machine-readable output")

    p_sched = sub.add_parser("schedule",
                             help="Manage the launchd cleanup schedule (weekly/monthly/off/status)")
    p_sched.add_argument("action", choices=["status", "weekly", "monthly", "off"])
    p_sched.add_argument("--json", action="store_true", help="Machine-readable output")

    sub.add_parser("install-deps", help="Install 'rich' for pretty terminal output")

    return parser


def main():
    argv = translate_legacy(sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        show_welcome()
        return

    if args.command == "install-deps":
        subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet"])
        print("✅ Dependencies installed. Re-run your command.")
        return

    if args.command == "schedule":
        if args.action == "status":
            run_schedule_status(json_mode=args.json)
        elif args.action in ("weekly", "monthly"):
            if not run_schedule_install(args.action, json_mode=args.json):
                sys.exit(1)
        else:
            run_schedule_off(json_mode=args.json)
        return

    config = load_config()

    if args.command == "config":
        if args.config_cmd == "show" or args.config_cmd is None:
            print(json.dumps(config, indent=2))
        elif args.config_cmd == "path":
            print(CONFIG_PATH)
        elif args.config_cmd in ("enable", "disable"):
            cmd_config_set_category(config, args.config_cmd, args.category.lower())
        elif args.config_cmd == "set":
            cmd_config_set_key(config, args.key, args.value)
        elif args.config_cmd == "sync":
            run_config_sync(args.sync_action, json_mode=getattr(args, "json", False))
        return

    if args.command == "categories":
        show_categories(config, json_mode=args.json)
        return

    if args.command == "doctor":
        run_doctor(config, json_mode=args.json)
        return

    if args.command == "disk-check":
        run_disk_check(config, json_mode=args.json, post=not args.no_post)
        return

    if args.command == "storage-map":
        show_storage_map(args.path,
                         min_bytes=int(args.min_size * 1024 * 1024),
                         json_mode=args.json)
        return

    if args.command == "storage-insights":
        show_storage_insights(config, json_mode=args.json)
        return

    if args.command == "report":
        if args.stats:
            show_stats(json_mode=args.json)
        else:
            show_report(limit=args.limit, json_mode=args.json)
        return

    if args.command == "projects":
        hits, roots, min_age = scan_projects(config, roots=args.roots, min_age_days=args.min_age_days)
        if args.clean or args.dry_run:
            targets = projects_to_targets(hits)
            if args.targets:
                wanted = {t.strip() for t in args.targets.split(",") if t.strip()}
                targets = [t for t in targets if t["id"] in wanted]
                missing = wanted - {t["id"] for t in targets}
                if missing:
                    print(f"Unknown artifact IDs: {', '.join(sorted(missing))}", file=sys.stderr)
                    sys.exit(1)
            mode = "trash" if args.trash else config.get("delete_mode", "rm")
            if args.dry_run:
                selected = _filter_git_flagged(targets, bypass=bool(args.targets))
                run_dry_run(selected, mode=mode, json_mode=args.json)
                return
            if args.yes:
                targets = _filter_git_flagged(targets, bypass=bool(args.targets))
            run_clean(targets, auto_approve=args.yes, mode=mode,
                      json_mode=args.json, explicit=bool(args.targets) or args.yes)
        elif args.json:
            print(json.dumps({
                "version": VERSION,
                "timestamp": datetime.datetime.now().isoformat(),
                "roots": roots,
                "min_age_days": min_age,
                "total_bytes": sum(h["size_bytes"] for h in hits),
                "artifacts": [
                    {**h, "id": t["id"]}
                    for h, t in zip(hits, projects_to_targets(hits))
                ],
            }, indent=2))
        else:
            print_projects(hits, roots, min_age)
        return

    # scan / clean share target selection
    categories = parse_categories(getattr(args, "category", None))
    target_ids = None
    # targets_given tracks whether --targets was SUPPLIED at all (presence,
    # via `is not None` -- argparse leaves the attribute None when the flag is
    # absent and "" when it is passed empty), independent of whether the value
    # parsed to anything. A garbage value like " , " or an explicit "" parses
    # to an empty target_ids set, but the downstream filter/explicit gate must
    # still key off "was --targets supplied" -- otherwise an empty parsed set
    # reads as "no --targets given", the filter is skipped, and --yes performs
    # a full safe auto-clean instead of the no-op the user asked for.
    targets_given = False
    if args.command == "clean":
        raw_targets = getattr(args, "targets", None)
        if raw_targets is not None:
            targets_given = True
            target_ids = {t.strip() for t in raw_targets.split(",") if t.strip()}
    # categories/target_ids are selection hints only (v2.6 scanner scoping):
    # they let collect_targets skip a dynamic scanner it can prove won't be
    # selected, but never widen what enabled_categories already gates. An
    # empty-but-supplied target_ids correctly scopes scanners OUT here (the
    # any() check in collect_targets is False for an empty set).
    targets = collect_targets(config,
                               categories=set(categories) if categories else None,
                               target_ids=target_ids)
    if categories:
        valid = set(ALL_CATEGORIES)
        unknown = [c for c in categories if c not in valid]
        if unknown:
            print(f"Unknown categories: {', '.join(unknown)}. Available: {', '.join(ALL_CATEGORIES)}", file=sys.stderr)
            sys.exit(1)
        targets = [t for t in targets if t["category"] in categories]
        if not targets:
            # A valid category with zero targets right now is not an error
            # (AGENTS.md: exit 0 covers "nothing to clean") -- tmp/simulators
            # are the first categories that can be enabled and legitimately
            # empty (clean /tmp, no Xcode installed). Only an unknown
            # category name (handled above) is a usage error. Proceed with
            # the empty list so scan/clean still emit well-formed JSON with
            # zero targets / total 0, and note why on stderr.
            enabled = set(config["enabled_categories"])
            disabled = sorted(c for c in categories if c not in enabled)
            if disabled:
                print(f"No targets for {', '.join(categories)} "
                      f"({', '.join(disabled)} disabled — see 'maccleaner categories')",
                      file=sys.stderr)
            else:
                print(f"{', '.join(categories)} enabled but no targets found "
                      f"on this machine right now", file=sys.stderr)

    if args.command == "scan":
        if args.min_size is not None:
            targets = filter_targets(targets, min_size_mb=args.min_size)
        targets = measure_targets(targets)
        if categories or args.min_size is not None:
            record_snapshot()  # partial selection: disk numbers only
        else:
            record_snapshot(*snapshot_fields(targets))
        if args.json:
            scan_json(targets)
        else:
            print_scan(targets, show_all=args.all)
        return

    if args.command == "clean":
        explicit = False
        if targets_given:
            targets = [t for t in targets if t["id"] in target_ids]
            missing = target_ids - {t["id"] for t in targets}
            if missing:
                print(f"Unknown target IDs: {', '.join(sorted(missing))}. Run 'maccleaner scan --json' to list IDs.",
                      file=sys.stderr)
                sys.exit(1)
            explicit = True
        if args.min_size is not None:
            targets = filter_targets(targets, min_size_mb=args.min_size)
        auto = args.yes or config.get("auto_approve", False)
        mode = "trash" if args.trash else config.get("delete_mode", "rm")
        if args.dry_run:
            selected = [t for t in targets if t["safe"] or explicit]
            run_dry_run(selected, mode=mode, json_mode=args.json)
            return
        full = not explicit and not categories and args.min_size is None
        run_clean(targets, auto_approve=auto, mode=mode, json_mode=args.json,
                  explicit=explicit, snapshot_scope="full" if full else "partial",
                  notify=args.notify)
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
