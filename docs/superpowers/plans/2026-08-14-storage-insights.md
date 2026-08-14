# Storage Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only "largest files in Documents/Downloads/Desktop" view — a new `storage-insights` CLI subcommand plus a Dashboard section in the app — that never deletes anything and is architecturally outside the delete pipeline.

**Architecture:** A single new scanner function (`scan_storage_insights`) in `cleaner.py`, stat-only and iCloud-eviction-safe by construction, feeding a new subcommand. On the app side, one new Codable model plus one new Dashboard section that fetches via the existing `CleanerBridge.run(_:_:)` generic, with a "Reveal in Finder" action per row. No category, no target, no `safe` flag — this scan never touches `get_targets()`/`collect_targets()`/`delete_target()`.

**Tech Stack:** Python 3 stdlib (`os.scandir`, `time`), `unittest`; SwiftUI (`RelativeDateTimeFormatter`, `NSWorkspace`).

## Global Constraints

- Single-file, stdlib-only `cleaner.py`. No `X | None` type annotations (macOS ships Python 3.9) — this also means **do not use `Path.stat(follow_symlinks=...)`**, which is Python 3.10+; use `os.DirEntry.stat(follow_symlinks=False)` via `os.scandir`, matching the existing pattern in `installed_bundle_ids()`/`scan_app_leftovers()`.
- **Never opens or reads file contents.** Every size/mtime read is a single `stat()` call. This is the entire iCloud-eviction-safety story — do not introduce any code path that opens a file.
- **Never follows symlinks.** Both symlinked directories and symlinked files are skipped entirely (checked via `entry.is_symlink()` before any other classification) — simpler and safer than trying to report a symlinked file's own size.
- This scan is architecturally outside the delete pipeline: no `safe` field, no category, no target `id`, never passed to `get_targets()`, `collect_targets()`, or `delete_target()`. Nothing in this plan adds a `--yes`/`--dry-run`/`--targets` flag to the new subcommand, because there is nothing to confirm or preview.
- JSON contract is new and additive (new subcommand, own top-level shape) — does not touch any existing command's output.
- No new config key. Root override is env-var only: `MACCLEANER_STORAGE_INSIGHTS_ROOTS` (colon-separated, PATH-style, default `~/Documents:~/Downloads:~/Desktop`), mirroring `MACCLEANER_INSTALLED_APPS_DIRS`.
- Size floor: 100 MB (`STORAGE_INSIGHTS_MIN_BYTES = 100 * 1024 * 1024`). Result cap: 50 (`STORAGE_INSIGHTS_MAX_RESULTS = 50`).
- Commit after every task; run `python3 -m unittest discover -s tests` before each commit.
- Version bump only in the final task.

## Baseline (verified on this checkout at plan time)

- `python3 -m unittest discover -s tests` → **377 tests, OK**
- `bash completions/run_tests.sh` → **51/51**
- `python3 -c "import cleaner; print(cleaner.VERSION)"` → **2.8.1**
- Current subcommand count (`maccleaner ` completion, non-flag entries) → **10**

---

### Task 1: `scan_storage_insights()` scanner

**Files:**
- Modify: `cleaner.py` — new constants + `_storage_insights_roots()` + `scan_storage_insights(config)`, placed immediately after the `TMP_CLONE_ARTIFACTS` block (near line 1337, so it sits next to the artifact-dir constants it reuses)
- Test: `tests/test_cleaner.py` — new class `TestStorageInsightsScanner`

**Interfaces:**
- Consumes: `ARTIFACT_MANIFESTS` (dict, keys are dev-artifact dir names, `cleaner.py:1309`), `TMP_CLONE_ARTIFACTS` (set, `cleaner.py:1337`), `HOME` (`Path`, `cleaner.py:55`).
- Produces: `scan_storage_insights(config)` → `list[dict]`, each `{"path": Path, "size_bytes": int, "mtime": float}`, sorted largest-first, capped at `STORAGE_INSIGHTS_MAX_RESULTS`. `config` parameter is accepted but unused in this task (kept for signature symmetry with every other `scan_*(config)` function in the file, and because a future config key would need it without changing the call site).

- [ ] **Step 1: Write the failing tests**

