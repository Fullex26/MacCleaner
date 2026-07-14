# AGENTS.md — MacCleaner machine interface

MacCleaner is a macOS developer storage cleanup tool: it scans ~60 known cache/artifact locations (Xcode, Docker, npm, pip, Homebrew, AI model caches, ...) plus stale per-project build artifacts, reports sizes, and deletes what you select. The engine is a single stdlib-only Python 3 script. Entry point: `python3 cleaner.py` from a repo checkout, or `maccleaner` (shell alias) / `python3 ~/mac-cleaner/cleaner.py` after `install.sh`. Every data command takes `--json`; that JSON interface is the contract this document specifies (the bundled macOS app is just another client of it). Current version: 2.0.0.

## 1. Quick recipes

```bash
# What's reclaimable right now? (read-only, no side effects)
maccleaner scan --json

# Clean everything marked safe, no prompts
maccleaner clean --yes --json

# Clean two specific targets by ID (IDs come from scan --json)
maccleaner clean --targets npm-cache,xcode-derived-data --yes --json

# Only big things in one category
maccleaner clean --category xcode --min-size 500 --yes --json

# Find stale project build artifacts (node_modules, .venv, target, ...) — read-only
maccleaner projects --json

# Delete specific stale artifacts (IDs from projects --json)
maccleaner projects --clean --targets project-documents-foo-node-modules --yes --json

# Recoverable clean: move to ~/.Trash instead of deleting
maccleaner clean --yes --trash --json

# Environment health check
maccleaner doctor --json

# Enumerate every target ID, including disabled categories
maccleaner categories --json
```

Rule of thumb for agents: **always pass `--yes` together with `--json` on `clean`**. Without `--yes`, clean falls into a per-item y/N prompt loop that reads stdin (prompts print to stdout, corrupting the JSON stream); with stdin closed, every item is silently skipped.

## 2. Command reference

```
maccleaner                       # no args: welcome screen (human)
maccleaner scan      [--category C]... [--min-size MB] [--all] [--json]
maccleaner clean     [--yes] [--targets ID,ID] [--category C]... [--min-size MB] [--trash] [--json]
maccleaner projects  [--roots DIR]... [--min-age-days N] [--clean] [--yes] [--targets ID,ID] [--trash] [--json]
maccleaner report    [-n N | --limit N] [--json]        # default last 10 runs
maccleaner doctor    [--json]
maccleaner config    show | path | enable CAT | disable CAT | set KEY VALUE
maccleaner categories [--json]
maccleaner install-deps          # pip-installs 'rich' (optional, cosmetic only)
maccleaner --version
```

Flag details:

- `--category` is repeatable and accepts comma-separated values (`--category xcode,node`). Unknown category → error on stderr, exit 1.
- `--min-size MB` filters targets below the threshold (forces a measure pass first).
- `scan --all` also shows empty/not-installed targets in human output; JSON always includes all enabled targets (check `exists`/`size_bytes`).
- `clean --targets` with an unknown ID → error listing the unknown IDs on stderr, exit 1, nothing deleted.
- `--trash` moves paths to `~/.Trash/<name>` (timestamped suffix on collision) instead of deleting. Config `delete_mode: "trash"` makes it the default; `--trash` overrides per-run.
- `projects --clean` requires either interactivity or `--yes`. Note: **all** project artifacts are review-level, but `projects --clean --yes` deletes *everything found* (the `--yes` counts as explicit consent for this command). Scope with `--targets` first.
- `config set` parses VALUE as JSON when possible: `config set project_min_age_days 60`, `config set project_roots '["~/Code"]'`, `config set delete_mode '"trash"'`.
- `config show` always prints JSON (no `--json` flag needed). `config path` prints the config file path.

Legacy v1 spellings still work via a pre-parse shim: `--preview`, `--clean [--yes]`, `--report`, bare `--json` (= `scan --json`), `--category`, `--config-show`, `--config-enable C`, `--config-disable C`, `--install-deps`, plus subcommand aliases `preview` → `scan` and `history` → `report`. Existing cron jobs, aliases, and the v1 menu bar app keep working unchanged.

## 3. JSON output schemas

Abbreviated but field-accurate examples. All JSON is pretty-printed to stdout.

### `scan --json`

