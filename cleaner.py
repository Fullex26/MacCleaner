#!/usr/bin/env python3
"""
MacCleaner - macOS Developer Storage Cleanup Tool
Usage:
  python3 cleaner.py --preview     # Show what would be deleted
  python3 cleaner.py --clean       # Interactive cleanup with confirmations
  python3 cleaner.py --clean --yes # Auto-approve all (for cron/scheduled runs)
  python3 cleaner.py --report      # Show last cleanup report
  python3 cleaner.py --json        # Output results as JSON (for menu bar app)
"""

import os
import sys
import json
import shutil
import argparse
import subprocess
import datetime
from pathlib import Path

# ── Optional rich output ──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import track
    from rich import print as rprint
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None

HOME = Path.home()
CONFIG_PATH = Path(__file__).parent / "config.json"
LOG_PATH = Path(__file__).parent / "report.log"
VERSION = "1.2.0"

# ── Default config ─────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "enabled_categories": [
        "xcode", "docker", "node", "python", "caches", "logs",
        "homebrew", "go", "rust", "ruby", "cocoapods", "gradle", "maven"
    ],
    "skip_paths": [],
    "log_threshold_mb": 100,
    "auto_approve": False,
    "schedule": "weekly"
}

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        # Merge with defaults for any missing keys
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

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
            capture_output=True, text=True, timeout=30
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

