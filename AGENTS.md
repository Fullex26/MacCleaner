# AGENTS.md — MacCleaner machine interface

MacCleaner is a macOS developer storage cleanup tool: it scans 70+ known cache/artifact locations across 20 categories (Xcode, Docker, npm, pip, Homebrew, AI model caches, Flutter, PHP, VMs, ...) plus stale per-project build artifacts, reports sizes, and deletes what you select. The engine is a single stdlib-only Python 3 script. Entry point: `python3 cleaner.py` from a repo checkout, or `maccleaner` (shell alias) / `python3 ~/mac-cleaner/cleaner.py` after `install.sh`. Every data command takes `--json`; that JSON interface is the contract this document specifies (the bundled macOS app is just another client of it). Current version: 2.1.0.

## 1. Quick recipes

```bash
# What's reclaimable right now? (no deletions; records a disk-usage snapshot — see §3)
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

# Preview exactly what a clean would delete — zero side effects, nothing written to disk
maccleaner clean --dry-run --json

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
maccleaner clean     [--yes] [--targets ID,ID] [--category C]... [--min-size MB] [--trash] [--dry-run] [--json]
maccleaner projects  [--roots DIR]... [--min-age-days N] [--clean] [--yes] [--targets ID,ID] [--trash] [--dry-run] [--json]
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
- `--dry-run` (on `clean` and `projects`) resolves the exact concrete paths (or, for cmd-based targets, the command that would run) and reports sizes without deleting anything, prompting, or writing to `report.log`/`snapshots.log`. Output is clean-shaped JSON plus `"dry_run": true`; the dry run itself always exits 0 — but argument validation (an unknown `--targets` ID, an unknown `--category`) runs first and still exits 1 before the dry run ever executes. On `projects`, `--dry-run` implies `--clean`'s target selection (including the git-aware filtering below) — you don't need to also pass `--clean`.
- `projects --clean` requires either interactivity or `--yes`. Note: **all** project artifacts are review-level, so `projects --clean --yes` (without `--targets`) deletes everything found *except* projects flagged dirty or unpushed in git — those are skipped and listed on stderr. Name a flagged artifact explicitly via `--targets` to clean it anyway (naming it counts as consent, same as `clean --targets`). Disable the git check entirely with `config set project_git_check false`.
- `config set` parses VALUE as JSON when possible: `config set project_min_age_days 60`, `config set project_roots '["~/Code"]'`, `config set delete_mode '"trash"'`.
- `config show` always prints JSON (no `--json` flag needed). `config path` prints the config file path.

Legacy v1 spellings still work via a pre-parse shim: `--preview`, `--clean [--yes]`, `--report`, bare `--json` (= `scan --json`), `--category`, `--config-show`, `--config-enable C`, `--config-disable C`, `--install-deps`, plus subcommand aliases `preview` → `scan` and `history` → `report`. Existing cron jobs, aliases, and the v1 menu bar app keep working unchanged.

## 3. JSON output schemas

Abbreviated but field-accurate examples. All JSON is pretty-printed to stdout.

### `scan --json`

```json
{
  "version": "2.1.0",
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

Targets are sorted by `size_bytes` descending. Command-based targets (docker/brew/pnpm/gem/conda/simctl) report an *estimate* in `size_bytes` (0 when the tool is absent or estimation fails) and always have `exists: true`. Path-based targets that don't exist have `exists: false, size_bytes: 0`.

### `clean --json` (also `projects --clean --json`)

```json
{
  "version": "2.1.0",
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

### `clean --dry-run --json` (also `projects --clean --dry-run --json` / `projects --dry-run --json`)

```json
{
  "version": "2.1.0",
  "timestamp": "2026-07-14T09:14:55.102331",
  "dry_run": true,
  "delete_mode": "rm",
  "freed_bytes": 14495514624,
  "freed_human": "13.5 GB",
  "disk_after": "Used: 380Gi / 460Gi (85%)",
  "items": [
    {
      "id": "xcode-derived-data",
      "label": "Xcode DerivedData",
      "freed": 14495514624,
      "status": "would-delete",
      "paths": [
        { "path": "/Users/you/Library/Developer/Xcode/DerivedData/App-abc123", "size_bytes": 14495514624 }
      ]
    },
    {
      "id": "docker-prune",
      "label": "Docker unused data",
      "freed": 2147483648,
      "status": "would-run",
      "cmd": "docker system prune -f --filter 'until=168h' 2>/dev/null || true",
      "paths": []
    }
  ]
}
```

Same envelope shape as `clean --json` (`delete_mode`, `freed_bytes`, `disk_after`, `items`), plus `"dry_run": true`. `status` is `would-delete` (path-based targets) or `would-run` (cmd-based targets) — never `deleted`/`trashed`/`error`, since nothing runs. `paths` is the concrete, already-resolved list of `{path, size_bytes}` this run would touch (glob patterns expanded, `empty_only` targets expanded to their children); cmd-based targets always have an empty `paths` array. `disk_after` reflects the *current* disk state, since nothing was freed. `--dry-run` never deletes anything, never prompts, and never writes to `report.log` or `snapshots.log`; the dry run itself always exits `0` — though argument validation (an unknown `--targets` ID or `--category`) runs first and still exits `1` before reaching it. On `projects --clean --dry-run` / `projects --dry-run`, the item set is filtered the same way `--clean --yes` would filter it (git-flagged projects excluded unless named via `--targets`), with the same stderr skip note.

### `projects --json` (read-only scan)

```json
{
  "version": "2.1.0",
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
      "git": { "dirty": false, "unpushed": true },
      "id": "project-documents-old-app-node-modules"
    }
  ]
}
```

Sorted by `size_bytes` descending. The `id` is what `projects --clean --targets` accepts. `git` is `null` when git status couldn't be determined (not a repo, `git` missing, or any git failure — including a 2-second timeout); otherwise `{"dirty": bool, "unpushed": bool}`. `dirty` means `git status --porcelain` reported changes; `unpushed` means there are commits on local branches that no remote has (a repo with no remotes at all counts as unpushed). Controlled by config `project_git_check` (default `true`); when disabled, `git` is always `null` and no git subprocess is run.

### `doctor --json`

```json
{
  "version": "2.1.0",
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
  "version": "2.1.0",
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
  "version": "2.1.0",
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
  ],
  "disk_history": {
    "current": {
      "total_bytes": 494384795648,
      "free_bytes": 74158219264,
      "used_bytes": 420226576384,
      "percent_used": 85.0
    },
    "snapshots": [
      {
        "ts": "2026-07-13T09:00:04.331920",
        "disk_total_bytes": 494384795648,
        "disk_free_bytes": 79158219264,
        "reclaimable_bytes": 23622320128,
        "categories": { "xcode": 14495514624, "docker": 2147483648 }
      }
    ]
  }
}
```

Oldest → newest, last N runs (`-n`, default 10). The log file keeps the last 50 runs. With no history: `{"version": "2.1.0", "runs": [], "disk_history": {...}}` (`disk_history` is present either way).

`disk_history` is **additive** (new in 2.1.0) and always present, even with no cleanup history. `current` is today's `disk_stats()` snapshot. `snapshots` is the full contents of `snapshots.log` (append-only, capped at the most recent 365 entries; a snapshot recorded on the same calendar day as the previous one replaces it instead of adding a new entry — so a machine scanned any number of times a day, including every few minutes by the menu bar app's auto-refresh, accumulates at most one entry per day, giving 365 entries roughly a year of history). Every `scan` and every real `clean`/`projects --clean` run (not `--dry-run`) records one snapshot. `reclaimable_bytes` and `categories` (a map of category → bytes) are `null` unless the run covers the **full, unscoped target list** — a plain `scan`, or a plain `clean` with no `--category`/`--min-size`/`--targets` (this doesn't depend on `--yes`: an unscoped interactive `clean` counts too). The sums span every measured target regardless of `safe` — review targets (e.g. all of `ai`, and `system`'s `trash`/`ios-backups`) are folded into `categories` too, not excluded; for `clean` the sum is taken after the run, over whatever remains uncleaned (targets not deleted/trashed this pass, including any review target skipped or declined). They're `null` for `scan --category …`, `scan --min-size …`, any `clean` scoped by `--targets`/`--category`/`--min-size`, and *every* `projects --clean` run (project artifacts aren't part of the regular category sweep, and it never records full scope) — in all of those cases only `disk_total_bytes`/`disk_free_bytes` are trustworthy. Use `MACCLEANER_SNAPSHOTS` to point the engine at a different snapshots file (see §5).

## 4. Target IDs

- Every target has a **stable kebab-case ID** (e.g. `xcode-derived-data`, `npm-cache`, `huggingface-hub`, `brew-cleanup`). IDs are the agent-facing selector for `clean --targets` and will not be renamed within a major version; new targets only add IDs.
- Two ID families are **dynamic** (derived from what's on disk, still deterministic per machine): `log-<folder-slug>` for oversized folders under `~/Library/Logs`, and `project-<home-relative-path-slug>` for stale project artifacts. Enumerate them fresh via `scan --json` / `projects --json` before cleaning; don't hardcode them.
- Enumerate the full static ID space with `categories --json` (all 20 categories: `xcode docker node python caches logs homebrew go rust ruby cocoapods gradle maven ai ide browsers system flutter php vms`). `scan --json` shows only targets in enabled categories, with live sizes.
- **New in 2.1.0** — 17 targets across 3 new categories: `flutter` (`dart-pub-cache`), `php` (`composer-cache`), `vms` (`colima-vm`, `vagrant-boxes`, `minikube-cache`); plus, in existing categories, `xcode-doc-cache`, `yarn-global-cache`, `npm-logs`, `conda-clean`, `sccache-cache`, `lm-studio-models`, `whisper-models`, `cypress-cache`, `teams-cache`, `zoom-updater`, `terraform-plugin-cache`, `expo-cache`. All additive — no existing IDs changed.
- **Safe vs. review** (`"safe": true/false`):
  - `clean --yes` (no `--targets`) cleans safe targets only; review targets appear in results as `"status": "skipped"`.
  - `clean --targets ID --yes` cleans exactly the named targets, **including review ones** — naming an ID explicitly counts as consent. This is the intended way for an agent to clean a review target after getting user confirmation.
  - Review targets are things with real re-acquisition cost or data risk: Xcode Archives (dSYMs), simulator runtimes, AI model caches (`huggingface-hub`, `torch-hub`, `ollama-models`, `lm-studio-models`, `whisper-models`), Playwright/Puppeteer/Cypress binaries, Maven repo, pyenv shims, VM disks (`colima-vm`, `vagrant-boxes`), iOS device backups, Trash, the broad `general-caches`.
  - Config `auto_approve: true` makes every `clean` behave as `--yes`.
- Cleaning respects `enabled_categories` and `skip_paths` from config; a `--targets` ID in a disabled category is reported as unknown (exit 1).

## 5. Exit codes, streams, environment

**Exit codes**: `0` success (including "nothing to clean"), `1` runtime error (unknown target ID, unknown category, invalid config key), `2` usage error (bad flags — argparse), `130` interrupted (SIGINT).

**Streams**: in `--json` mode, the JSON document is the only thing on stdout; all human-facing progress/messages go to stderr. Parse stdout, log stderr. (Without `--json`, everything is human-formatted on stdout.)

**Environment variables** (engine):

| Variable | Effect |
|---|---|
| `MACCLEANER_CONFIG` | Path to config JSON (default: `config.json` next to `cleaner.py`) |
| `MACCLEANER_LOG` | Path to the run-history log (default: `report.log` next to `cleaner.py`, falling back to `~/Library/Application Support/MacCleaner/report.log` when that directory isn't writable — e.g. running from a signed `.app` bundle's `Contents/Resources/cleaner.py`) |
| `MACCLEANER_SNAPSHOTS` | Path to the disk-snapshots log (default: `snapshots.log` next to `cleaner.py`, with the same Application Support fallback as `MACCLEANER_LOG`) — new in 2.1.0 |

`MACCLEANER_ENGINE` is read by the macOS app only (points it at a development `cleaner.py`); the engine itself ignores it.

**Config keys** (`config show` / `config set`): `enabled_categories` (list), `skip_paths` (list of path prefixes to never touch), `log_threshold_mb` (default 100), `auto_approve` (default false), `schedule`, `delete_mode` (`"rm"` | `"trash"`), `project_roots` (default `~/Documents ~/Developer ~/Projects ~/Code ~/dev`), `project_min_age_days` (default 30), `project_git_check` (default `true` — new in 2.1.0; when `true`, `projects` shells out to `git` per project to populate the `git` field and `projects --clean --yes` skips dirty/unpushed projects; set to `false` to skip the git checks entirely). Missing keys are merged from defaults at load time. Installed config lives at `~/mac-cleaner/config.json`; CLI and macOS app share it.

## 6. Safety guarantees (blast radius)

- **Home-only**: the deleter refuses any path not strictly inside `$HOME`, and refuses `$HOME` itself and `/`. Out-of-home paths surface as per-item errors, never deletions.
- **Symlinks are never followed**: a symlink is unlinked (the link itself), never traversed into its destination. The projects scanner also never follows symlinks while walking.
- **Empty-only targets**: `general-caches` (`~/Library/Caches`) and `trash` (`~/.Trash`) delete *contents* only — the directory itself is preserved.
- **Trash mode**: `--trash` (or `delete_mode: "trash"`) moves paths to `~/.Trash` instead of deleting — fully recoverable until the Trash is emptied. Exception: the `trash` target always hard-deletes (moving Trash into Trash would be a no-op).
- **Projects scanner is conservative**: bounded depth (5), only known artifact dir names, most require a sibling manifest proving project type (`node_modules` needs `package.json`, `target` needs `Cargo.toml`, `.venv` needs `pyproject.toml`/`requirements.txt`/..., etc.), minimum age gate (default 30 days by dir mtime), never descends into `.git`, hidden dirs, or the artifacts themselves. All hits are review-level.
- **Command-based targets** run fixed, non-destructive-by-design tool commands: `docker system prune -f --filter 'until=168h'`, `brew cleanup --prune=all`, `brew autoremove`, `pnpm store prune`, `gem cleanup`, `conda clean --all --yes`, `xcrun simctl delete unavailable`. No user input is interpolated into them.
- **Git-aware projects** (new in 2.1.0): when `project_git_check` is enabled (default), every project artifact's parent directory is checked with `git status --porcelain` (dirty) and `git rev-list --count --branches --not --remotes` (unpushed — a repo with no remotes at all counts as unpushed). These git invocations pass `--no-optional-locks` (so a concurrent `git add`/`git commit` by the user is never blocked by the scan taking `.git/index.lock`) and `-c core.fsmonitor=` (so a repo-local fsmonitor hook can't execute code during a read-only scan). Any git failure — not a repo, `git` not installed, a 2-second timeout — degrades to `"git": null` rather than blocking the scan. `projects --clean --yes` and `projects --dry-run` / `projects --clean --dry-run`, without `--targets`, both skip dirty/unpushed projects and both list them on stderr; naming one via `--targets` cleans (or previews) it anyway.
- **`--dry-run`** (new in 2.1.0, on `clean` and `projects`): resolves and reports the exact concrete paths/sizes (or command) a real run would act on, deletes nothing, prompts for nothing, and writes no `report.log` or `snapshots.log` entry.
- `scan`, `projects` (without `--clean`), `doctor`, `report`, `categories`, and `config show|path` never delete anything. `scan` and every real `clean`/`projects --clean` run (not `--dry-run`) also record a disk-usage entry to `snapshots.log`; `clean` and `projects --clean` (but not `--dry-run`) additionally append a run entry to `report.log`. `--dry-run` writes to neither log.