```json
{
  "version": "2.0.0",
  "timestamp": "2026-07-14T09:12:03.481920",
  "disk": "Used: 380Gi / 460Gi (85%)",
  "disk_stats": {
    "total_bytes": 494384795648,
    "free_bytes": 74158219264,
    "used_bytes": 420226576384,
    "percent_used": 85.0
  },
  "total_reclaimable_bytes": 23622320128,
  "total_reclaimable_human": "22.0 GB",
  "targets": [
    {
      "id": "xcode-derived-data",
      "category": "xcode",
      "label": "Xcode DerivedData",
      "description": "Intermediate build products; Xcode rebuilds them on demand",
      "size_bytes": 14495514624,
      "size_human": "13.5 GB",
      "safe": true,
      "exists": true
    },
    {
      "id": "docker-prune",
      "category": "docker",
      "label": "Docker unused data",
      "description": "Unused containers/images/networks older than a week (docker system prune)",
      "size_bytes": 2147483648,
      "size_human": "2.0 GB",
      "safe": true,
      "exists": true
    }
  ]
}
```

Targets are sorted by `size_bytes` descending. Command-based targets (docker/brew/pnpm/gem/simctl) report an *estimate* in `size_bytes` (0 when the tool is absent or estimation fails) and always have `exists: true`. Path-based targets that don't exist have `exists: false, size_bytes: 0`.

### `clean --json` (also `projects --clean --json`)

```json
{
  "version": "2.0.0",
  "timestamp": "2026-07-14T09:14:55.102331",
  "delete_mode": "rm",
  "freed_bytes": 14495514624,
  "freed_human": "13.5 GB",
  "disk_after": "Used: 366Gi / 460Gi (82%)",
  "items": [
    { "id": "xcode-derived-data", "label": "Xcode DerivedData", "freed": 14495514624, "status": "deleted" },
    { "id": "docker-prune",       "label": "Docker unused data", "freed": 0, "status": "deleted" },
    { "id": "xcode-archives",     "label": "Xcode Archives",     "freed": 0, "status": "skipped" }
  ]
}
```