# ── Estimate parsers for cmd-based targets ─────────────────────────────────────
def _parse_brew_estimate(output):
    import re
    m = re.search(r'free approximately ([0-9.]+)\s*(KB|MB|GB|TB)', output)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2)
    return int(val * {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[unit])

def _parse_docker_estimate(output):
    import re
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
def get_targets(config):
    skip = [Path(p) for p in config.get("skip_paths", [])]
    targets = []

    def add(category, label, path, safe=True, cmd=None, estimate_cmd=None, estimate_parser=None):
        if path is None:
            p = None
        else:
            p = Path(path) if not isinstance(path, Path) else path
            # Expand ~
            if str(path).startswith("~"):
                p = HOME / str(path)[2:]
        if p is not None and any(str(p).startswith(str(s)) for s in skip):
            return
        if category in config["enabled_categories"]:
            targets.append({
                "category": category,
                "label": label,
                "path": p,
                "safe": safe,
                "cmd": cmd,
                "estimate_cmd": estimate_cmd,
                "estimate_parser": estimate_parser,
            })

    # Xcode
    add("xcode", "Xcode DerivedData",       "~/Library/Developer/Xcode/DerivedData")
    add("xcode", "Xcode Previews",           "~/Library/Developer/Xcode/UserData/Previews")
    add("xcode", "Xcode iOS DeviceSupport",  "~/Library/Developer/Xcode/iOS DeviceSupport")
    add("xcode", "Xcode watchOS DeviceSupport", "~/Library/Developer/Xcode/watchOS DeviceSupport")
    add("xcode", "Xcode Archives",           "~/Library/Developer/Xcode/Archives", safe=False)
    add("xcode", "Simulator unavailable",    None,
        cmd="xcrun simctl delete unavailable 2>/dev/null || true")
    add("xcode", "Simulator runtimes",      "~/Library/Developer/CoreSimulator/Volumes", safe=False)

    # Docker
    add("docker", "Docker unused data", None,
        cmd="docker system prune -f --filter 'until=168h' 2>/dev/null || true",
        estimate_cmd="docker system df 2>/dev/null",
        estimate_parser="docker_df")

    # Node
    add("node", "npm cache",                 "~/.npm/_cacache")
    add("node", "pnpm store",                None,
        cmd="pnpm store prune 2>/dev/null || true",
        estimate_cmd="pnpm store path 2>/dev/null | xargs du -sk 2>/dev/null",
        estimate_parser="du_path")
    add("node", "yarn cache",                "~/.yarn/cache")

    # Python
    add("python", "pip cache",               "~/Library/Caches/pip")
    add("python", "pyenv shims cache",       "~/.pyenv/shims", safe=False)

    # Caches
    add("caches", "Claude app cache",        "~/Library/Application Support/Claude/Cache")
    add("caches", "Claude VM bundles",       "~/Library/Application Support/Claude/vm_bundles")
    add("caches", "Claude Code Cache",       "~/Library/Application Support/Claude/Code Cache")
    add("caches", "Claude GPU Cache",        "~/Library/Application Support/Claude/GPUCache")
    add("caches", "Cursor cache",            "~/Library/Application Support/Cursor/Cache")
    add("caches", "Chrome cache",            "~/Library/Application Support/Google/Chrome/Default/Cache")
    add("caches", "Spotify cache",           "~/Library/Application Support/Spotify/Data")
    add("caches", "General app caches",      "~/Library/Caches", safe=False)

    # Homebrew
    add("homebrew", "Homebrew cache", None,
        cmd="brew cleanup --prune=all 2>/dev/null || true",
        estimate_cmd="brew cleanup --dry-run 2>/dev/null",
        estimate_parser="brew_dry_run")
    add("homebrew", "Homebrew unused deps", None,
        cmd="brew autoremove 2>/dev/null || true")

    # Go
    add("go", "Go module cache",   "~/go/pkg/mod")
    add("go", "Go build cache",    "~/go/pkg/cache")

    # Rust / Cargo
    add("rust", "Cargo registry",  "~/.cargo/registry")
    add("rust", "Cargo git cache", "~/.cargo/git")

    # Ruby
    add("ruby", "Ruby gem cleanup", None,
        cmd="gem cleanup 2>/dev/null || true")

    # CocoaPods
    add("cocoapods", "CocoaPods cache", "~/Library/Caches/CocoaPods")

    # Gradle / Android
    add("gradle", "Gradle caches", "~/.gradle/caches")

    # Maven
    add("maven", "Maven local repo", "~/.m2/repository", safe=False)

    # Logs
    threshold = config.get("log_threshold_mb", 100) * 1024 * 1024
    log_dir = HOME / "Library/Logs"
    if "logs" in config["enabled_categories"] and log_dir.exists():
        for item in log_dir.iterdir():
            size = get_size(item)
            if size > threshold:
                targets.append({
                    "category": "logs",
                    "label": f"Log: {item.name}",
                    "path": item,
                    "safe": True,
                    "cmd": None,
                    "estimate_cmd": None,
                    "estimate_parser": None,
                })

    return targets

# ── Core actions ───────────────────────────────────────────────────────────────
def measure_targets(targets):
    """Attach size info to each target."""
    for t in targets:
        if t["path"] and t["path"].exists():
            t["size"] = get_size(t["path"])
        elif t.get("estimate_cmd"):
            t["size"] = _run_estimate(t["estimate_cmd"], t.get("estimate_parser"))
        else:
            t["size"] = 0
    return targets

def delete_target(t) -> int:
    """Delete a target. Returns bytes freed."""
    freed = 0
    if t.get("cmd"):
        subprocess.run(t["cmd"], shell=True, capture_output=True)
        return 0  # Can't easily measure cmd-based targets
    path = t["path"]
    if path and path.exists():
        freed = get_size(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    return freed

# ── Config commands ─────────────────────────────────────────────────────────────
def cmd_config_show(cfg):
    print(json.dumps(cfg, indent=2))

def cmd_config_set(cfg, action, category):
    valid = set(DEFAULT_CONFIG["enabled_categories"])
    if category not in valid:
        print(f"Unknown category '{category}'. Valid: {', '.join(sorted(valid))}")
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

            # Header
            header = " MacCleaner — Space=toggle  a=all  n=none  Enter=confirm  q=cancel"
            stdscr.addstr(0, 0, header[:w-1], curses.A_BOLD)
            stdscr.addstr(1, 0, "─" * (w - 1))

            # Items
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

            # Footer
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
            "  [bold cyan]maccleaner preview[/bold cyan]        — see what can be cleaned\n"
            "  [bold cyan]maccleaner clean[/bold cyan]          — start cleaning (interactive)\n"
            "  [bold cyan]maccleaner report[/bold cyan]         — show cleanup history\n\n"
            "  [dim]maccleaner clean --yes   — auto-clean all safe items[/dim]\n"
            "  [dim]maccleaner --category xcode  — scope to one category[/dim]",
            title="Welcome",
            border_style="cyan"
        ))
    else:
        print(f"\n{'='*50}")
        print("🧹 MacCleaner — Free up space on your Mac")
        print(f"{'='*50}")
        print("  maccleaner preview    — see what can be cleaned")
        print("  maccleaner clean      — start cleaning (interactive)")
        print("  maccleaner report     — show cleanup history")
        print(f"{'='*50}\n")