```python
class TestStorageInsightsScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.docs = self.tmp / "Documents"
        self.downloads = self.tmp / "Downloads"
        self.desktop = self.tmp / "Desktop"
        for d in (self.docs, self.downloads, self.desktop):
            d.mkdir()
        self._patch = mock.patch.dict(os.environ, {
            "MACCLEANER_STORAGE_INSIGHTS_ROOTS":
                f"{self.docs}:{self.downloads}:{self.desktop}"
        })
        self._patch.start()
        self.cfg = {}

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_file(self, root, name, mb):
        p = root / name
        p.write_bytes(b"\0" * (mb * 1024 * 1024))
        return p

    def test_finds_file_above_floor(self):
        self._make_file(self.docs, "big.mov", 150)
        hits = cleaner.scan_storage_insights(self.cfg)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["path"], self.docs / "big.mov")
        self.assertEqual(hits[0]["size_bytes"], 150 * 1024 * 1024)

    def test_excludes_file_below_floor(self):
        self._make_file(self.docs, "small.pdf", 50)
        self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_scans_all_three_roots(self):
        self._make_file(self.docs, "a.mov", 120)
        self._make_file(self.downloads, "b.dmg", 130)
        self._make_file(self.desktop, "c.zip", 140)
        hits = cleaner.scan_storage_insights(self.cfg)
        self.assertEqual({h["path"].name for h in hits}, {"a.mov", "b.dmg", "c.zip"})

    def test_sorted_largest_first(self):
        self._make_file(self.docs, "small.mov", 110)
        self._make_file(self.docs, "big.mov", 500)
        hits = cleaner.scan_storage_insights(self.cfg)
        self.assertEqual([h["path"].name for h in hits], ["big.mov", "small.mov"])

    def test_caps_at_max_results(self):
        for i in range(cleaner.STORAGE_INSIGHTS_MAX_RESULTS + 5):
            self._make_file(self.docs, f"f{i}.bin", 101)
        hits = cleaner.scan_storage_insights(self.cfg)
        self.assertEqual(len(hits), cleaner.STORAGE_INSIGHTS_MAX_RESULTS)

    def test_skips_dev_artifact_directory(self):
        nm = self.docs / "some-project" / "node_modules"
        nm.mkdir(parents=True)
        self._make_file(nm, "bundle.js", 120)
        self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_skips_app_bundle_contents(self):
        app_contents = self.docs / "SomeApp.app" / "Contents" / "MacOS"
        app_contents.mkdir(parents=True)
        self._make_file(app_contents, "SomeApp", 200)
        self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_symlinked_directory_not_followed(self):
        real = self.tmp / "real_outside"
        real.mkdir()
        self._make_file(real, "huge.bin", 300)
        link = self.docs / "linked"
        link.symlink_to(real)
        self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_symlinked_file_not_reported(self):
        real_file = self._make_file(self.tmp, "real.bin", 200)
        link = self.docs / "link.bin"
        link.symlink_to(real_file)
        self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_missing_root_not_fatal(self):
        with mock.patch.dict(os.environ, {
                "MACCLEANER_STORAGE_INSIGHTS_ROOTS":
                str(self.tmp / "does-not-exist")}):
            self.assertEqual(cleaner.scan_storage_insights(self.cfg), [])

    def test_never_opens_file_contents(self):
        # The entire iCloud-eviction-safety guarantee rests on this: the
        # scanner must never call open() on anything it scans. Patching
        # builtins.open to raise proves it by construction rather than by
        # inspection.
        self._make_file(self.docs, "big.mov", 150)
        with mock.patch("builtins.open", side_effect=AssertionError(
                "scan_storage_insights must never open file contents")):
            hits = cleaner.scan_storage_insights(self.cfg)
        self.assertEqual(len(hits), 1)

    def test_default_roots_env_unset(self):
        # Without the override, the function must fall back to the real
        # ~/Documents:~/Downloads:~/Desktop default -- verify the default
        # string is built correctly rather than raising or returning None.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MACCLEANER_STORAGE_INSIGHTS_ROOTS", None)
            roots = cleaner._storage_insights_roots()
        self.assertEqual(roots, [cleaner.HOME / "Documents",
                                  cleaner.HOME / "Downloads",
                                  cleaner.HOME / "Desktop"])
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_cleaner.TestStorageInsightsScanner -v`
Expected: FAIL — `AttributeError: module 'cleaner' has no attribute 'scan_storage_insights'`.

- [ ] **Step 3: Implement**

Immediately after the `TMP_CLONE_ARTIFACTS` block in `cleaner.py`:

```python
# ── Storage Insights (read-only, never wired into the delete pipeline) ────────
STORAGE_INSIGHTS_MIN_BYTES = 100 * 1024 * 1024
STORAGE_INSIGHTS_MAX_RESULTS = 50
STORAGE_INSIGHTS_ROOTS_DEFAULT = f"{HOME}/Documents:{HOME}/Downloads:{HOME}/Desktop"
_STORAGE_INSIGHTS_SKIP_DIRS = set(ARTIFACT_MANIFESTS) | TMP_CLONE_ARTIFACTS


def _storage_insights_roots():
    raw = os.environ.get("MACCLEANER_STORAGE_INSIGHTS_ROOTS", STORAGE_INSIGHTS_ROOTS_DEFAULT)
    return [Path(p) for p in raw.split(":") if p]


def scan_storage_insights(config):
    """Read-only scan of the configured roots (default ~/Documents,
    ~/Downloads, ~/Desktop) for files >= STORAGE_INSIGHTS_MIN_BYTES.

    stat()-only -- never opens file contents, so it can never trigger an
    iCloud download of an evicted file. Skips known dev-artifact
    directories (the same names scan_projects treats as noise) and never
    descends into a .app bundle. Never follows symlinks -- both symlinked
    directories and symlinked files are skipped entirely. Iterative
    (explicit stack), not recursive, so an unusually deep directory tree
    can't hit Python's recursion limit. Returns up to
    STORAGE_INSIGHTS_MAX_RESULTS entries, largest first.

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
                    if e.name in _STORAGE_INSIGHTS_SKIP_DIRS or e.name.endswith(".app"):
                        continue
                    stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    if st.st_size >= STORAGE_INSIGHTS_MIN_BYTES:
                        hits.append({"path": Path(e.path), "size_bytes": st.st_size,
                                     "mtime": st.st_mtime})
            except OSError:
                continue
    hits.sort(key=lambda h: h["size_bytes"], reverse=True)
    return hits[:STORAGE_INSIGHTS_MAX_RESULTS]
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_cleaner.TestStorageInsightsScanner -v` → PASS (12/12).
Then the full suite: `python3 -m unittest discover -s tests` → all green (377 + 12 = **389**).

- [ ] **Step 5: Commit**

```bash
git add cleaner.py tests/test_cleaner.py
git commit -m "feat: scan_storage_insights() read-only large-files scanner"
```

---

### Task 2: `storage-insights` subcommand + completions

**Files:**
- Modify: `cleaner.py` — `_relative_days()` helper, `show_storage_insights()`, `build_parser()`, `main()`
- Modify: `completions/_maccleaner`, `completions/maccleaner.bash`, `completions/run_tests.sh`
- Test: `tests/test_cleaner.py` — new class `TestStorageInsightsCommand`