`status` is one of `deleted`, `trashed` (trash mode), `skipped`, `error` (with an added `"error": "message"` field). A failed item never aborts the run. Command-based targets report `freed: 0` (their reclaim isn't measurable), so `freed_bytes` undercounts when they ran. `delete_mode` is `"rm"` or `"trash"`.

### `projects --json` (read-only scan)

```json
{
  "version": "2.0.0",
  "timestamp": "2026-07-14T09:16:12.000000",
  "roots": ["/Users/you/Documents", "/Users/you/Code"],
  "min_age_days": 30,
  "total_bytes": 5368709120,
  "artifacts": [
    {
      "path": "/Users/you/Documents/old-app/node_modules",
      "kind": "node_modules",
      "project": "/Users/you/Documents/old-app",
      "age_days": 142,
      "size_bytes": 3221225472,
      "id": "project-documents-old-app-node-modules"
    }
  ]
}
```

Sorted by `size_bytes` descending. The `id` is what `projects --clean --targets` accepts.

### `doctor --json`

```json
{
  "version": "2.0.0",
  "ok": true,
  "checks": [
    { "name": "Python",       "status": "3.12.4", "ok": true },
    { "name": "Config",       "status": "valid — /Users/you/mac-cleaner/config.json", "ok": true },
    { "name": "tool: docker", "status": "not found (its targets will be skipped)", "ok": true },
    { "name": "Disk",         "status": "69.1 GB free of 460.4 GB (85.0% used)", "ok": true }
  ]
}
```

`ok` is false only for genuine problems (currently: invalid config JSON). Missing optional tools are informational (`ok: true`).

### `categories --json`

```json
{
  "version": "2.0.0",
  "categories": [
    {
      "name": "xcode",
      "description": "Xcode build products, device support, simulators, SwiftPM/Carthage",
      "enabled": true,
      "targets": [
        { "id": "xcode-derived-data", "label": "Xcode DerivedData", "safe": true },
        { "id": "xcode-archives",     "label": "Xcode Archives",    "safe": false }
      ]
    }
  ]
}
```

Lists **all** categories and targets regardless of the enabled_categories config — use this to enumerate the full ID space.

### `report --json`

```json
{
  "version": "2.0.0",
  "runs": [
    {
      "timestamp": "2026-07-13T09:00:04.120394",
      "total_freed_bytes": 8589934592,
      "total_freed_human": "8.0 GB",
      "disk_after": "Used: 366Gi / 460Gi (82%)",
      "items": [
        { "id": "npm-cache", "label": "npm cache", "freed": 1073741824, "status": "deleted" }
      ]
    }
  ]
}
```

Oldest → newest, last N runs (`-n`, default 10). The log file keeps the last 50 runs. With no history: `{"version": "2.0.0", "runs": []}`.

## 4. Target IDs

- Every target has a **stable kebab-case ID** (e.g. `xcode-derived-data`, `npm-cache`, `huggingface-hub`, `brew-cleanup`). IDs are the agent-facing selector for `clean --targets` and will not be renamed within a major version; new targets only add IDs.
- Two ID families are **dynamic** (derived from what's on disk, still deterministic per machine): `log-<folder-slug>` for oversized folders under `~/Library/Logs`, and `project-<home-relative-path-slug>` for stale project artifacts. Enumerate them fresh via `scan --json` / `projects --json` before cleaning; don't hardcode them.
- Enumerate the full static ID space with `categories --json` (all 17 categories: `xcode docker node python caches logs homebrew go rust ruby cocoapods gradle maven ai ide browsers system`). `scan --json` shows only targets in enabled categories, with live sizes.
- **Safe vs. review** (`"safe": true/false`):
  - `clean --yes` (no `--targets`) cleans safe targets only; review targets appear in results as `"status": "skipped"`.
  - `clean --targets ID --yes` cleans exactly the named targets, **including review ones** — naming an ID explicitly counts as consent. This is the intended way for an agent to clean a review target after getting user confirmation.
  - Review targets are things with real re-acquisition cost or data risk: Xcode Archives (dSYMs), simulator runtimes, AI model caches (`huggingface-hub`, `torch-hub`, `ollama-models`), Playwright/Puppeteer browsers, Maven repo, pyenv shims, iOS device backups, Trash, the broad `general-caches`.
  - Config `auto_approve: true` makes every `clean` behave as `--yes`.
- Cleaning respects `enabled_categories` and `skip_paths` from config; a `--targets` ID in a disabled category is reported as unknown (exit 1).

## 5. Exit codes, streams, environment

**Exit codes**: `0` success (including "nothing to clean"), `1` runtime error (unknown target ID, unknown category, invalid config key), `2` usage error (bad flags — argparse), `130` interrupted (SIGINT).

**Streams**: in `--json` mode, the JSON document is the only thing on stdout; all human-facing progress/messages go to stderr. Parse stdout, log stderr. (Without `--json`, everything is human-formatted on stdout.)

**Environment variables** (engine):

| Variable | Effect |
|---|---|
| `MACCLEANER_CONFIG` | Path to config JSON (default: `config.json` next to `cleaner.py`) |
| `MACCLEANER_LOG` | Path to the run-history log (default: `report.log` next to `cleaner.py`) |

`MACCLEANER_ENGINE` is read by the macOS app only (points it at a development `cleaner.py`); the engine itself ignores it.

**Config keys** (`config show` / `config set`): `enabled_categories` (list), `skip_paths` (list of path prefixes to never touch), `log_threshold_mb` (default 100), `auto_approve` (default false), `schedule`, `delete_mode` (`"rm"` | `"trash"`), `project_roots` (default `~/Documents ~/Developer ~/Projects ~/Code ~/dev`), `project_min_age_days` (default 30). Missing keys are merged from defaults at load time. Installed config lives at `~/mac-cleaner/config.json`; CLI and macOS app share it.

## 6. Safety guarantees (blast radius)

- **Home-only**: the deleter refuses any path not strictly inside `$HOME`, and refuses `$HOME` itself and `/`. Out-of-home paths surface as per-item errors, never deletions.
- **Symlinks are never followed**: a symlink is unlinked (the link itself), never traversed into its destination. The projects scanner also never follows symlinks while walking.
- **Empty-only targets**: `general-caches` (`~/Library/Caches`) and `trash` (`~/.Trash`) delete *contents* only — the directory itself is preserved.
- **Trash mode**: `--trash` (or `delete_mode: "trash"`) moves paths to `~/.Trash` instead of deleting — fully recoverable until the Trash is emptied. Exception: the `trash` target always hard-deletes (moving Trash into Trash would be a no-op).
- **Projects scanner is conservative**: bounded depth (5), only known artifact dir names, most require a sibling manifest proving project type (`node_modules` needs `package.json`, `target` needs `Cargo.toml`, `.venv` needs `pyproject.toml`/`requirements.txt`/..., etc.), minimum age gate (default 30 days by dir mtime), never descends into `.git`, hidden dirs, or the artifacts themselves. All hits are review-level.
- **Command-based targets** run fixed, non-destructive-by-design tool commands: `docker system prune -f --filter 'until=168h'`, `brew cleanup --prune=all`, `brew autoremove`, `pnpm store prune`, `gem cleanup`, `xcrun simctl delete unavailable`. No user input is interpolated into them.
- `scan`, `projects` (without `--clean`), `doctor`, `report`, `categories`, and `config show|path` are strictly read-only. Only `clean` and `projects --clean` delete anything; both append a run entry to the report log.
