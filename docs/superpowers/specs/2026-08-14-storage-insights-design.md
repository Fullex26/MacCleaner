# MacCleaner v2.9 — Storage Insights (read-only large-files view)

**Date:** 2026-08-14
**Status:** Approved (design validated in-session with Jordan)
**Prior art:** `docs/superpowers/specs/2026-08-12-v2.7-app-uninstaller-design.md` (originally deferred this
sub-project until App Uninstaller shipped); `CLAUDE.md`/`AGENTS.md` (engine conventions);
`[[icloud-git-eviction]]`-class prior incident (memory) — this repo has already been bitten once by
an operation that force-materialized iCloud-evicted files, which is why eviction-safety is a first-class
constraint here, not an afterthought.

## Motivation

MacCleaner has always cleaned regenerable developer/cache junk. The original "how can we scan for
even more" brainstorm (2026-08-12) split into two sub-projects because Applications/Documents data is
a fundamentally different risk category from caches: irreplaceable, not regenerable. App Uninstaller
(v2.7) was the piece that fit the existing auto-delete model (orphaned leftovers of apps you've already
removed). This spec is the second piece, deliberately built to a different standard: it never deletes
anything. It surfaces large files under Documents/Downloads/Desktop so *you* can decide, in Finder,
what (if anything) to do about them.

## Non-goals (explicit)

- **No deletion, ever, of anything this feature finds.** Not `safe`, not `review`, not a target, not a
  category — this scan never enters `get_targets()`/`collect_targets()`/`delete_target()` at all. It is
  architecturally outside the delete pipeline, not merely defaulted to unsafe within it.
- **No duplicate detection in v1.** Real duplicate detection needs content hashing, which means reading
  full file bytes — expensive for large media, and it would force-download any iCloud-evicted file it
  touches. Deferred to a future v1.1 that can give eviction-safety its own explicit design (e.g. skip
  hashing any file whose `NSURLUbiquitousItemDownloadingStatusKey`/equivalent shows it's not fully
  downloaded, rather than silently materializing it).
- **No user-configurable scan roots in v1.** Fixed to `~/Documents`, `~/Downloads`, `~/Desktop`. A config
  key can follow later if the fixed roots prove too narrow in practice.
- **No content reads of any kind.** The scan is `stat()`-only from end to end — this is what makes
  eviction-safety free rather than something to carefully bolt on.

## 1. Engine — `scan_storage_insights(config)` + `storage-insights` subcommand

A new, standalone scan function, structurally simple (no target dicts, no category, no `safe` flag —
this is a reporting function, not a cleanup scanner):

```python
def scan_storage_insights(config):
    """Read-only scan of ~/Documents, ~/Downloads, ~/Desktop for files at or
    above STORAGE_INSIGHTS_MIN_BYTES. stat()-only -- never opens or reads
    file contents, so it can never trigger an iCloud download of an evicted
    file. Skips dev-artifact directories (the same ARTIFACT_MANIFESTS /
    TMP_CLONE_ARTIFACTS names scan_projects already treats as noise, so the
    two features never disagree about what's "developer clutter" vs. "your
    file") and never descends into a .app bundle (treated as one opaque
    unit, matching Finder). Returns up to STORAGE_INSIGHTS_MAX_RESULTS
    entries, largest first."""
```

- **Roots**: `HOME / "Documents"`, `HOME / "Downloads"`, `HOME / "Desktop"` by default — not a
  user-facing config key (matching the approved "fixed roots" scope), but overridable via
  **`MACCLEANER_STORAGE_INSIGHTS_ROOTS`** (colon-separated, PATH-style — the same shape as the existing
  `MACCLEANER_INSTALLED_APPS_DIRS`), which is the sandboxing seam tests use so they never touch the
  real `~/Documents`/`~/Downloads`/`~/Desktop`. A root that doesn't exist is skipped, not an error (a
  user with no `~/Desktop` folder, e.g. someone who moved to Stage Manager only, must not crash the
  scan) — this applies identically whether a root came from the default or the override.