**Interfaces:**
- Consumes: `scan_storage_insights(config)` (Task 1), `fmt_size(bytes_val)` (`cleaner.py:272`), `VERSION` (`cleaner.py:140`), `RICH`/`console`/`Table` (existing rich-or-plain pattern used by every other `show_*`/`run_*` output function).
- Produces: `show_storage_insights(config, json_mode=False)` — prints to stdout, no return value (matches `show_report`/`run_doctor`'s own signature shape). CLI: `storage-insights` / `storage-insights --json`. JSON shape: `{"version": str, "entries": [{"path": str, "size_bytes": int, "size_human": str, "mtime": float}, ...]}`.

- [ ] **Step 1: Write the failing tests**

```python
class TestStorageInsightsCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.docs = self.tmp / "Documents"
        self.docs.mkdir()
        self._patch = mock.patch.dict(os.environ, {
            "MACCLEANER_STORAGE_INSIGHTS_ROOTS": str(self.docs)
        })
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relative_days_buckets(self):
        now = time.time()
        self.assertEqual(cleaner._relative_days(now), "today")
        self.assertEqual(cleaner._relative_days(now - 86400 * 1.5), "yesterday")
        self.assertEqual(cleaner._relative_days(now - 86400 * 5), "5 days ago")

    def test_json_output_shape(self):
        (self.docs / "big.mov").write_bytes(b"\0" * 150 * 1024 * 1024)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cleaner.show_storage_insights({}, json_mode=True)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["version"], cleaner.VERSION)
        self.assertEqual(len(data["entries"]), 1)
        entry = data["entries"][0]
        self.assertEqual(entry["path"], str(self.docs / "big.mov"))
        self.assertEqual(entry["size_bytes"], 150 * 1024 * 1024)
        self.assertEqual(entry["size_human"], cleaner.fmt_size(150 * 1024 * 1024))
        self.assertIn("mtime", entry)

    def test_plain_output_no_crash_when_empty(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cleaner.show_storage_insights({}, json_mode=False)
        self.assertIn("100 MB", buf.getvalue())

    def test_cli_json_end_to_end(self):
        (self.docs / "big.mov").write_bytes(b"\0" * 150 * 1024 * 1024)
        env = dict(os.environ)
        r = subprocess.run([sys.executable, str(REPO / "cleaner.py"),
                            "storage-insights", "--json"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data["entries"]), 1)

    def test_no_yes_or_targets_flag_exists(self):
        # This subcommand must never grow a delete-adjacent flag -- it has
        # nothing to confirm or preview.
        import argparse
        parser = cleaner.build_parser()
        sub_action = next(a for a in parser._actions
                          if isinstance(a, argparse._SubParsersAction))
        storage_parser = sub_action.choices["storage-insights"]
        flags = {opt for action in storage_parser._actions
                 for opt in action.option_strings}
        self.assertNotIn("--yes", flags)
        self.assertNotIn("--targets", flags)
        self.assertNotIn("--dry-run", flags)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_cleaner.TestStorageInsightsCommand -v`
Expected: FAIL — `AttributeError: module 'cleaner' has no attribute '_relative_days'` (and subsequent failures for `show_storage_insights`, the CLI subcommand).

- [ ] **Step 3: Implement the helper and output function**

Immediately after `scan_storage_insights()` (end of Task 1's block):

```python
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


def show_storage_insights(config, json_mode=False):
    hits = scan_storage_insights(config)
    if json_mode:
        print(json.dumps({
            "version": VERSION,
            "entries": [
                {"path": str(h["path"]), "size_bytes": h["size_bytes"],
                 "size_human": fmt_size(h["size_bytes"]), "mtime": h["mtime"]}
                for h in hits
            ],
        }, indent=2))
        return
    if not hits:
        print("No files found at or above 100 MB in ~/Documents, ~/Downloads, or ~/Desktop.")
        return
    if RICH:
        table = Table(title="Large Files", show_lines=False)
        table.add_column("Size", style="green", justify="right")
        table.add_column("Path", style="cyan")
        table.add_column("Modified", style="yellow")
        for h in hits:
            table.add_row(fmt_size(h["size_bytes"]), str(h["path"]), _relative_days(h["mtime"]))
        console.print(table)
    else:
        print(f"\n{'='*60}")
        print("Large Files (>=100 MB)")
        print(f"{'='*60}")
        for h in hits:
            print(f"  {fmt_size(h['size_bytes']):>10}  {_relative_days(h['mtime']):>12}  {h['path']}")
        print(f"{'='*60}\n")
```

- [ ] **Step 4: Wire the subcommand into `build_parser()` and `main()`**

In `build_parser()`, immediately after the `p_disk` (`disk-check`) block and before `p_sched`:

```python
    p_storage = sub.add_parser("storage-insights",
                               help="Read-only: largest files in Documents/Downloads/Desktop (never deletes)")
    p_storage.add_argument("--json", action="store_true", help="Machine-readable output")
```

In `main()`, immediately after the `disk-check` dispatch block:

```python
    if args.command == "storage-insights":
        show_storage_insights(config, json_mode=args.json)
        return
```

- [ ] **Step 5: Run tests**

Run: `python3 -m unittest tests.test_cleaner.TestStorageInsightsCommand -v` → PASS (5/5).
Then the full suite → all green (389 + 5 = **394**).

- [ ] **Step 6: Update completions**

`completions/_maccleaner`:
- Add a subcommand description near the existing `'doctor:Check environment and install health'` line (~line 201):
  ```
  'storage-insights:Largest files in Documents/Downloads/Desktop (read-only)'
  ```
- Add `storage-insights` to the `--json --help`-only case branch (~line 300):
  ```
  doctor|categories|disk-check|storage-insights)
  ```

`completions/maccleaner.bash`:
- Add `storage-insights` to the main subcommand list in the `compgen -W` string (~line 233, alongside `scan clean projects report doctor config ...`).
- Add `storage-insights` to the `--json`-only case branch (~line 245):
  ```
  doctor|categories|disk-check|storage-insights)
  ```

`completions/run_tests.sh` — bump the two subcommand-count assertions (10 → 11):
```
eq "subcommands"          "11" "$(B 'maccleaner ' | grep -cv '^--')"
eq "zsh subcommands"      "11" "$(Z 'maccleaner ' | wc -l | tr -d ' ')"
```

- [ ] **Step 7: Run the completions harness and the drift tripwire**

```bash
bash completions/run_tests.sh
```
Expected: `passed=51 failed=0` (the two bumped counts still balance the total).

```bash
python3 -m unittest tests.test_cleaner.TestCompletions -v
```
Expected: PASS — confirms `storage-insights` and its `--json` flag are present in both completion files (this is the automated tripwire; if either file was missed, this fails with a clear message naming which one).

- [ ] **Step 8: Commit**

```bash
git add cleaner.py completions/_maccleaner completions/maccleaner.bash completions/run_tests.sh tests/test_cleaner.py
git commit -m "feat: storage-insights CLI subcommand + completions"
```

---

### Task 3: Contract documentation

**Files:**
- Modify: `AGENTS.md` — new `storage-insights` contract section, subcommand list update
- Modify: `CLAUDE.md` — commands list update

**Interfaces:** none new — this task documents Tasks 1–2's real, already-tested behavior.

- [ ] **Step 1: Add `storage-insights` to `AGENTS.md`'s top-level command list**

Find the fenced `bash` block near the top of `AGENTS.md` that lists every subcommand (mirrors `CLAUDE.md`'s own command list) and add:
```
python3 cleaner.py storage-insights   # Read-only: largest files in Documents/Downloads/Desktop (--json)
```
directly after the `disk-check` line.

- [ ] **Step 2: Add a new `storage-insights --json` contract section to `AGENTS.md`**

Add a new `###`-level section (matching the style of the existing `doctor`/`report` sections), stating:
- The exact JSON shape: `{"version": str, "entries": [{"path": str, "size_bytes": int, "size_human": str, "mtime": float}]}`.
- Roots scanned by default (`~/Documents`, `~/Downloads`, `~/Desktop`) and the `MACCLEANER_STORAGE_INSIGHTS_ROOTS` override.
- The 100 MB floor and 50-entry cap, both hardcoded (no config key).
- **Explicitly**: this command has no corresponding delete/target mechanism — `entries` are informational only, there is no `id` field, and no other command accepts a `storage-insights` entry as input. An agent reading this contract must not expect a way to act on these entries except by operating on the filesystem path directly (outside MacCleaner).
- That it never opens file contents (stat-only) and is therefore safe to run against directories containing iCloud-evicted files without triggering a download.

- [ ] **Step 3: Update `CLAUDE.md`'s command list**

Add the same `storage-insights` line to `CLAUDE.md`'s fenced command-list block (mirrors Step 1's `AGENTS.md` edit), and add one sentence to the "Python cleaner.py internals" section noting `scan_storage_insights()` exists, is stat-only, and is deliberately not part of the `get_targets()`/`collect_targets()` family.

- [ ] **Step 4: Verify**

```bash
python3 -m unittest discover -s tests   # unaffected, still green
bash completions/run_tests.sh           # 51/51
```

- [ ] **Step 5: Commit**

```bash
git commit -am "docs: storage-insights contract in AGENTS.md + CLAUDE.md"
```

---

### Task 4: App — Dashboard "Large Files" section

**Files:**
- Modify: `app/Sources/CleanerBridge.swift` — new `StorageInsightEntry`/`StorageInsightsReport` Codable models, `@Published var storageInsights`, `@Published var isScanningStorageInsights`, `scanStorageInsights()` async function
- Modify: `app/Sources/DashboardView.swift` — new section rendering the list

**Interfaces:**
- Consumes: `CleanerBridge.run<T: Decodable>(_ type: T.Type, _ args: [String]) async throws -> T` (`app/Sources/CleanerBridge.swift:333`, the existing generic engine-call-and-decode helper every other fetch function already uses).
- Produces: `StorageInsightsReport.entries: [StorageInsightEntry]`; `CleanerBridge.storageInsights: StorageInsightsReport?`; `CleanerBridge.scanStorageInsights() async`.

- [ ] **Step 1: Add the Codable models**

In `app/Sources/CleanerBridge.swift`, immediately after the existing `CategoriesReport` struct:

```swift
struct StorageInsightEntry: Codable, Identifiable {
    let path: String
    let size_bytes: Int
    let size_human: String
    let mtime: Double
    var id: String { path }
}

struct StorageInsightsReport: Codable {
    let entries: [StorageInsightEntry]
}
```

- [ ] **Step 2: Add the published state and fetch function**

Immediately after the existing `@Published var projects: ProjectsReport?` declaration (`app/Sources/CleanerBridge.swift:205`):

```swift
    @Published var storageInsights: StorageInsightsReport?
```

Immediately after the existing `@Published var isScanningProjects = false` declaration (`app/Sources/CleanerBridge.swift:238`):

```swift
    @Published var isScanningStorageInsights = false
```

Immediately after the existing `scanProjects()` function (`app/Sources/CleanerBridge.swift:576-585`):

```swift
    func scanStorageInsights() async {
        isScanningStorageInsights = true
        defer { isScanningStorageInsights = false }
        do {
            storageInsights = try await run(StorageInsightsReport.self, ["storage-insights", "--json"])
        } catch {
            statusMessage = "Storage insights scan failed: \(error.localizedDescription)"
        }
    }
```

- [ ] **Step 3: Build to confirm the model/bridge changes compile**

```bash
bash app/build.sh
```
Expected: clean build, no new warnings. (No behavior to test yet — the Dashboard doesn't call `scanStorageInsights()` until Step 4.)

- [ ] **Step 4: Add the Dashboard section**

In `app/Sources/DashboardView.swift`, add a new section after the existing category-list content (inside the same `VStack(alignment: .leading, spacing: 10)` at line 53, as a sibling section following the same header + row-list pattern the category sections already use):

```swift
    private var largeFilesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Large Files")
                .font(DesignSystem.Typography.sectionHeader)
            if let entries = bridge.storageInsights?.entries, !entries.isEmpty {
                ForEach(entries) { entry in
                    LargeFileRow(entry: entry)
                }
            } else if bridge.isScanningStorageInsights {
                ProgressView().frame(maxWidth: .infinity, alignment: .center)
            } else {
                Text("No files ≥100 MB found in Documents, Downloads, or Desktop.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .task { await bridge.scanStorageInsights() }
    }
```

(Check `DesignSystem.Typography`'s exact token name for section headers before using `sectionHeader` verbatim — grep `DesignSystem.swift` for how the existing category-section title is styled and match that token exactly rather than assuming the name.)

Add `largeFilesSection` into the existing body's `VStack`, after the category list content.

Add a new row view, following the existing `TargetRow` struct's shape (`app/Sources/DashboardView.swift:237`) as the template for spacing/font/layout conventions:

```swift
struct LargeFileRow: View {
    let entry: StorageInsightEntry

    private var relativeModified: String {
        let date = Date(timeIntervalSince1970: entry.mtime)
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text((entry.path as NSString).lastPathComponent)
                    .font(.system(.body, design: .monospaced))
                Text(relativeModified)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(entry.size_human)
                .font(.system(.body, design: .monospaced))
                .foregroundStyle(.secondary)
            Button {
                NSWorkspace.shared.selectFile(entry.path, inFileViewerRootedAtPath: "")
            } label: {
                Image(systemName: "magnifyingglass")
            }
            .buttonStyle(.borderless)
            .help("Reveal in Finder")
        }
        .padding(.vertical, 2)
    }
}
```

- [ ] **Step 5: Build and visually verify**

```bash
bash app/build.sh --install
```
Then launch `~/Applications/MacCleaner.app`, open the Dashboard, confirm:
- The "Large Files" section appears and populates (create a scratch file ≥100 MB under `~/Documents` first if the real machine has none, to confirm the populated state — remove the scratch file afterward).
- Clicking the magnifying-glass button opens Finder with that file selected.
- The empty state (no qualifying files) renders sensibly if you remove the scratch file and rescan.

- [ ] **Step 6: Commit**

```bash
git add app/Sources/CleanerBridge.swift app/Sources/DashboardView.swift
git commit -m "feat(app): Dashboard Large Files section with Reveal in Finder"
```

---

### Task 5: Version bump, changelog, bundle, and end-to-end verification

**Files:** `cleaner.py` (VERSION), `app/Info.plist` (both version keys), `AGENTS.md` (version sweep), `CHANGELOG.md`, `ROADMAP.md`, `MacCleaner.app` (rebuilt bundle)

- [ ] **Step 1: End-to-end verification of the new subcommand**

```bash
mkdir -p /tmp/si-e2e/Documents /tmp/si-e2e/Downloads /tmp/si-e2e/Desktop
dd if=/dev/zero of=/tmp/si-e2e/Documents/big.mov bs=1m count=150 2>/dev/null
MACCLEANER_STORAGE_INSIGHTS_ROOTS="/tmp/si-e2e/Documents:/tmp/si-e2e/Downloads:/tmp/si-e2e/Desktop" \
  python3 cleaner.py storage-insights --json | python3 -m json.tool
MACCLEANER_STORAGE_INSIGHTS_ROOTS="/tmp/si-e2e/Documents:/tmp/si-e2e/Downloads:/tmp/si-e2e/Desktop" \
  python3 cleaner.py storage-insights
rm -rf /tmp/si-e2e
```
Confirm the JSON lists `big.mov` at 150 MB, the plain-text table renders it, and nothing under `/tmp/si-e2e` needed to be manually cleaned up by anything other than this `rm -rf` (the tool itself must never have touched it).

- [ ] **Step 2: Confirm real-machine safety (read-only)**

```bash
time python3 cleaner.py storage-insights --json > /dev/null
```
Confirm this completes quickly (a few seconds at most — Documents/Downloads/Desktop are not deep trees for most users) and does not hang (a hang would indicate an iCloud-eviction problem slipping through despite the stat-only design — if this happens, stop and investigate before proceeding, do not paper over it).

- [ ] **Step 3: Version sweep**

Derive the current version from the code first (`grep -n '^VERSION' cleaner.py`) — do not assume "2.8.1" is still current. Bump to **2.9.0** (new subcommand + new app surface area, not a patch fix): `VERSION` in `cleaner.py`, both version keys in `app/Info.plist`, every `"version": "X.Y.Z"` JSON example plus the intro line in `AGENTS.md`. Confirm nothing was missed:
```bash
grep -rn "2\.8\.1" --exclude-dir=.git --exclude-dir=docs . | grep -v CHANGELOG.md
```
Remaining hits should only be legitimately historical (CHANGELOG's own 2.8.1 section, "new in 2.8.1"-style provenance notes, and `Casks/maccleaner.rb`, bumped separately post-release).

- [ ] **Step 4: CHANGELOG**

New `## [2.9.0] — <today's date>` section covering: the new `storage-insights` subcommand, what it scans and why (Documents/Downloads/Desktop, 100 MB floor, top 50), that it's read-only and architecturally separate from the delete pipeline (no target, no category, no `--yes`), the iCloud-eviction-safety guarantee (stat-only, never opens file contents), and the new Dashboard "Large Files" section with Reveal in Finder.

- [ ] **Step 5: Roadmap**

Update `ROADMAP.md`'s "Current State" header to "v2.9.0 ✅" and add a `- **v2.9 additions**` bullet summarizing the feature, following the exact style of the existing v2.7/v2.8 bullets (one paragraph, concrete details, ending with the new test count).

- [ ] **Step 6: Rebuild the committed app bundle**

```bash
bash app/build.sh && rm -rf MacCleaner.app && cp -R build/MacCleaner.app MacCleaner.app
xattr -cr MacCleaner.app
cmp cleaner.py MacCleaner.app/Contents/Resources/cleaner.py && echo "bundle engine in sync"
```

- [ ] **Step 7: Full verification**

```bash
python3 -m unittest discover -s tests
bash completions/run_tests.sh
python3 cleaner.py doctor; echo "exit=$?"   # must still be 0
```

- [ ] **Step 8: Commit**

```bash
git status   # review before staging
git commit -am "chore: release 2.9.0"
```

- [ ] **Step 9: Ship**

Push the branch, open a PR to `main`, wait for CI (both required checks — Tests + Smoke Test, Build macOS app — per the branch protection already in place), squash-merge (branch auto-deletes on merge), tag `v2.9.0`, push the tag, then verify the published release (`stapler validate`, `spctl -a -vvv -t exec`, appcast advertises 2.9.0 with a matching enclosure length/signature) and bump the `Fullex26/homebrew-tap` cask per `docs/RELEASING.md` §5, syncing the repo's `Casks/maccleaner.rb` in a follow-up PR. **These are outward-facing actions — confirm with the user before executing this step.**