def print_preview(targets):
    targets = measure_targets(targets)
    total = sum(t["size"] for t in targets)

    if RICH:
        table = Table(title="🧹 MacCleaner Preview", show_lines=True)
        table.add_column("Category", style="cyan", width=12)
        table.add_column("Item", style="white")
        table.add_column("Size", style="yellow", justify="right")
        table.add_column("Safe?", justify="center")

        for t in sorted(targets, key=lambda x: x["size"], reverse=True):
            size_str = (fmt_size(t["size"]) if t["size"]
                        else "~unknown" if t.get("estimate_cmd")
                        else "cmd-based")
            safe_str = "✅" if t["safe"] else "⚠️"
            table.add_row(t["category"], t["label"], size_str, safe_str)

        console.print(table)
        console.print(Panel(
            f"[bold green]Total reclaimable: {fmt_size(total)}[/bold green]\n"
            f"[dim]Current disk: {disk_free()}[/dim]\n\n"
            f"[bold]→ Run [cyan]maccleaner clean[/cyan] to start cleaning[/bold]",
            title="Summary"
        ))
    else:
        print(f"\n{'='*60}")
        print(f"MacCleaner Preview — {disk_free()}")
        print(f"{'='*60}")
        for t in sorted(targets, key=lambda x: x["size"], reverse=True):
            size_str = (fmt_size(t["size"]) if t["size"]
                        else "~unknown" if t.get("estimate_cmd")
                        else "cmd")
            safe = "safe" if t["safe"] else "REVIEW"
            print(f"  [{t['category']:8}] {t['label']:<45} {size_str:>10}  {safe}")
        print(f"\n  Total reclaimable: {fmt_size(total)}")
        print(f"\n  → Run 'maccleaner clean' to start cleaning")
        print(f"{'='*60}\n")

def run_clean(targets, auto_approve=False):
    targets = measure_targets(targets)
    total_freed = 0
    results = []

    # Sort: safe items first, largest first within each group
    safe_targets = sorted([t for t in targets if t["safe"]], key=lambda x: x["size"], reverse=True)
    review_targets = sorted([t for t in targets if not t["safe"]], key=lambda x: x["size"], reverse=True)
    ordered = safe_targets + review_targets

    print(f"\n🧹 MacCleaner — {disk_free()}\n")

    if not auto_approve:
        selected, confirmed = run_tui_clean(ordered)
        if selected is None:
            pass  # TUI unavailable, fall through to y/N loop
        elif not confirmed:
            print("Cancelled.")
            return 0, []
        else:
            # Execute TUI selection
            for t in selected:
                size_str = fmt_size(t["size"]) if t["size"] else "~unknown"
                print(f"  ✅ Deleting: {t['label']} ({size_str})")
                freed = delete_target(t)
                total_freed += freed
                results.append({"label": t["label"], "freed": freed, "status": "deleted"})
            # Mark skipped items
            selected_labels = {t["label"] for t in selected}
            for t in ordered:
                if t["label"] not in selected_labels:
                    results.append({"label": t["label"], "freed": 0, "status": "skipped"})
            write_log(total_freed, results)
            print(f"\n{'='*50}")
            print(f"  Total freed: {fmt_size(total_freed)}")
            print(f"  Disk now:    {disk_free()}")
            print(f"{'='*50}\n")
            return total_freed, results

    # y/N loop — runs for auto_approve=True or TUI fallback (non-interactive)
    for t in ordered:
        size_str = (fmt_size(t["size"]) if t["size"]
                    else "~unknown" if t.get("estimate_cmd")
                    else "cmd-based")
        safe_label = "" if t["safe"] else " ⚠️  REVIEW BEFORE DELETING"
        prompt_str = f"  Delete [{t['category']}] {t['label']} ({size_str}){safe_label}? [y/N] "

        if auto_approve and t["safe"]:
            print(f"  ✅ Auto-deleting: {t['label']} ({size_str})")
            freed = delete_target(t)
            total_freed += freed
            results.append({"label": t["label"], "freed": freed, "status": "deleted"})
        else:
            ans = input(prompt_str).strip().lower()
            if ans == "y":
                freed = delete_target(t)
                total_freed += freed
                results.append({"label": t["label"], "freed": freed, "status": "deleted"})
                print(f"    → Deleted ✓")
            else:
                results.append({"label": t["label"], "freed": 0, "status": "skipped"})
                print(f"    → Skipped")

    # Write log
    write_log(total_freed, results)

    print(f"\n{'='*50}")
    print(f"  Total freed: {fmt_size(total_freed)}")
    print(f"  Disk now:    {disk_free()}")
    print(f"{'='*50}\n")

    return total_freed, results

