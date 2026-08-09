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


LOG_PATH = _resolve_state_path("MACCLEANER_LOG", "report.log")
SNAPSHOTS_PATH = _resolve_state_path("MACCLEANER_SNAPSHOTS", "snapshots.log")
ALERTS_PATH = _resolve_state_path("MACCLEANER_ALERTS", "alerts.json")
CONFIG_PATH = _resolve_state_path("MACCLEANER_CONFIG", "config.json")
SNAPSHOT_CAP = 365
VERSION = "2.4.0"

# ── Default config ─────────────────────────────────────────────────────────────
ALL_CATEGORIES = [
    "xcode", "docker", "node", "python", "caches", "logs", "homebrew",
    "go", "rust", "ruby", "cocoapods", "gradle", "maven",
    "ai", "ide", "browsers", "system",
    "flutter", "php", "vms",
    "tmp", "simulators",
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
    "ai":        "Downloaded AI models (Hugging Face, PyTorch, Ollama) — re-downloadable",
    "ide":       "Editor caches (VS Code, JetBrains)",
    "browsers":  "Browser caches (Arc, Brave, Edge, Firefox)",
    "system":    "Trash and iOS device backups — review carefully",
    "flutter":   "Dart & Flutter pub package cache",
    "php":       "Composer package cache",
    "vms":       "VM disks and container runtimes (Colima, Vagrant, minikube) — review carefully",
    "tmp":       "Stale build artifacts in /private/tmp left by tools and AI coding sessions — review carefully",
    "simulators": "Stale iOS simulator devices and unused runtime images (via simctl) — review carefully",
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
    "tmp_min_age_days": 3,           # /tmp dirs younger than this are never offered
    "simulator_stale_days": 30,      # simulators not booted for this long count as stale
    "notifications": True,           # notify when a scheduled clean finishes
    "low_disk_alerts": True,         # warn when free space drops below the threshold
    "low_disk_threshold_gb": 10,     # the low-disk warning threshold
    "full_refresh_hours": 6,         # how often the app runs a full scan (app-side)
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
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
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


# ── Size helpers ───────────────────────────────────────────────────────────────
def get_size(path: Path) -> int:
    """Return size in bytes of a path (file or directory)."""
    if not path.exists():
        return 0
    try:
        result = subprocess.run(
            ["du", "-sk", str(path)],
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
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            return f"Used: {parts[2]} / {parts[1]} ({parts[4]})"
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


def _parse_docker_estimate(output):
    total = 0
    for line in output.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            m = re.match(r'([0-9.]+)(B|KB|MB|GB|TB)', parts[3])
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
            estimate_cmd=None, estimate_parser=None, desc="", empty_only=False):
        if category not in enabled:
            return
        p, pattern = None, None
        if path is not None:
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


def collect_targets(config, all_categories=False):
    """Static targets plus dynamic scanner targets (tmp, simulators).
    scan/clean/dry-run call this; `categories` deliberately keeps calling
    get_targets() — dynamic per-dir IDs are unstable and the completions'
    live-ID pipeline must not see them."""
    targets = get_targets(config, all_categories=all_categories)
    enabled = set(ALL_CATEGORIES) if all_categories else set(config["enabled_categories"])
    if "tmp" in enabled:
        targets += tmp_to_targets(scan_tmp_artifacts(config))
    if "simulators" in enabled:
        targets += scan_simulator_targets(config)
    return targets


def _target_paths(t):
    """Concrete filesystem paths for a target (glob patterns expanded).

    Glob matches are filtered against skip_paths here because the prefix
    check in add() can only see the pattern, not its expansions."""
    if t.get("glob"):
        skip = t.get("skip", [])
        return [Path(p) for p in sorted(globmod.glob(t["glob"]))
                if not any(p.startswith(s) for s in skip)]
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
    """Only paths strictly inside the user's home are deletable."""
    try:
        rp = path.absolute()
    except Exception:
        return False
    home = HOME.absolute()
    if str(rp).rstrip("/") in ("", "/", str(home).rstrip("/")):
        return False
    return str(rp).startswith(str(home) + os.sep)


def _tmp_scan_path_allowed(path):
    """The single, narrow carve-out to the home-only rule: a path is
    deletable outside $HOME only when it is a DIRECT child of the tmp scan
    root (resolved, so /tmp symlinking to /private/tmp is handled) — and
    delete_target additionally requires the target to carry the tmp_scan
    marker that only scan_tmp_artifacts()/tmp_to_targets() set. There is
    deliberately no config key that widens this."""
    try:
        rp = Path(path).resolve()
        root = TMP_SCAN_ROOT.resolve()
    except OSError:
        return False
    return rp.parent == root and rp != root


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
    total = sum(t["size"] for t in targets)
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


def scan_json(targets, extra=None):
    targets = measure_targets(targets)
    total = sum(t["size"] for t in targets)
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
        _notify(f"MacCleaner freed {fmt_size(total_freed)}",
                f"{cleaned} item{'s' if cleaned != 1 else ''} cleaned · "
                f"{fmt_size(disk_stats()['free_bytes'])} free")

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

# ── Simulator dynamic targets ─────────────────────────────────────────────────
# Device UDIDs and runtime identifiers from `simctl ... -j` output end up
# interpolated into shell cmd strings (delete_target runs cmd targets with
# shell=True) — anything that fails these shapes is dropped rather than ever
# reaching a shell, so a malformed/hostile simctl response can't inject.
_SIMCTL_UDID_RE = re.compile(r"[0-9A-Fa-f-]{8,}\Z")
_SIMCTL_RUNTIME_ID_RE = re.compile(r"[A-Za-z0-9.-]+\Z")


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
        if (p / "info.plist").exists() and (p / "Build").is_dir() and (p / "Index.noindex").is_dir():
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


def scan_tmp_artifacts(config):
    """Top-level-only scan of TMP_SCAN_ROOT for stale build junk.

    Guards (all mandatory, see the v2.5 design doc): min-age via
    tmp_min_age_days, symlinks never followed or classified, other-owner
    dirs skipped, active AI-session prefixes skipped, plain files skipped."""
    min_age = config.get("tmp_min_age_days", 3)
    cutoff = time.time() - min_age * 86400
    hits = []
    try:
        entries = list(os.scandir(TMP_SCAN_ROOT))
    except OSError:
        return hits
    uid = os.getuid()
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
            if any(e.name.startswith(pre) for pre in TMP_ACTIVE_PREFIXES):
                continue
            st = e.stat(follow_symlinks=False)
            if st.st_uid != uid or st.st_mtime > cutoff:
                continue
            kind = _classify_tmp_dir(Path(e.path))
            if kind:
                hits.append({"path": Path(e.path), "kind": kind, "mtime": st.st_mtime})
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
def _launchd_is_loaded(label: str) -> bool:
    """True only if launchd currently has `label` loaded — a plist file on
    disk merely means it was written, not that bootstrap/load succeeded or
    that it's still loaded (finding I1)."""
    try:
        r = subprocess.run(["launchctl", "list", label],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def run_doctor(config, json_mode=False):
    checks = []

    def check(name, status, ok=True):
        checks.append({"name": name, "status": status, "ok": ok})

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
        loaded = [a["label"] for a in st["agents"] if a["loaded"]]
        not_loaded = [a["label"] for a in st["agents"] if not a["loaded"]]
        if loaded:
            note = f"launchd: {', '.join(loaded)}"
            if not_loaded:
                note += (f" (plist present but not loaded: {', '.join(not_loaded)}"
                         " — run scheduler.sh weekly to reload)")
            if st["legacy_cron"]:
                note += " (plus a legacy cron entry — run scheduler.sh weekly to clean up)"
            check("Schedule", note)
        elif not_loaded:
            # The plist exists but launchd doesn't have it loaded — distinct
            # from "never installed at all" (finding I1).
            check("Schedule",
                  f"plist present but not loaded: {', '.join(not_loaded)}"
                  " — run scheduler.sh weekly to reload", ok=False)
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

    app_paths = [HOME / "Applications/MacCleaner.app", Path("/Applications/MacCleaner.app")]
    check("Menu bar app", "installed" if any(p.exists() for p in app_paths) else "not installed")

    for tool in ["brew", "docker", "xcrun", "node", "npm", "pnpm", "yarn", "bun", "deno",
                 "go", "cargo", "gem", "pod", "gradle", "mvn", "uv", "ollama",
                 "conda", "dart", "composer", "terraform", "colima", "vagrant", "minikube"]:
        present = shutil.which(tool) is not None
        check(f"tool: {tool}", "found" if present else "not found (its targets will be skipped)")

    ds = disk_stats()
    check("Disk", f"{fmt_size(ds['free_bytes'])} free of {fmt_size(ds['total_bytes'])} ({ds['percent_used']}% used)")

    all_ok = all(c["ok"] for c in checks)

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

    return all_ok


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


def run_disk_check(config, json_mode=False):
    """Cheap enough to run hourly: one disk_usage call, no measurement, no
    snapshot. Always exits 0 — it is a monitor, not a check that fails."""
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
        notified = _notify(
            f"Low disk space: {fmt_size(free)} free",
            f"Below your {fmt_size(threshold)} threshold — "
            f"open MacCleaner to reclaim space.")
        # Only stamp the throttle when the banner actually posted — otherwise
        # a failed notification would suppress retries for the next 24h even
        # though the user never saw anything (finding M5).
        persist_state = notified
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
        agents.append({"label": label,
                       "plist_present": present,
                       "loaded": _launchd_is_loaded(label) if present else False})
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
        if a["loaded"]:
            print(f"✅ {a['label']} (launchd)")
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
    c_set.add_argument("key")
    c_set.add_argument("value")

    p_cats = sub.add_parser("categories", help="List categories and their targets")
    p_cats.add_argument("--json", action="store_true", help="Machine-readable output")

    p_disk = sub.add_parser("disk-check",
                            help="Warn when free space is below the configured threshold (cheap; for launchd)")
    p_disk.add_argument("--json", action="store_true", help="Machine-readable output")

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
        return

    if args.command == "categories":
        show_categories(config, json_mode=args.json)
        return

    if args.command == "doctor":
        run_doctor(config, json_mode=args.json)
        return

    if args.command == "disk-check":
        run_disk_check(config, json_mode=args.json)
        return

    if args.command == "report":
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
    targets = collect_targets(config)
    categories = parse_categories(getattr(args, "category", None))
    if categories:
        valid = set(ALL_CATEGORIES)
        unknown = [c for c in categories if c not in valid]
        if unknown:
            print(f"Unknown categories: {', '.join(unknown)}. Available: {', '.join(ALL_CATEGORIES)}", file=sys.stderr)
            sys.exit(1)
        targets = [t for t in targets if t["category"] in categories]
        if not targets:
            print(f"No targets for {', '.join(categories)} (category may be disabled — see 'maccleaner categories')", file=sys.stderr)
            sys.exit(1)

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
        if args.targets:
            wanted = {t.strip() for t in args.targets.split(",") if t.strip()}
            targets = [t for t in targets if t["id"] in wanted]
            missing = wanted - {t["id"] for t in targets}
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
