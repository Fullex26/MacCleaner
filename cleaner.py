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
import glob as globmod
import time
import shutil
import argparse
import subprocess
import datetime
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
CONFIG_PATH = Path(os.environ.get("MACCLEANER_CONFIG", Path(__file__).parent / "config.json"))
LOG_PATH = Path(os.environ.get("MACCLEANER_LOG", Path(__file__).parent / "report.log"))
VERSION = "2.0.0"

# ── Default config ─────────────────────────────────────────────────────────────
ALL_CATEGORIES = [
    "xcode", "docker", "node", "python", "caches", "logs", "homebrew",
    "go", "rust", "ruby", "cocoapods", "gradle", "maven",
    "ai", "ide", "browsers", "system",
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
}

DEFAULT_CONFIG = {
    "enabled_categories": list(ALL_CATEGORIES),
    "skip_paths": [],
    "log_threshold_mb": 100,
    "auto_approve": False,
    "schedule": "weekly",
    "delete_mode": "rm",  # "rm" = delete immediately, "trash" = move to ~/.Trash
    "project_roots": ["~/Documents", "~/Developer", "~/Projects", "~/Code", "~/dev"],
    "project_min_age_days": 30,
}


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy


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


def _run_estimate(estimate_cmd, parser):
    try:
        r = subprocess.run(estimate_cmd, shell=True, capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            return 0
        return {
            "brew_dry_run": _parse_brew_estimate,
            "docker_df":    _parse_docker_estimate,
            "du_path":      _parse_du_estimate,
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

    # AI models
    add("ai", "huggingface-hub", "Hugging Face hub cache", "~/.cache/huggingface", safe=False,
        desc="Downloaded models/datasets — can be very large; re-downloaded on demand")
    add("ai", "torch-hub", "PyTorch hub cache", "~/.cache/torch", safe=False,
        desc="Downloaded PyTorch models and weights")
    add("ai", "ollama-models", "Ollama models", "~/.ollama/models", safe=False,
        desc="Local Ollama models — re-pull with 'ollama pull' if needed")

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
            # cmd strings are static literals from get_targets() (need `|| true`
            # and pipes) — never user input, so shell=True is safe here
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


def run_clean(targets, auto_approve=False, mode="rm", json_mode=False, explicit=False):
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
    hits.sort(key=lambda h: h["size_bytes"], reverse=True)
    return hits, [str(r) for r in roots], min_age


def projects_to_targets(hits):
    targets = []
    for h in hits:
        rel = os.path.relpath(h["path"], str(HOME))
        targets.append({
            "id": f"project-{slugify(rel)}",
            "category": "projects",
            "label": f"{h['kind']} — {os.path.relpath(h['project'], str(HOME))}",
            "description": f"Stale {h['kind']} ({h['age_days']} days old)",
            "path": Path(h["path"]),
            "glob": None,
            "safe": False,
            "cmd": None,
            "estimate_cmd": None,
            "estimate_parser": None,
            "empty_only": False,
            "size": h["size_bytes"],
            "exists": True,
        })
    return targets


def print_projects(hits, roots, min_age):
    total = sum(h["size_bytes"] for h in hits)
    if RICH:
        table = Table(title=f"📦 Stale project artifacts (≥{min_age} days old)", show_lines=False)
        table.add_column("Artifact", style="cyan")
        table.add_column("Project", style="white")
        table.add_column("Age", justify="right")
        table.add_column("Size", style="yellow", justify="right")
        for h in hits:
            table.add_row(h["kind"], os.path.relpath(h["project"], str(HOME)),
                          f"{h['age_days']}d", fmt_size(h["size_bytes"]))
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
            print(f"  {h['kind']:<14} {proj:<40} {h['age_days']:>4}d {fmt_size(h['size_bytes']):>10}")
        print(f"\n  Total: {fmt_size(total)} across {len(hits)} artifacts")
        print(f"  Roots scanned: {', '.join(roots)}")
        print(f"\n  → Run 'maccleaner projects --clean' to remove them")
        print(f"{'='*72}\n")


# ── Doctor ──────────────────────────────────────────────────────────────────────
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
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        scheduled = r.returncode == 0 and "cleaner.py" in r.stdout
        check("Schedule", "cron job active" if scheduled else "no cron schedule (run scheduler.sh weekly)")
    except Exception:
        check("Schedule", "could not read crontab")

    app_paths = [HOME / "Applications/MacCleaner.app", Path("/Applications/MacCleaner.app")]
    check("Menu bar app", "installed" if any(p.exists() for p in app_paths) else "not installed")

    for tool in ["brew", "docker", "xcrun", "node", "npm", "pnpm", "yarn", "bun", "deno",
                 "go", "cargo", "gem", "pod", "gradle", "mvn", "uv", "ollama"]:
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
    with open(LOG_PATH, "w") as f:
        json.dump(logs, f, indent=2)


def show_report(limit=10, json_mode=False):
    if not LOG_PATH.exists():
        if json_mode:
            print(json.dumps({"version": VERSION, "runs": []}))
        else:
            print("No cleanup history found. Run 'maccleaner clean' first.")
        return
    with open(LOG_PATH) as f:
        logs = json.load(f)

    if json_mode:
        print(json.dumps({"version": VERSION, "runs": logs[-limit:]}, indent=2))
    elif RICH:
        table = Table(title="📊 Cleanup History", show_lines=True)
        table.add_column("Date", style="cyan")
        table.add_column("Freed", style="green", justify="right")
        table.add_column("Disk After", style="yellow")
        for entry in logs[-limit:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            table.add_row(ts, entry["total_freed_human"], entry["disk_after"])
        console.print(table)
    else:
        print(f"\n{'='*60}")
        print(f"Cleanup History (last {limit} runs)")
        print(f"{'='*60}")
        for entry in logs[-limit:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            print(f"  {ts}  Freed: {entry['total_freed_human']:>10}  {entry['disk_after']}")
        print(f"{'='*60}\n")


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

    p_proj = sub.add_parser("projects", help="Find stale build artifacts (node_modules, .venv, target, ...)")
    p_proj.add_argument("--roots", action="append", metavar="DIR", help="Roots to scan (default: config project_roots)")
    p_proj.add_argument("--min-age-days", type=int, metavar="N", help="Only artifacts untouched for N days (default 30)")
    p_proj.add_argument("--clean", action="store_true", help="Delete the found artifacts")
    p_proj.add_argument("--yes", action="store_true", help="With --clean: no prompts")
    p_proj.add_argument("--targets", metavar="ID,ID", help="With --clean: only these artifact IDs")
    p_proj.add_argument("--trash", action="store_true", help="Move to Trash instead of deleting")
    p_proj.add_argument("--json", action="store_true", help="Machine-readable output")

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

    if args.command == "report":
        show_report(limit=args.limit, json_mode=args.json)
        return

    if args.command == "projects":
        hits, roots, min_age = scan_projects(config, roots=args.roots, min_age_days=args.min_age_days)
        if args.clean:
            targets = projects_to_targets(hits)
            if args.targets:
                wanted = {t.strip() for t in args.targets.split(",") if t.strip()}
                targets = [t for t in targets if t["id"] in wanted]
                missing = wanted - {t["id"] for t in targets}
                if missing:
                    print(f"Unknown artifact IDs: {', '.join(sorted(missing))}", file=sys.stderr)
                    sys.exit(1)
            mode = "trash" if args.trash else config.get("delete_mode", "rm")
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
    targets = get_targets(config)
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
        run_clean(targets, auto_approve=auto, mode=mode, json_mode=args.json, explicit=explicit)
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        sys.exit(0)
