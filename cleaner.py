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

# ── Default config ─────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "enabled_categories": [
        "xcode", "docker", "node", "python", "caches", "logs"
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

# ── Target definitions ─────────────────────────────────────────────────────────
def get_targets(config):
    skip = [Path(p) for p in config.get("skip_paths", [])]
    targets = []

    def add(category, label, path, safe=True, cmd=None):
        p = Path(path) if not isinstance(path, Path) else path
        # Expand ~ 
        if str(path).startswith("~"):
            p = HOME / str(path)[2:]
        if any(str(p).startswith(str(s)) for s in skip):
            return
        if category in config["enabled_categories"]:
            targets.append({
                "category": category,
                "label": label,
                "path": p,
                "safe": safe,
                "cmd": cmd,  # Optional shell command instead of rm -rf
            })

    # Xcode
    add("xcode", "Xcode DerivedData",       "~/Library/Developer/Xcode/DerivedData")
    add("xcode", "Xcode Previews",           "~/Library/Developer/Xcode/UserData/Previews")
    add("xcode", "Xcode iOS DeviceSupport",  "~/Library/Developer/Xcode/iOS DeviceSupport")
    add("xcode", "Xcode watchOS DeviceSupport", "~/Library/Developer/Xcode/watchOS DeviceSupport")
    add("xcode", "Xcode Archives",           "~/Library/Developer/Xcode/Archives", safe=False)
    add("xcode", "Simulator unavailable",    None,
        cmd="xcrun simctl delete unavailable 2>/dev/null || true")

    # Docker
    add("docker", "Docker unused data",      None,
        cmd="docker system prune -f --filter 'until=168h' 2>/dev/null || true")

    # Node
    add("node", "npm cache",                 "~/.npm/_cacache")
    add("node", "pnpm store",                None, cmd="pnpm store prune 2>/dev/null || true")
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
                })

    return targets

# ── Core actions ───────────────────────────────────────────────────────────────
def measure_targets(targets):
    """Attach size info to each target."""
    for t in targets:
        if t["path"] and t["path"].exists():
            t["size"] = get_size(t["path"])
        else:
            t["size"] = 0  # cmd-based targets estimated after
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

# ── Output modes ───────────────────────────────────────────────────────────────
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
            size_str = fmt_size(t["size"]) if t["size"] else "cmd-based"
            safe_str = "✅" if t["safe"] else "⚠️"
            table.add_row(t["category"], t["label"], size_str, safe_str)

        console.print(table)
        console.print(Panel(
            f"[bold green]Total reclaimable: {fmt_size(total)}[/bold green]\n"
            f"[dim]Current disk: {disk_free()}[/dim]",
            title="Summary"
        ))
    else:
        print(f"\n{'='*60}")
        print(f"MacCleaner Preview — {disk_free()}")
        print(f"{'='*60}")
        for t in sorted(targets, key=lambda x: x["size"], reverse=True):
            size_str = fmt_size(t["size"]) if t["size"] else "cmd"
            safe = "safe" if t["safe"] else "REVIEW"
            print(f"  [{t['category']:8}] {t['label']:<45} {size_str:>10}  {safe}")
        print(f"\n  Total reclaimable: {fmt_size(total)}")
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

    for t in ordered:
        size_str = fmt_size(t["size"]) if t["size"] else "cmd-based"
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
    parser = argparse.ArgumentParser(description="MacCleaner — Developer storage cleanup")
    parser.add_argument("--preview", action="store_true", help="Show what would be deleted")
    parser.add_argument("--clean",   action="store_true", help="Run interactive cleanup")
    parser.add_argument("--yes",     action="store_true", help="Auto-approve safe deletions")
    parser.add_argument("--report",  action="store_true", help="Show cleanup history")
    parser.add_argument("--json",    action="store_true", help="Output JSON (for menu bar app)")
    parser.add_argument("--install-deps", action="store_true", help="Install rich for pretty output")
    args = parser.parse_args()

    if args.install_deps:
        subprocess.run([sys.executable, "-m", "pip", "install", "rich", "--quiet"])
        print("✅ Dependencies installed. Re-run your command.")
        return

    config = load_config()
    targets = get_targets(config)

    if args.preview:
        print_preview(targets)
    elif args.clean:
        auto = args.yes or config.get("auto_approve", False)
        run_clean(targets, auto_approve=auto)
    elif args.report:
        show_report()
    elif args.json:
        run_json_output(targets)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
