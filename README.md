2# 🧹 MacCleaner

> macOS developer storage cleanup tool — CLI + menu bar app.

[![Build](https://img.shields.io/github/actions/workflow/status/Fullex26/MacCleaner/ci.yml?branch=main&style=for-the-badge&label=BUILD)](https://github.com/Fullex26/MacCleaner/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Fullex26/MacCleaner?style=for-the-badge&label=RELEASE)](https://github.com/Fullex26/MacCleaner/releases/latest)
[![Stars](https://img.shields.io/github/stars/Fullex26/MacCleaner?style=for-the-badge&label=STARS)](https://github.com/Fullex26/MacCleaner/stargazers)
[![License](https://img.shields.io/github/license/Fullex26/MacCleaner?style=for-the-badge&label=LICENSE)](LICENSE)

MacCleaner finds and removes developer detritus that accumulates silently — Xcode build caches, Docker layers, Node module stores, pip caches, and more. A single command can reclaim tens of gigabytes.

---

## Install

```bash
git clone https://github.com/Fullex26/MacCleaner
cd MacCleaner
bash install.sh
```

Installs to `~/mac-cleaner/`, adds shell aliases, optionally schedules a weekly cron job, and copies the menu bar app to `~/Applications/`.

**Requirements:** macOS, Python 3 (pre-installed on all Macs). [`rich`](https://github.com/Textualize/rich) is installed automatically for pretty output — the tool works without it too.

---

## CLI Usage

```bash
maccleaner                  # Show help & available commands
maccleaner preview          # See what can be cleaned + sizes
maccleaner clean            # Interactive cleanup (TUI checklist)
maccleaner clean --yes      # Auto-approve all safe items (cron mode)
maccleaner report           # Show last 10 cleanup runs
```

> Short aliases also work: `mclean`, `mpreview`, `mreport`.

Or call directly without installing:

```bash
python3 cleaner.py --preview
python3 cleaner.py --json    # Machine-readable output (consumed by menu bar app)
```

---

## What It Cleans

| Category | Items |
|----------|-------|
| **Xcode** | DerivedData, Previews, iOS/watchOS DeviceSupport, Archives |
| **Docker** | Unused images, volumes, containers (idle 7+ days) |
| **Node** | npm cache, pnpm store, yarn cache |
| **Python** | pip cache |
| **Caches** | Claude, Cursor, Chrome, Spotify, app caches |
| **Logs** | Log files over 100 MB |

### Safe vs. Review Items

- **Safe** — Always fine to delete (caches, derived data, build artifacts). Auto-clean touches these.
- **Review** — Check before deleting (archives, general caches). Always prompts, even with `--yes`.

---

## Menu Bar App

After running `install.sh`, the menu bar app is at `~/Applications/MacCleaner.app`.

```bash
open ~/Applications/MacCleaner.app
```

The 🧹 icon appears in your menu bar showing total reclaimable space. Click to preview, clean interactively, or auto-clean safe items.

> **First launch:** macOS may show a security warning for unsigned apps. Right-click → Open → Open to allow it. See [Phase 3 of the roadmap](ROADMAP.md) for notarization plans.

---

## Configuration

`~/mac-cleaner/config.json`:

```json
{
  "enabled_categories": ["xcode", "docker", "node", "python", "caches", "logs"],
  "skip_paths": [],
  "log_threshold_mb": 100,
  "auto_approve": false
}
```

- Remove entries from `enabled_categories` to skip them entirely
- Add paths to `skip_paths` to never touch them
- Set `auto_approve: true` to skip all confirmations (cron mode)

Or manage categories from the CLI without editing the file:

```bash
maccleaner --config-enable homebrew
maccleaner --config-disable docker
maccleaner --config-show
```

---

## Scheduling

```bash
~/mac-cleaner/scheduler.sh weekly    # Every Monday 9am
~/mac-cleaner/scheduler.sh monthly   # 1st of month
~/mac-cleaner/scheduler.sh remove    # Remove schedule
~/mac-cleaner/scheduler.sh status    # Check current schedule
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features — more cleanup targets (Homebrew, Go, Cargo, Ruby), smarter menu bar app, code signing, and a full native Swift rewrite.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The easiest contribution is adding a new cleanup target — it's a small dictionary entry in `cleaner.py`. Phase 1 of the roadmap lists exactly what we're looking for.

---

## License

[MIT](LICENSE) — Jordan Fuller, 2026.
