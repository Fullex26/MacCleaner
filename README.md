# 🧹 MacCleaner

macOS developer storage cleanup tool. CLI + menu bar app.

## Install

```bash
bash install.sh
```

## CLI Usage

```bash
mpreview          # See what will be deleted + sizes
mclean            # Interactive cleanup (confirm each item)
mclean --yes      # Auto-approve all safe items (for cron)
mreport           # Show cleanup history
```

## What It Cleans

| Category | Items |
|----------|-------|
| **Xcode** | DerivedData, Previews, iOS/watchOS DeviceSupport, Archives |
| **Docker** | Unused images, volumes, containers (unused 7+ days) |
| **Node** | npm cache, pnpm store, yarn cache |
| **Python** | pip cache |
| **Caches** | Claude, Cursor, Chrome, Spotify, app caches |
| **Logs** | Log files over 100MB |

## Config (`config.json`)

```json
{
  "enabled_categories": ["xcode", "docker", "node", "python", "caches", "logs"],
  "skip_paths": ["/path/to/keep"],
  "log_threshold_mb": 100,
  "auto_approve": false,
  "schedule": "weekly"
}
```

- Remove categories from `enabled_categories` to skip them entirely
- Add paths to `skip_paths` to never touch them
- Set `auto_approve: true` to skip confirmations (cron mode)

## Schedule

```bash
~/mac-cleaner/scheduler.sh weekly    # Every Monday 9am
~/mac-cleaner/scheduler.sh monthly   # 1st of month
~/mac-cleaner/scheduler.sh remove    # Remove schedule
~/mac-cleaner/scheduler.sh status    # Check current schedule
```

## Menu Bar App

1. Open `MacCleanerApp/` in Xcode
2. Build & Run (⌘R)
3. 🧹 appears in menu bar showing reclaimable space
4. Click to preview, clean, or auto-clean

## Safe vs Review Items

- ✅ **Safe** — Always fine to delete (caches, derived data, previews)
- ⚠️ **Review** — Check before deleting (archives, general caches)

Auto-clean (`--yes`) only touches safe items. Review items always prompt.