- **Walk**: `os.walk` with directory pruning — before descending into a directory, skip it if its name
  is in `ARTIFACT_MANIFESTS`'s keys or `TMP_CLONE_ARTIFACTS` (reusing the existing module-level
  constants at `cleaner.py:1309`/`1337` verbatim — no new list to keep in sync), or if it ends in
  `.app` (the whole bundle is skipped, not just excluded from results — this also means an oversized
  binary inside a `.app` never gets stat'd at all, which is both correct and cheaper). Symlinked
  directories are not followed (`os.walk(..., followlinks=False)`, the default) — this is a read-only
  reporting scan, not a delete path, but not following symlinks keeps the walk bounded to the real
  tree and avoids double-counting or wandering outside the three roots via a stray symlink.
- **File filter**: every non-directory entry (via `os.walk`'s `filenames`, plus a `followlinks=False`
  guard so a symlinked *file* is stat'd via `lstat`-equivalent semantics, never dereferenced) whose
  size is `>= STORAGE_INSIGHTS_MIN_BYTES` (constant, **100 MB** — `100 * 1024 * 1024`).
- **Never opens file contents.** Size and mtime both come from a single `os.stat()` (or
  `DirEntry.stat()` during the walk, cheaper — no second syscall) per candidate. This is the entire
  eviction-safety story: `stat()` reads filesystem metadata, which macOS answers for an evicted iCloud
  placeholder without materializing the file.
- **Sort + cap**: sort by size descending, truncate to `STORAGE_INSIGHTS_MAX_RESULTS` (constant, **50**).
- **Return shape**: a list of dicts, `{"path": Path, "size_bytes": int, "mtime": float}` — no `id`, no
  `safe`, no `category`. This is deliberately NOT target-shaped; nothing downstream should be able to
  mistake a storage-insights entry for something `get_targets()`-family code could act on.

`main()` gains a `storage-insights` subcommand:
- `python3 cleaner.py storage-insights` — human-readable table (path, human size, relative last-modified),
  same `RICH`/plain-fallback pattern every other command already uses.
- `python3 cleaner.py storage-insights --json` — `{"version": VERSION, "entries": [{"path": str,
  "size_bytes": int, "size_human": str, "mtime": float}, ...]}`. JSON on stdout, nothing else — same
  contract as every other data command.
- No `--yes`, no `--targets`, no `--dry-run` flag on this subcommand — there is nothing to confirm or
  preview, because there is nothing this command can do except report.

## 2. Contract

Purely additive: a new subcommand, new JSON shape under its own top-level key. Nothing about
`scan`/`clean`/`categories`/`doctor`'s existing contracts changes. `AGENTS.md` gains a new section
documenting `storage-insights --json`'s shape, explicitly stating (for any agent reading the contract)
that this command's output is informational only and has no corresponding delete/target mechanism.

## 3. App — new Dashboard section

A new section in the existing Dashboard (alongside the category list), titled something like "Large
Files", calling `storage-insights --json` through the same `CleanerBridge` pattern every other
Dashboard section already uses. Each row: file name, human size, relative last-modified, and a
"Reveal in Finder" button calling `NSWorkspace.shared.selectFile(path, inFileViewerRootedAtPath: "")`
— this opens Finder with the item selected and highlighted. This is a launch action, not a file
operation: MacCleaner never touches the file itself, on any code path this feature introduces.

No new Codable model complexity: a flat `StorageInsightEntry` decoding the three JSON fields, no `safe`/
`category` fields to reason about (unlike every existing target-shaped model), which keeps this view
structurally incapable of accidentally feeding into any clean/select-all/bulk-action code that exists
for the target-shaped views.

## 4. Testing

- **Scanner tests** (mirrors the existing `scan_tmp_artifacts`/`scan_projects` test style): sandboxed
  via `MACCLEANER_STORAGE_INSIGHTS_ROOTS` pointed at scratch directories containing files above and
  below the size floor, confirming only the ≥100 MB ones surface;
  a dev-artifact directory (e.g. a fabricated `node_modules` with an oversized fake file inside) is
  correctly skipped; a fabricated `.app` bundle with an oversized binary inside `Contents/MacOS` is
  correctly skipped in its entirety; a symlinked directory pointing outside the sandboxed root is not
  followed; more than 50 qualifying files correctly truncates to 50, sorted largest-first; a missing
  root directory (e.g. no `Desktop` folder) does not raise.
- **Eviction-safety test**: an explicit test asserting the scanner never calls anything that opens file
  content — e.g. `mock.patch("builtins.open")` (or the specific read primitive used) raising if called
  during a scan over fixture files, proving by construction that the implementation can't regress into
  reading bytes.
- **CLI contract test**: `storage-insights --json` produces valid JSON with the documented shape;
  `storage-insights` (plain) doesn't crash without `rich` installed.
- **App**: no Swift unit tests exist elsewhere in this codebase for Dashboard sections (verified — the
  app is tested via manual/build verification, matching every prior UI-adding task in this project's
  history), so this follows the same pattern: build clean, visual spot-check of the new section and the
  Reveal-in-Finder action.