def run_json_output(targets):
    """Output JSON for menu bar app consumption."""
    targets = measure_targets(targets)
    total = sum(t["size"] for t in targets)
    output = {
        "timestamp": datetime.datetime.now().isoformat(),
        "disk": disk_free(),
        "total_reclaimable_bytes": total,
        "total_reclaimable_human": fmt_size(total),
        "targets": [
            {
                "category": t["category"],
                "label": t["label"],
                "size_bytes": t["size"],
                "size_human": fmt_size(t["size"]),
                "safe": t["safe"],
            }
            for t in sorted(targets, key=lambda x: x["size"], reverse=True)
        ]
    }
    print(json.dumps(output, indent=2))

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

def show_report():
    if not LOG_PATH.exists():
        print("No cleanup history found. Run --clean first.")
        return
    with open(LOG_PATH) as f:
        logs = json.load(f)

    if RICH:
        table = Table(title="📊 Cleanup History", show_lines=True)
        table.add_column("Date", style="cyan")
        table.add_column("Freed", style="green", justify="right")
        table.add_column("Disk After", style="yellow")
        for entry in logs[-10:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            table.add_row(ts, entry["total_freed_human"], entry["disk_after"])
        console.print(table)
    else:
        print(f"\n{'='*60}")
        print("Cleanup History (last 10 runs)")
        print(f"{'='*60}")
        for entry in logs[-10:]:
            ts = entry["timestamp"][:16].replace("T", " ")
            print(f"  {ts}  Freed: {entry['total_freed_human']:>10}  {entry['disk_after']}")
        print(f"{'='*60}\n")

# ── CLI entry point ────────────────────────────────────────────────────────────
def main():
    # Translate natural subcommands → --flags so existing aliases/cron are unaffected
    _SUBCOMMANDS = {
        "clean":   "--clean",
        "preview": "--preview",
        "scan":    "--preview",
        "report":  "--report",
        "history": "--report",
        "help":    "--help",
        "version": "--version",
    }
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        sys.argv[1] = _SUBCOMMANDS[sys.argv[1]]

    parser = argparse.ArgumentParser(description="MacCleaner — Developer storage cleanup")
    parser.add_argument("--version",        action="version", version=f"MacCleaner {VERSION}")
    parser.add_argument("--preview",        action="store_true", help="Show what would be deleted")
    parser.add_argument("--clean",          action="store_true", help="Run interactive cleanup")
    parser.add_argument("--yes",            action="store_true", help="Auto-approve safe deletions")
    parser.add_argument("--report",         action="store_true", help="Show cleanup history")
    parser.add_argument("--json",           action="store_true", help="Output JSON (for menu bar app)")
    parser.add_argument("--install-deps",   action="store_true", help="Install rich for pretty output")
    parser.add_argument("--category",       metavar="CATEGORY",
                        help="Only operate on this category (e.g. xcode, docker, node)")
    parser.add_argument("--config-show",    action="store_true", help="Print current config as JSON")
    parser.add_argument("--config-enable",  metavar="CATEGORY", help="Enable a category in config")
    parser.add_argument("--config-disable", metavar="CATEGORY", help="Disable a category in config")
    args = parser.parse_args()

    if args.install_deps:
        subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet"])
        print("✅ Dependencies installed. Re-run your command.")
        return

    config = load_config()

    # Config commands — short-circuit before slow target scan
    if args.config_show:
        cmd_config_show(config)
        return
    if args.config_enable:
        cmd_config_set(config, "enable", args.config_enable)
        return
    if args.config_disable:
        cmd_config_set(config, "disable", args.config_disable)
        return

    targets = get_targets(config)

    if args.category:
        targets = [t for t in targets if t["category"] == args.category.lower()]
        if not targets:
            print(f"No targets for '{args.category}'. Available: {', '.join(DEFAULT_CONFIG['enabled_categories'])}")
            sys.exit(1)

    if args.clean:
        auto = args.yes or config.get("auto_approve", False)
        run_clean(targets, auto_approve=auto)
    elif args.preview:
        print_preview(targets)
    elif args.report:
        show_report()
    elif args.json:
        run_json_output(targets)
    else:
        show_welcome()

if __name__ == "__main__":
    main()
