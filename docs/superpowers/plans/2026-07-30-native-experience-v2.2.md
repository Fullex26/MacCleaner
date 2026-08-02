# Native Experience v2.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MacCleaner feel native — launchd scheduling that survives sleep, notifications when a scheduled clean finishes, an hourly low-disk watch, and a live menu bar with "last cleaned".

**Architecture:** The engine grows the primitives (`_notify`, `clean --notify`, a cheap `disk-check` with throttle state in `alerts.json`); `scheduler.sh` installs two launchd agents and migrates existing cron users; the SwiftUI app adds native notifications, split-cadence refresh, and Settings toggles. Every behavior is reachable and testable from the CLI — Swift stays a thin client.

**Tech Stack:** Python 3 stdlib only (`cleaner.py`), bash (`scheduler.sh`), launchd plists, SwiftUI + UserNotifications (macOS 13+). Tests: stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-07-30-native-experience-v2.2-design.md`

## Global Constraints

- `cleaner.py` stays a single file, Python 3 **stdlib only**. `rich` is optional — every human-output path needs a working plain variant.
- JSON contract is **additive only**: never remove or rename existing keys. JSON → stdout, human messages → stderr. Exit codes: 0 success, 1 runtime error, 2 usage error.
- `translate_legacy()` v1 spellings must keep working.
- Deletion logic (`_safe_to_delete`, `_remove`, `delete_target`) must not be touched by this plan.
- Notification failures, `launchctl` failures, and state-file corruption **degrade** — they warn on stderr and never change a command's exit code.
- Tests: `python3 -m unittest discover -s tests` from the repo root, stdlib only. Every filesystem-touching test sandboxes via `tempfile.mkdtemp()` plus the env vars `MACCLEANER_CONFIG` / `MACCLEANER_LOG` / `MACCLEANER_SNAPSHOTS` / `MACCLEANER_ALERTS` (new) and `HOME` for subprocess tests. **A test must never write into the repo, the real home, the real crontab, or the real `~/Library/LaunchAgents`, and must never post a real notification.**
- Baseline: 89 tests passing on `main` @ c943ebb. Zero failures is the gate at every task, not the exact count.
- The two notification switches are independent: `notifications` governs only the clean-finished notification; `low_disk_alerts` governs only the low-disk warning.

## File Structure

- Modify: `cleaner.py` — config keys, `_notify`/`_notify_argv`, `clean --notify`, `disk-check`, `alerts.json` helpers, `doctor` schedule check (single-file engine; the repo's convention)
- Modify: `scheduler.sh` — launchd agent generation, cron migration, status
- Modify: `tests/test_cleaner.py` — new classes `TestNotify`, `TestDiskCheck`, `TestLowDiskThrottle`, `TestScheduler`
- Create: `app/Sources/NotificationManager.swift` — one responsibility: request authorization, post notifications, degrade silently
- Modify: `app/Sources/CleanerBridge.swift` — refresh timers, `lastCleanedAt`, notification/alert config
- Modify: `app/Sources/MacCleanerApp.swift` — "Last cleaned" menu row, menu-open refresh
- Modify: `app/Sources/SettingsView.swift` — notification + low-disk toggles and threshold
- Modify: `app/Info.plist` — version 2.2.0
- Modify: `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`

---

### Task 1: Config keys + notification primitive

**Files:**
- Modify: `cleaner.py` — `DEFAULT_CONFIG` (~line 96), new `ALERTS_PATH` constant beside `SNAPSHOTS_PATH` (~line 103), new `_notify_argv`/`_notify` functions
- Test: `tests/test_cleaner.py`

**Interfaces:**
- Consumes: `_resolve_state_path(env_var, filename, script_dir=None) -> Path` (existing, v2.1).
- Produces: config keys `notifications` (True), `low_disk_alerts` (True), `low_disk_threshold_gb` (10), `full_refresh_hours` (6); `ALERTS_PATH: Path`; `_escape_applescript(text) -> str`; `_notify_argv(title, message) -> list[str]`; `_notify(title, message) -> bool` (True when a notification was posted). Tasks 2 and 3 call `_notify`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cleaner.py`:

```python
class TestNotify(unittest.TestCase):
    def test_new_config_defaults(self):
        cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))
        self.assertTrue(cfg["notifications"])
        self.assertTrue(cfg["low_disk_alerts"])
        self.assertEqual(cfg["low_disk_threshold_gb"], 10)
        self.assertEqual(cfg["full_refresh_hours"], 6)

    def test_new_keys_merge_into_old_config(self):
        tmp = Path(tempfile.mkdtemp())
        orig = cleaner.CONFIG_PATH
        cleaner.CONFIG_PATH = tmp / "config.json"
        try:
            cleaner.CONFIG_PATH.write_text('{"enabled_categories": ["node"]}')
            cfg = cleaner.load_config()
            self.assertEqual(cfg["enabled_categories"], ["node"])
            self.assertTrue(cfg["low_disk_alerts"])
            self.assertEqual(cfg["low_disk_threshold_gb"], 10)
        finally:
            cleaner.CONFIG_PATH = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_alerts_path_resolution(self):
        """The override wins; otherwise alerts.json sits beside cleaner.py."""
        installed = Path.home() / "mac-cleaner"
        self.assertEqual(
            cleaner._resolve_state_path("MACCLEANER_ALERTS", "alerts.json", installed),
            installed / "alerts.json")
        os.environ["MACCLEANER_ALERTS"] = "/tmp/alerts-override-test.json"
        try:
            self.assertEqual(
                cleaner._resolve_state_path("MACCLEANER_ALERTS", "alerts.json", installed),
                Path("/tmp/alerts-override-test.json"))
        finally:
            del os.environ["MACCLEANER_ALERTS"]

    def test_escape_applescript(self):
        self.assertEqual(cleaner._escape_applescript('say "hi"'), 'say \\"hi\\"')
        self.assertEqual(cleaner._escape_applescript(r"back\slash"), r"back\\slash")
        self.assertEqual(cleaner._escape_applescript("plain"), "plain")

    def test_notify_argv_shape(self):
        argv = cleaner._notify_argv("MacCleaner freed 1.0 GB", 'a "quoted" note')
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[1], "-e")
        self.assertIn('display notification "a \\"quoted\\" note"', argv[2])
        self.assertIn('with title "MacCleaner freed 1.0 GB"', argv[2])
        self.assertEqual(len(argv), 3)

    def test_notify_survives_missing_binary(self):
        """A notification failure must never raise into the caller."""
        orig = cleaner._notify_argv
        cleaner._notify_argv = lambda t, m: ["definitely-not-a-real-binary-xyz"]
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                result = cleaner._notify("t", "m")
            self.assertFalse(result)
            self.assertIn("notif", err.getvalue().lower())
        finally:
            cleaner._notify_argv = orig
```

Add `import contextlib` and `import io` to the test file's imports if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cleaner.TestNotify -v`
Expected: FAIL/ERROR — `KeyError: 'notifications'`, `AttributeError: module 'cleaner' has no attribute '_escape_applescript'`.

- [ ] **Step 3: Implement.** Three edits to `cleaner.py`:

3a. `DEFAULT_CONFIG` — add after `"project_git_check": True,`:

```python
    "notifications": True,           # notify when a scheduled clean finishes
    "low_disk_alerts": True,         # warn when free space drops below the threshold
    "low_disk_threshold_gb": 10,     # the low-disk warning threshold
    "full_refresh_hours": 6,         # how often the app runs a full scan (app-side)
```

3b. After the `SNAPSHOTS_PATH` line (~103), add:

```python
ALERTS_PATH = _resolve_state_path("MACCLEANER_ALERTS", "alerts.json")
```

3c. Add a notification section after `fmt_size()` (so `fmt_size` is available to callers building messages):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cleaner.TestNotify -v` → all PASS.
Run: `python3 -m unittest discover -s tests` → 95 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add cleaner.py tests/test_cleaner.py
git commit -m "feat: notification primitive and v2.2 config keys"
```

---

### Task 2: `clean --notify`

**Files:**
- Modify: `cleaner.py` — `run_clean()` signature and its tail (after the snapshot block, before the JSON/human output), `build_parser()`'s `clean` subparser (~line 1661), `main()`'s clean branch
- Test: `tests/test_cleaner.py`

**Interfaces:**
- Consumes: `_notify(title, message) -> bool` (Task 1), config key `notifications` (Task 1), existing `run_clean(targets, auto_approve, mode, json_mode, explicit, snapshot_scope)`.
- Produces: `run_clean(..., notify=False)` keyword; `--notify` flag on `clean`. Task 4's launchd plist invokes `clean --yes --notify`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cleaner.py`:

```python
class TestCleanNotify(unittest.TestCase):
    """--notify must be observable without posting a real notification: the
    tests capture the argv _notify would have used by swapping the primitive."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".npm" / "_cacache").mkdir(parents=True)
        (self.home / ".npm" / "_cacache" / "blob").write_text("x" * 4096)
        self.cfg_path = self.tmp / "config.json"
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(self.cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log"),
                    "MACCLEANER_ALERTS": str(self.tmp / "alerts.json")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_cfg(self, **extra):
        cfg = {"enabled_categories": ["node"]}
        cfg.update(extra)
        self.cfg_path.write_text(json.dumps(cfg))

    def run_cli(self, *args, fake_osascript=True):
        """Run the CLI with a stub `osascript` early on PATH that records its
        argv to a file, so we can assert on the notification without posting."""
        env = dict(self.env)
        if fake_osascript:
            bindir = self.tmp / "bin"
            bindir.mkdir(exist_ok=True)
            recorded = self.tmp / "notified.txt"
            stub = bindir / "osascript"
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$RECORD_FILE"\n')
            stub.chmod(0o755)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["RECORD_FILE"] = str(recorded)
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def notified_text(self):
        f = self.tmp / "notified.txt"
        return f.read_text() if f.exists() else ""

    def test_notify_posts_after_clean(self):
        self.write_cfg()
        r = self.run_cli("clean", "--yes", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertGreater(data["freed_bytes"], 0)
        self.assertIn("display notification", self.notified_text())
        self.assertIn("MacCleaner", self.notified_text())

    def test_notifications_disabled_suppresses(self):
        self.write_cfg(notifications=False)
        r = self.run_cli("clean", "--yes", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "",
                         "notifications:false must suppress the notification")
        self.assertFalse((self.home / ".npm" / "_cacache").exists(),
                         "the clean itself must still happen")

    def test_no_flag_no_notification(self):
        self.write_cfg()
        r = self.run_cli("clean", "--yes", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "")

    def test_dry_run_never_notifies(self):
        self.write_cfg()
        r = self.run_cli("clean", "--dry-run", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "")
        self.assertTrue((self.home / ".npm" / "_cacache").exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cleaner.TestCleanNotify -v`
Expected: FAIL — argparse exits 2 on `unrecognized arguments: --notify`, so the `returncode == 0` assertions fail.

- [ ] **Step 3: Implement.** Three edits to `cleaner.py`:

3a. `run_clean` signature:

```python
def run_clean(targets, auto_approve=False, mode="rm", json_mode=False, explicit=False,
              snapshot_scope="partial", notify=False):
```

3b. In `run_clean`, immediately after the snapshot block (`record_snapshot()` / the `if snapshot_scope == "full":` block) and **before** the `if json_mode:` output block:

```python
    if notify and load_config().get("notifications", True):
        cleaned = sum(1 for r in results if r["status"] in ("deleted", "trashed"))
        _notify(f"MacCleaner freed {fmt_size(total_freed)}",
                f"{cleaned} item{'s' if cleaned != 1 else ''} cleaned · "
                f"{fmt_size(disk_stats()['free_bytes'])} free")
```

3c. `build_parser()` — add to the `clean` subparser after `--dry-run`:

```python
    p_clean.add_argument("--notify", action="store_true",
                         help="Post a macOS notification when the clean finishes")
```

3d. `main()`'s clean branch — pass the flag through on the `run_clean(...)` call:

```python
        run_clean(targets, auto_approve=auto, mode=mode, json_mode=args.json,
                  explicit=explicit, snapshot_scope="full" if full else "partial",
                  notify=args.notify)
```

The dry-run branch returns before `run_clean`, so `--dry-run --notify` posts nothing without any extra guard — the test above locks that in.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cleaner.TestCleanNotify -v` → PASS.
Run: `python3 -m unittest discover -s tests` → 99 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add cleaner.py tests/test_cleaner.py
git commit -m "feat: clean --notify posts a summary when a scheduled clean finishes"
```

---

### Task 3: `disk-check` subcommand + low-disk throttle

**Files:**
- Modify: `cleaner.py` — new `load_alerts()`/`save_alerts()`/`_low_disk_decision()`/`run_disk_check()` after the snapshot section, `build_parser()` (new subparser), `main()` (new branch)
- Test: `tests/test_cleaner.py`

**Interfaces:**
- Consumes: `ALERTS_PATH` and `_notify` (Task 1), existing `disk_stats()`, `fmt_size()`, `_atomic_write_json(path, data)`, `load_config()`.
- Produces: `load_alerts() -> dict`; `save_alerts(alerts) -> None`; `_low_disk_decision(alerts, now, free_bytes, threshold_bytes) -> tuple[bool, dict]` (pure — `(should_notify, new_low_disk_state)`); `run_disk_check(config, json_mode=False) -> dict`; `disk-check` subcommand. Task 4's diskwatch plist invokes `disk-check`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cleaner.py`:

```python
class TestLowDiskThrottle(unittest.TestCase):
    """_low_disk_decision is pure: (alerts, now, free, threshold) -> (notify?, state)."""

    def setUp(self):
        self.now = datetime.datetime(2026, 7, 30, 12, 0, 0)
        self.threshold = 10 * 1024**3

    def decide(self, alerts, free, now=None):
        return cleaner._low_disk_decision(alerts, now or self.now, free, self.threshold)

    def test_first_dip_notifies(self):
        notify, state = self.decide({}, 5 * 1024**3)
        self.assertTrue(notify)
        self.assertEqual(state["state"], "below")
        self.assertEqual(state["last_notified"], self.now.isoformat())

    def test_above_threshold_never_notifies(self):
        notify, state = self.decide({}, 500 * 1024**3)
        self.assertFalse(notify)
        self.assertEqual(state["state"], "above")

    def test_still_below_within_24h_stays_quiet(self):
        alerts = {"low_disk": {"state": "below",
                               "last_notified": (self.now - datetime.timedelta(hours=3)).isoformat()}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertFalse(notify, "must not re-notify hourly")
        self.assertEqual(state["state"], "below")

    def test_still_below_after_24h_renotifies(self):
        alerts = {"low_disk": {"state": "below",
                               "last_notified": (self.now - datetime.timedelta(hours=25)).isoformat()}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify)
        self.assertEqual(state["last_notified"], self.now.isoformat())

    def test_recovery_then_new_dip_notifies_immediately(self):
        recovered = {"low_disk": {"state": "below",
                                  "last_notified": (self.now - datetime.timedelta(hours=1)).isoformat()}}
        notify, state = self.decide(recovered, 500 * 1024**3)
        self.assertFalse(notify)
        self.assertEqual(state["state"], "above")
        notify2, _ = self.decide({"low_disk": state}, 5 * 1024**3)
        self.assertTrue(notify2, "a fresh dip after recovery must notify at once")

    def test_corrupt_last_notified_notifies(self):
        alerts = {"low_disk": {"state": "below", "last_notified": "not-a-timestamp"}}
        notify, _ = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify)


class TestDiskCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.alerts = self.tmp / "alerts.json"
        self.log = self.tmp / "report.log"
        self.snaps = self.tmp / "snapshots.log"
        self.cfg_path = self.tmp / "config.json"
        self.env = {**os.environ, "HOME": str(self.tmp),
                    "MACCLEANER_CONFIG": str(self.cfg_path),
                    "MACCLEANER_LOG": str(self.log),
                    "MACCLEANER_SNAPSHOTS": str(self.snaps),
                    "MACCLEANER_ALERTS": str(self.alerts)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, **cfg):
        self.cfg_path.write_text(json.dumps(cfg) if cfg else "{}")
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "osascript"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$RECORD_FILE"\n')
        stub.chmod(0o755)
        env = {**self.env, "PATH": f"{bindir}:{os.environ['PATH']}",
               "RECORD_FILE": str(self.tmp / "notified.txt")}
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def notified(self):
        f = self.tmp / "notified.txt"
        return f.read_text() if f.exists() else ""

    def test_json_shape_and_exit_zero(self):
        r = self.run_cli("disk-check", "--json")
        self.assertEqual(r.returncode, 0, "disk-check is a monitor: always exit 0")
        data = json.loads(r.stdout)
        for key in ["free_bytes", "free_human", "threshold_bytes",
                    "below_threshold", "notified"]:
            self.assertIn(key, data)
        self.assertIsInstance(data["below_threshold"], bool)

    def test_huge_threshold_triggers_notification(self):
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertTrue(data["below_threshold"])
        self.assertTrue(data["notified"])
        self.assertIn("display notification", self.notified())
        self.assertTrue(self.alerts.exists())
        state = json.loads(self.alerts.read_text())["low_disk"]
        self.assertEqual(state["state"], "below")

    def test_alerts_disabled_reports_but_stays_quiet(self):
        r = self.run_cli("disk-check", "--json",
                         low_disk_threshold_gb=10_000_000, low_disk_alerts=False)
        data = json.loads(r.stdout)
        self.assertTrue(data["below_threshold"], "numbers still reported")
        self.assertFalse(data["notified"])
        self.assertEqual(self.notified(), "")

    def test_no_side_effect_files(self):
        r = self.run_cli("disk-check", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.log.exists(), "disk-check must not write report.log")
        self.assertFalse(self.snaps.exists(), "disk-check must not record a snapshot")

    def test_corrupt_alerts_file_self_heals(self):
        self.alerts.write_text("{not json")
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["notified"])
        self.assertIn("low_disk", json.loads(self.alerts.read_text()))

    def test_human_output(self):
        r = self.run_cli("disk-check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("free", r.stdout.lower())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cleaner.TestLowDiskThrottle tests.test_cleaner.TestDiskCheck -v`
Expected: ERROR — `AttributeError: module 'cleaner' has no attribute '_low_disk_decision'`; the CLI tests fail with exit code 2 (`invalid choice: 'disk-check'`).

- [ ] **Step 3: Implement.** Add a section to `cleaner.py` after the snapshot helpers (after `format_disk_trend`/`_print_disk_trend`):

```python
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
    threshold = int(float(config.get("low_disk_threshold_gb", 10)) * 1024**3)
    free = ds["free_bytes"]
    enabled = config.get("low_disk_alerts", True)

    alerts = load_alerts()
    should_notify, state = _low_disk_decision(alerts, datetime.datetime.now(), free, threshold)
    notified = False
    if enabled and should_notify:
        notified = _notify(
            f"Low disk space: {fmt_size(free)} free",
            f"Below your {fmt_size(threshold)} threshold — "
            f"open MacCleaner to reclaim space.")
    if enabled:
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
```

Note the `if enabled:` guard on persisting state — with alerts off we report the numbers but leave the throttle untouched, so turning alerts back on doesn't inherit a stale "already warned" stamp.

`build_parser()` — add before the `install-deps` parser:

```python
    p_disk = sub.add_parser("disk-check",
                            help="Warn when free space is below the configured threshold (cheap; for launchd)")
    p_disk.add_argument("--json", action="store_true", help="Machine-readable output")
```

`main()` — add a branch next to the other early-return commands (after the `doctor` branch):

```python
    if args.command == "disk-check":
        run_disk_check(config, json_mode=args.json)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cleaner.TestLowDiskThrottle tests.test_cleaner.TestDiskCheck -v` → PASS.
Run: `python3 -m unittest discover -s tests` → 112 tests, OK.

- [ ] **Step 5: Commit**

```bash
git add cleaner.py tests/test_cleaner.py
git commit -m "feat: disk-check subcommand with throttled low-disk alerts"
```

---

### Task 4: launchd scheduling + cron migration

**Files:**
- Modify: `scheduler.sh` (full rewrite of the scheduling mechanism, same command surface)
- Modify: `cleaner.py` — `run_doctor()`'s Schedule check (~line 1307)
- Test: `tests/test_cleaner.py`

**Interfaces:**
- Consumes: `cleaner.py disk-check` (Task 3) and `clean --yes --notify` (Task 2).
- Produces: agents `com.fullex.maccleaner.clean` and `com.fullex.maccleaner.diskwatch` in `$MACCLEANER_LAUNCH_AGENTS_DIR` (default `~/Library/LaunchAgents`); `scheduler.sh weekly|monthly|remove|status`; doctor reports launchd state.

**Why launchd:** cron silently skips a job whose time passed while the Mac was asleep. `StartCalendarInterval` runs it on wake — the whole reason for the switch.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cleaner.py`:

```python
class TestScheduler(unittest.TestCase):
    """scheduler.sh against a sandboxed LaunchAgents dir with stub
    launchctl/crontab on PATH — never touches the real agents or crontab."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.calls = self.tmp / "calls.txt"
        self.crontab_file = self.tmp / "crontab.txt"
        self.crontab_file.write_text("")
        for name in ("launchctl", "crontab"):
            stub = self.bindir / name
            stub.write_text(
                '#!/bin/sh\n'
                f'printf "{name} %s\\n" "$*" >> "$CALLS_FILE"\n'
                'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
                'if [ "$1" = "list" ]; then grep -o "maccleaner[a-z.]*" "$CALLS_FILE" 2>/dev/null | head -1; exit 0; fi\n'
                'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > "$CRONTAB_FILE"; fi\n'
                'exit 0\n')
            stub.chmod(0o755)
        self.env = {**os.environ,
                    "PATH": f"{self.bindir}:{os.environ['PATH']}",
                    "HOME": str(self.tmp),
                    "MACCLEANER_LAUNCH_AGENTS_DIR": str(self.agents),
                    "CALLS_FILE": str(self.calls),
                    "CRONTAB_FILE": str(self.crontab_file)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sched(self, *args):
        return subprocess.run(["bash", str(REPO / "scheduler.sh"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def plist(self, label):
        return self.agents / f"com.fullex.maccleaner.{label}.plist"

    def test_weekly_installs_both_agents(self):
        r = self.sched("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.plist("clean").exists(), "clean agent missing")
        self.assertTrue(self.plist("diskwatch").exists(), "diskwatch agent missing")

    def test_plists_are_valid(self):
        self.sched("weekly")
        for label in ("clean", "diskwatch"):
            lint = subprocess.run(["plutil", "-lint", str(self.plist(label))],
                                  capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, f"{label}: {lint.stdout}{lint.stderr}")

    def test_clean_agent_content(self):
        self.sched("weekly")
        body = self.plist("clean").read_text()
        self.assertIn("com.fullex.maccleaner.clean", body)
        self.assertIn("cleaner.py", body)
        self.assertIn("--notify", body)
        self.assertIn("StartCalendarInterval", body)
        self.assertIn("<key>Weekday</key>", body)

    def test_monthly_uses_day_not_weekday(self):
        self.sched("monthly")
        body = self.plist("clean").read_text()
        self.assertIn("<key>Day</key>", body)
        self.assertNotIn("<key>Weekday</key>", body)

    def test_diskwatch_agent_content(self):
        self.sched("weekly")
        body = self.plist("diskwatch").read_text()
        self.assertIn("disk-check", body)
        self.assertIn("StartInterval", body)
        self.assertIn("3600", body)

    def test_remove_deletes_both(self):
        self.sched("weekly")
        r = self.sched("remove")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.plist("clean").exists())
        self.assertFalse(self.plist("diskwatch").exists())

    def test_reinstall_replaces_not_stacks(self):
        self.sched("weekly")
        self.sched("monthly")
        body = self.plist("clean").read_text()
        self.assertIn("<key>Day</key>", body)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)

    def test_migrates_cron_weekly(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.sched("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("migrat", (r.stdout + r.stderr).lower())
        self.assertTrue(self.plist("clean").exists())
        self.assertIn("<key>Weekday</key>", self.plist("clean").read_text())
        self.assertNotIn("cleaner.py", self.crontab_file.read_text(),
                         "the cron line must be removed after migration")

    def test_migration_preserves_monthly(self):
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.sched("status")
        self.assertIn("<key>Day</key>", self.plist("clean").read_text())

    def test_migration_keeps_unrelated_cron_lines(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "*/5 * * * * /usr/local/bin/other-job\n")
        self.sched("status")
        remaining = self.crontab_file.read_text()
        self.assertIn("other-job", remaining, "unrelated cron jobs must survive")
        self.assertNotIn("cleaner.py", remaining)

    def test_migration_is_idempotent(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.sched("status")
        second = self.sched("status")
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("migrat", second.stdout.lower(),
                         "second run has nothing to migrate")

    def test_status_reports_installed(self):
        self.sched("weekly")
        r = self.sched("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("maccleaner", r.stdout.lower())

    def test_usage_when_no_command(self):
        r = self.sched()
        self.assertIn("weekly", r.stdout)
        self.assertIn("monthly", r.stdout)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cleaner.TestScheduler -v`
Expected: FAIL — no plists are created (`scheduler.sh` still writes crontab lines), so the `exists()` assertions fail.

- [ ] **Step 3: Implement `scheduler.sh`** — replace the whole file:

```bash
#!/bin/bash
# MacCleaner Scheduler — install or remove the launchd agents
#
# Two agents:
#   com.fullex.maccleaner.clean      weekly/monthly scheduled clean (--notify)
#   com.fullex.maccleaner.diskwatch  hourly low-disk check
#
# launchd rather than cron: launchd runs a missed calendar job after the Mac
# wakes; cron silently skips it.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER="$SCRIPT_DIR/cleaner.py"
LOG="$SCRIPT_DIR/cron.log"          # same path v1/v2.1 used; users know it
PYTHON="$(command -v python3)"

# Overridable for tests only; defaults to the real per-user agents directory.
AGENTS_DIR="${MACCLEANER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
CLEAN_LABEL="com.fullex.maccleaner.clean"
WATCH_LABEL="com.fullex.maccleaner.diskwatch"

write_plist() {
    # $1 = label, $2 = XML for program args, $3 = XML for the trigger
    local label="$1" args_xml="$2" trigger_xml="$3"
    mkdir -p "$AGENTS_DIR"
    cat > "$AGENTS_DIR/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$label</string>
    <key>ProgramArguments</key>
    <array>
$args_xml
    </array>
$trigger_xml
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
PLIST
}

bootstrap() {
    local label="$1" plist="$AGENTS_DIR/$1.plist"
    launchctl bootout "gui/$UID/$label" 2>/dev/null
    if ! launchctl bootstrap "gui/$UID" "$plist" 2>/dev/null; then
        # Older macOS, or a launchd that dislikes bootstrap for this domain
        launchctl unload "$plist" 2>/dev/null
        if ! launchctl load "$plist" 2>/dev/null; then
            echo "⚠️  Could not load $label with launchctl." >&2
            echo "    The plist is written to $plist — load it manually with:" >&2
            echo "    launchctl bootstrap gui/$UID \"$plist\"" >&2
            return 1
        fi
    fi
}

unload_agent() {
    local label="$1"
    launchctl bootout "gui/$UID/$label" 2>/dev/null || launchctl unload "$AGENTS_DIR/$label.plist" 2>/dev/null
    rm -f "$AGENTS_DIR/$label.plist"
}

install_diskwatch() {
    write_plist "$WATCH_LABEL" \
"        <string>$PYTHON</string>
        <string>$CLEANER</string>
        <string>disk-check</string>" \
"    <key>StartInterval</key>
    <integer>3600</integer>"
    bootstrap "$WATCH_LABEL"
}

install_clean() {
    # $1 = "weekly" | "monthly"
    local trigger
    if [ "$1" = "monthly" ]; then
        trigger="    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"
    else
        trigger="    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>"
    fi
    write_plist "$CLEAN_LABEL" \
"        <string>$PYTHON</string>
        <string>$CLEANER</string>
        <string>clean</string>
        <string>--yes</string>
        <string>--notify</string>" \
"$trigger"
    bootstrap "$CLEAN_LABEL"
}

install_schedule() {
    install_clean "$1"
    install_diskwatch
    if [ "$1" = "monthly" ]; then
        echo "✅ Scheduled: 1st of every month at 9am (launchd)"
    else
        echo "✅ Scheduled: every Monday at 9am (launchd)"
    fi
    echo "   Low-disk check: hourly"
    echo "   Log: $LOG"
}

# Migrate a legacy cron line, if any, to launchd. Idempotent.
migrate_cron() {
    local existing
    existing="$(crontab -l 2>/dev/null)" || return 0
    case "$existing" in
        *cleaner.py*) ;;
        *) return 0 ;;
    esac
    local kind="weekly"
    # A monthly cron line pins day-of-month (field 3); weekly pins weekday (field 5).
    if echo "$existing" | grep "cleaner.py" | awk '{print $3}' | grep -qv '^\*$'; then
        kind="monthly"
    fi
    echo "→ Migrating your cron schedule to launchd ($kind)…"
    echo "$existing" | grep -v "cleaner.py" | crontab -
    install_schedule "$kind"
    echo "   Removed the old cron entry."
}

status() {
    echo "── MacCleaner Scheduler Status ──"
    local found=0
    for label in "$CLEAN_LABEL" "$WATCH_LABEL"; do
        if [ -f "$AGENTS_DIR/$label.plist" ]; then
            echo "✅ $label (launchd)"
            found=1
        fi
    done
    [ "$found" = 0 ] && echo "❌ Not scheduled (run ./scheduler.sh weekly)"
    if crontab -l 2>/dev/null | grep -q "cleaner.py"; then
        echo "⚠️  A legacy cron entry is still present — run ./scheduler.sh weekly to migrate."
    fi
}

migrate_cron

case "${1:-}" in
    weekly)   install_schedule weekly ;;
    monthly)  install_schedule monthly ;;
    remove)
        unload_agent "$CLEAN_LABEL"
        unload_agent "$WATCH_LABEL"
        echo "✅ Removed MacCleaner launchd agents"
        ;;
    status)   status ;;
    *)
        echo "MacCleaner Scheduler (launchd)"
        echo ""
        echo "Usage: ./scheduler.sh [command]"
        echo ""
        echo "  weekly   — Clean every Monday at 9am + hourly low-disk check"
        echo "  monthly  — Clean on the 1st of each month + hourly low-disk check"
        echo "  remove   — Remove the scheduled agents"
        echo "  status   — Show current schedule"
        echo ""
        echo "An existing cron schedule is migrated to launchd automatically."
        ;;
esac
```

- [ ] **Step 4: Update `doctor`'s Schedule check** — replace the crontab block in `run_doctor()` (~line 1305-1311):

```python
    try:
        agents = HOME / "Library/LaunchAgents"
        labels = [p.stem for p in sorted(agents.glob("com.fullex.maccleaner.*.plist"))] \
            if agents.exists() else []
        cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        has_cron = cron.returncode == 0 and "cleaner.py" in cron.stdout
        if labels:
            note = f"launchd: {', '.join(labels)}"
            if has_cron:
                note += " (plus a legacy cron entry — run scheduler.sh weekly to clean up)"
            check("Schedule", note)
        elif has_cron:
            check("Schedule", "legacy cron entry (run scheduler.sh weekly to migrate to launchd)")
        else:
            check("Schedule", "not scheduled (run scheduler.sh weekly)")
    except Exception:
        check("Schedule", "could not determine schedule")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cleaner.TestScheduler -v` → PASS.
Run: `python3 -m unittest discover -s tests` → 126 tests, OK.
Run: `bash scheduler.sh` (no args) → prints the launchd usage text and installs nothing.

- [ ] **Step 6: Commit**

```bash
git add scheduler.sh cleaner.py tests/test_cleaner.py
git commit -m "feat: launchd scheduling with automatic cron migration"
```

---

### Task 5: App notifications + Settings toggles

**Files:**
- Create: `app/Sources/NotificationManager.swift`
- Modify: `app/Sources/CleanerBridge.swift` — `EngineConfig` model, `loadSettings()`, new setters, post-clean notification
- Modify: `app/Sources/SettingsView.swift` — new Notifications section
- Modify: `app/Info.plist` — version 2.2.0

**Interfaces:**
- Consumes: engine config keys `notifications`, `low_disk_alerts`, `low_disk_threshold_gb` (Task 1); `config set KEY VALUE` (existing).
- Produces: `NotificationManager.shared.requestAuthorization()`, `NotificationManager.shared.post(title:body:)`; `bridge.notificationsEnabled`, `bridge.lowDiskAlertsEnabled`, `bridge.lowDiskThresholdGB`, `bridge.setNotifications(_:)`, `bridge.setLowDiskAlerts(_:)`, `bridge.setLowDiskThreshold(_:)`. Task 6 uses none of these.

There is no Swift test target in this project (a documented gap), so verification for Tasks 5 and 6 is: the app compiles via `bash app/build.sh`, plus the stated manual smoke checks.

- [ ] **Step 1: Create `app/Sources/NotificationManager.swift`**

```swift
import Foundation
import UserNotifications

/// Posts native notifications, degrading silently when unavailable.
///
/// An ad-hoc-signed app may fail to register with the notification centre, and
/// the user may simply deny permission. Neither is an error worth surfacing:
/// the app must never block or complain because a notification could not be
/// delivered. The CLI's osascript path covers the headless case.
final class NotificationManager {
    static let shared = NotificationManager()
    private var authorized = false

    private init() {}

    func requestAuthorization() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound]) { granted, _ in
                DispatchQueue.main.async { self.authorized = granted }
            }
    }

    func post(title: String, body: String) {
        guard authorized else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request, withCompletionHandler: nil)
    }
}
```

- [ ] **Step 2: Extend `EngineConfig` and settings state in `CleanerBridge.swift`**

Replace the `EngineConfig` struct (~line 87):

```swift
struct EngineConfig: Codable {
    var delete_mode: String?
    var notifications: Bool?
    var low_disk_alerts: Bool?
    var low_disk_threshold_gb: Double?
    var full_refresh_hours: Double?
}
```

Add to the `@Published` block (~line 129):

```swift
    @Published var notificationsEnabled = true
    @Published var lowDiskAlertsEnabled = true
    @Published var lowDiskThresholdGB: Double = 10
    @Published var fullRefreshHours: Double = 6
```

In `loadSettings()`, after `deleteMode = cfg.delete_mode ?? "rm"`:

```swift
            notificationsEnabled = cfg.notifications ?? true
            lowDiskAlertsEnabled = cfg.low_disk_alerts ?? true
            lowDiskThresholdGB = cfg.low_disk_threshold_gb ?? 10
            fullRefreshHours = cfg.full_refresh_hours ?? 6
```

Add setters next to `setDeleteMode`:

```swift
    func setNotifications(_ on: Bool) async {
        do {
            try await runPlain(["config", "set", "notifications", on ? "true" : "false"])
            notificationsEnabled = on
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setLowDiskAlerts(_ on: Bool) async {
        do {
            try await runPlain(["config", "set", "low_disk_alerts", on ? "true" : "false"])
            lowDiskAlertsEnabled = on
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }

    func setLowDiskThreshold(_ gb: Double) async {
        do {
            try await runPlain(["config", "set", "low_disk_threshold_gb",
                                String(format: "%g", gb)])
            lowDiskThresholdGB = gb
        } catch {
            statusMessage = "Config change failed: \(error.localizedDescription)"
        }
    }
```

- [ ] **Step 3: Notify after an in-app clean.** In `CleanerBridge`, at the end of both `clean(ids:)` and `autoCleanSafe()` — after `lastClean` is assigned on success — add:

```swift
            if notificationsEnabled, let result = lastClean {
                NotificationManager.shared.post(
                    title: "MacCleaner freed \(result.freed_human)",
                    body: "\(result.items.filter { $0.status != "skipped" }.count) items cleaned")
            }
```

- [ ] **Step 4: Request authorization at launch.** In `app/Sources/MacCleanerApp.swift`, add an `init()` to `MacCleanerApp`:

```swift
    init() {
        NotificationManager.shared.requestAuthorization()
    }
```

- [ ] **Step 5: Add the Settings section.** In `SettingsView.swift`, insert a new `Section` before the "About" section:

```swift
            Section {
                Toggle(isOn: Binding(
                    get: { bridge.notificationsEnabled },
                    set: { on in Task { await bridge.setNotifications(on) } }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Notify when a clean finishes")
                        Text("Includes scheduled cleans run in the background")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                Toggle(isOn: Binding(
                    get: { bridge.lowDiskAlertsEnabled },
                    set: { on in Task { await bridge.setLowDiskAlerts(on) } }
                )) {
                    Text("Warn when disk space is low")
                }

                if bridge.lowDiskAlertsEnabled {
                    LabeledContent("Warn below") {
                        Stepper("\(Int(bridge.lowDiskThresholdGB)) GB",
                                value: Binding(
                                    get: { bridge.lowDiskThresholdGB },
                                    set: { gb in Task { await bridge.setLowDiskThreshold(gb) } }
                                ),
                                in: 1...500, step: 1)
                    }
                }
            } header: {
                Text("Notifications")
            }
```

- [ ] **Step 6: Bump `app/Info.plist`** — both `CFBundleShortVersionString` and `CFBundleVersion` to `2.2.0`.

- [ ] **Step 7: Verify the build**

Run: `bash app/build.sh`
Expected: compiles with no errors, producing `build/MacCleaner.app`.
Run: `python3 -m unittest discover -s tests` → 126 tests, OK (unchanged — no Python touched).

Manual smoke (state what you observed): launch `build/MacCleaner.app`, open Settings, confirm the three new controls render and that toggling one persists — verify with `python3 cleaner.py config show | grep -E 'notifications|low_disk'`.

- [ ] **Step 8: Commit**

```bash
git add app/Sources/NotificationManager.swift app/Sources/CleanerBridge.swift app/Sources/SettingsView.swift app/Sources/MacCleanerApp.swift app/Info.plist
git commit -m "feat: app notifications and notification settings"
```

---

### Task 6: Split-cadence auto-refresh + "last cleaned"

**Files:**
- Modify: `app/Sources/CleanerBridge.swift` — timers, `lastCleanedAt`, `lightRefresh()`, wake observer
- Modify: `app/Sources/MacCleanerApp.swift` — "Last cleaned" menu row, menu-open refresh

**Interfaces:**
- Consumes: `report --json` (returns `runs` and `disk_history.current.free_bytes`), `scan --json`, `bridge.fullRefreshHours` (Task 5), existing `HistoryRun`/`HistoryReport`, `isCleaning`.
- Produces: `bridge.lastCleanedAt: Date?`, `bridge.freeBytes: Int?`, `bridge.startAutoRefresh()`, `bridge.lightRefresh()`, `bridge.fullRefreshIfStale()`.

**Why split cadence:** a full `scan` fans out `du` across 70+ targets (measured: over a minute on a populated machine), while reading free space is instant. A single short-interval timer running full scans would grind the disk.

- [ ] **Step 1: Add a `disk_history` model to `CleanerBridge.swift`.** Extend the history models (~line 72):

```swift
struct DiskCurrent: Codable {
    let free_bytes: Int
    let total_bytes: Int
}

struct DiskHistory: Codable {
    let current: DiskCurrent
}

struct HistoryReport: Codable {
    let runs: [HistoryRun]
    let disk_history: DiskHistory?
}
```

`disk_history` is optional so the app still decodes output from an older engine.

- [ ] **Step 2: Add refresh state and timers.** Add to the `@Published` block:

```swift
    @Published var lastCleanedAt: Date?
    @Published var freeBytes: Int?
```

Add private timer state to `CleanerBridge`:

```swift
    private var lightTimer: Timer?
    private var fullTimer: Timer?
    private var lastFullScan: Date?
    private var wakeObserver: NSObjectProtocol?
```

Add the refresh methods:

```swift
    // ── Auto-refresh ───────────────────────────────────────────────────────────
    //
    // Two cadences on purpose: `report --json` is a couple of file reads and one
    // stat, so it can run every minute; a full `scan` shells out to `du` for
    // 70+ targets and must not.

    func startAutoRefresh() {
        lightTimer?.invalidate()
        lightTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.lightRefresh() }
        }
        scheduleFullTimer()
        if wakeObserver == nil {
            wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
                forName: NSWorkspace.didWakeNotification, object: nil, queue: .main
            ) { [weak self] _ in
                Task { @MainActor in
                    await self?.lightRefresh()
                    await self?.fullRefreshIfStale()
                }
            }
        }
        Task { await lightRefresh() }
    }

    private func scheduleFullTimer() {
        fullTimer?.invalidate()
        let interval = max(3600, fullRefreshHours * 3600)
        fullTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.fullRefreshIfStale() }
        }
    }

    /// Cheap: free space and last-cleaned only. Never runs during a clean.
    func lightRefresh() async {
        guard !isCleaning else { return }
        guard let report = try? await run(HistoryReport.self, ["report", "--json", "-n", "1"])
        else { return }
        freeBytes = report.disk_history?.current.free_bytes
        lastCleanedAt = report.runs.last.flatMap { Self.parseTimestamp($0.timestamp) }
    }

    /// Full scan, debounced so a wake plus a menu-open doesn't launch two.
    func fullRefreshIfStale() async {
        guard !isCleaning, !isBusy else { return }
        let interval = max(3600, fullRefreshHours * 3600)
        if let last = lastFullScan, Date().timeIntervalSince(last) < interval { return }
        lastFullScan = Date()
        await scan()
    }

    nonisolated static func parseTimestamp(_ raw: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = formatter.date(from: raw) { return d }
        formatter.formatOptions = [.withInternetDateTime]
        if let d = formatter.date(from: raw) { return d }
        // The engine writes datetime.isoformat(), which has no timezone suffix.
        let fallback = DateFormatter()
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"
        if let d = fallback.date(from: raw) { return d }
        fallback.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return fallback.date(from: raw)
    }
```

Also set `lastFullScan = Date()` at the end of a successful `scan()` so the debounce accounts for scans triggered elsewhere.

- [ ] **Step 3: Wire it into the app.** In `MacCleanerApp.swift`:

Start refresh when the app launches — extend `MainView`'s `.task`:

```swift
        .task {
            bridge.startAutoRefresh()
            if bridge.report == nil {
                await bridge.scan()
            }
        }
```

Add the "Last cleaned" row and a menu-open refresh to `MenuBarContent`:

```swift
    var body: some View {
        if let report = bridge.report {
            Text("Reclaimable: \(report.total_reclaimable_human)")
        }
        if let free = bridge.freeBytes {
            Text("Free disk: \(ByteCountFormatter.string(fromByteCount: Int64(free), countStyle: .file))")
        } else if let stats = bridge.report?.disk_stats {
            Text("Free disk: \(ByteCountFormatter.string(fromByteCount: Int64(stats.free_bytes), countStyle: .file))")
        }
        Text("Last cleaned: \(Self.lastCleanedText(bridge.lastCleanedAt))")
        Divider()
```

…keeping the existing Scan / Auto-Clean / Open / Quit buttons below unchanged, and add the helper to `MenuBarContent`:

```swift
    static func lastCleanedText(_ date: Date?) -> String {
        guard let date else { return "Never" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: date, relativeTo: Date())
    }
```

Trigger a refresh when the menu opens, by adding to the `MenuBarExtra`'s content in `MacCleanerApp.body`:

```swift
        MenuBarExtra {
            MenuBarContent()
                .environmentObject(bridge)
                .task {
                    await bridge.lightRefresh()
                    await bridge.fullRefreshIfStale()
                }
        } label: {
```

- [ ] **Step 4: Verify the build**

Run: `bash app/build.sh`
Expected: compiles cleanly.
Run: `python3 -m unittest discover -s tests` → 126 tests, OK.

Manual smoke (report what you observed): launch `build/MacCleaner.app`; the menu shows "Last cleaned: …" (or "Never" on a fresh state file) and a free-disk figure. Confirm the light tick is cheap — after the app has been open a few minutes, `ps` should show no lingering `du` processes, and Activity Monitor should show no repeated CPU spikes on the 60-second boundary.

- [ ] **Step 5: Commit**

```bash
git add app/Sources/CleanerBridge.swift app/Sources/MacCleanerApp.swift
git commit -m "feat: split-cadence auto-refresh and last-cleaned in the menu bar"
```

---

### Task 7: Version bump + documentation

**Files:**
- Modify: `cleaner.py:105` (VERSION), `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`

- [ ] **Step 1: Bump the engine version** — `cleaner.py`: `VERSION = "2.2.0"`.

- [ ] **Step 2: Update `AGENTS.md`** (additive contract documentation, matching the file's existing style):
  - `disk-check` — purpose (cheap, for launchd), `--json` shape (`free_bytes`, `free_human`, `threshold_bytes`, `below_threshold`, `notified`), **always exits 0**, records no snapshot and no `report.log` entry.
  - `clean --notify` — posts a notification after the run; honours `notifications`; `--dry-run --notify` posts nothing.
  - The four new config keys with defaults, and the note that `notifications` and `low_disk_alerts` are independent.
  - `MACCLEANER_ALERTS` env var and `alerts.json` (same location rule as `report.log`/`snapshots.log`).
  - Scheduling is launchd (`com.fullex.maccleaner.clean`, `com.fullex.maccleaner.diskwatch`); cron is legacy and auto-migrated.

- [ ] **Step 3: Update `CLAUDE.md`**: add `disk-check` to the CLI command list; the four config keys; `MACCLEANER_ALERTS` under env vars; `alerts.json` alongside the `report.log`/`snapshots.log` location rule; launchd instead of cron in the Install & Schedule section (`scheduler.sh weekly|monthly|remove|status`, auto-migration); the new `app/Sources/NotificationManager.swift` in the app structure list; updated test count (126).

- [ ] **Step 4: Update `README.md`**: brief user-facing lines for scheduled-clean notifications, low-disk alerts (10 GB default, configurable), the live menu bar with "last cleaned", and that scheduling now uses launchd (existing cron users are migrated automatically). Match the file's existing tone and keep it short.

- [ ] **Step 5: Update `ROADMAP.md`**: tick **Notifications**, **launchd instead of cron**, **Low disk alerts**, **Auto-refresh**, and **Last cleaned timestamp**. Leave the Distribution items (Sparkle, Homebrew Cask, signing) for sub-project 3.

- [ ] **Step 6: Add a `CHANGELOG.md` entry** at the top, following the file's existing format:

```markdown
## [2.2.0] — Unreleased

### Added
- launchd scheduling replaces cron — a clean whose scheduled time passed while the Mac was asleep now runs on wake instead of being skipped. Existing cron schedules migrate automatically on the next `scheduler.sh` run
- Notifications when a scheduled clean finishes (`clean --notify`, used by the launchd agent), and in-app notifications after a clean
- `disk-check` — a cheap hourly low-disk watch installed alongside any schedule; warns below `low_disk_threshold_gb` (default 10 GB), throttled to at most one warning per day
- Live menu bar — free disk and "last cleaned" refresh every minute; the full reclaimable scan runs on a long interval (`full_refresh_hours`, default 6) plus on wake and when the menu opens
- Settings toggles for notifications, low-disk alerts, and the threshold

### Changed
- `doctor`'s Schedule check reports launchd agents, and flags a legacy cron entry
```

- [ ] **Step 7: Verify and commit**

Run: `python3 -m unittest discover -s tests` → 126 tests, OK.
Run: `python3 cleaner.py --version` → `MacCleaner 2.2.0`.

```bash
git add cleaner.py AGENTS.md CLAUDE.md README.md ROADMAP.md CHANGELOG.md
git commit -m "docs: v2.2.0 — launchd, notifications, low-disk watch"
```

---

### Task 8: End-to-end verification

**Files:** none modified (fixes only if something fails).

- [ ] **Step 1: Full suite** — `python3 -m unittest discover -s tests -v` → 126 tests, OK, output pristine.

- [ ] **Step 2: Engine smoke (safe, read-only commands only)**

```bash
python3 cleaner.py --version
python3 cleaner.py disk-check --json | python3 -c "import json,sys; d=json.load(sys.stdin); print('free', d['free_human'], '· below:', d['below_threshold'], '· notified:', d['notified'])"
python3 cleaner.py disk-check
python3 cleaner.py clean --dry-run --notify --json | python3 -c "import json,sys; d=json.load(sys.stdin); print('dry_run:', d['dry_run'], '— must not have notified')"
python3 cleaner.py doctor --json | python3 -c "import json,sys; d=json.load(sys.stdin); print([c['status'] for c in d['checks'] if c['name']=='Schedule'])"
python3 cleaner.py config show | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in ['notifications','low_disk_alerts','low_disk_threshold_gb','full_refresh_hours']})"
```

Expected: version 2.2.0; `disk-check` exits 0 and reports real numbers (`notified: false` on a healthy disk); the dry run posts nothing; `doctor` reports a Schedule status; all four config keys present with defaults.

- [ ] **Step 2b: Confirm `disk-check` really is cheap** — `time python3 cleaner.py disk-check --json`. Expected: well under a second (it runs hourly; anything slower means it is measuring something it shouldn't).

- [ ] **Step 3: Legacy contract** — `python3 cleaner.py --json | head -3` still emits scan JSON (the v1 menu bar app contract), and `python3 cleaner.py preview` renders the scan table.

- [ ] **Step 4: App build** — `bash app/build.sh` compiles, and `grep -c '2.2.0' build/MacCleaner.app/Contents/Info.plist` returns 2.

- [ ] **Step 5: Scheduler dry check — fully sandboxed.**

**`MACCLEANER_LAUNCH_AGENTS_DIR` alone is not a sandbox.** It redirects only where plists are written; the crontab and `launchctl` calls are still real. `weekly`/`monthly` auto-migrate a legacy cron line, so running either with just that variable set would strip the user's real cron entry and bootstrap agents from a directory that is about to be deleted. **This machine has a live MacCleaner cron line.** Stub `crontab` and `launchctl` on `PATH` as well:

```bash
d=$(mktemp -d); b="$d/bin"; mkdir -p "$b" "$d/agents"
printf '#!/bin/sh\nexit 0\n' > "$b/launchctl"; printf '#!/bin/sh\nexit 0\n' > "$b/crontab"
chmod +x "$b/launchctl" "$b/crontab"
PATH="$b:$PATH" MACCLEANER_LAUNCH_AGENTS_DIR="$d/agents" bash scheduler.sh weekly
plutil -lint "$d"/agents/*.plist
```

Expected: `weekly` reports the schedule and exits 0; both plists report OK. `status` is read-only and safe to run with only the agents-dir override, but keep the stubs anyway — there is no reason to point a real `launchctl` at a temp-dir plist.

Then confirm the real environment was untouched:

```bash
crontab -l 2>/dev/null | grep -c cleaner.py
ls ~/Library/LaunchAgents/com.fullex.maccleaner.* 2>/dev/null || echo "no real agents installed"
```

Expected: the cron count is whatever it was before this step (do not "fix" it here — migrating the maintainer's real schedule is their call, not a verification side effect), and no real agents were installed.

- [ ] **Step 6: Check for stray runtime files** — `git status --porcelain` shows no untracked runtime artifacts. If `alerts.json` appears at the repo root (created by the smoke runs above), add it to `.gitignore` beside `report.log` and `snapshots.log`, along with its atomic-write temp pattern `.alerts.json.*.tmp`, and commit.

- [ ] **Step 7: Commit any fixes** from smoke testing, message format `fix: <what>`.

---

## Self-Review Notes

- **Spec coverage:** §1 config keys + `_notify` → Task 1; `clean --notify` → Task 2; `disk-check` + throttle + `alerts.json` → Task 3; §2 launchd + migration + doctor → Task 4; §3 NotificationManager + Settings + Info.plist → Task 5, refresh cadence + last-cleaned → Task 6; §4 error handling → distributed across Tasks 1–4 (degradation is asserted in their tests); §5 testing → each task's tests plus Task 8; §6 docs → Task 7.
- **Test-count checkpoints** (89 → 95 → 99 → 112 → 126) assume no test is split or merged; treat them as expectations. The gate is `OK` with zero failures.
- **Type consistency:** `_notify_argv(title, message) -> list`, `_notify(title, message) -> bool`, `_low_disk_decision(alerts, now, free_bytes, threshold_bytes) -> (bool, dict)`, `run_disk_check(config, json_mode=False) -> dict`, `load_alerts() -> dict`, `save_alerts(alerts) -> None`, `run_clean(..., notify=False)`; Swift: `NotificationManager.shared.post(title:body:)`, `bridge.lightRefresh()`, `bridge.fullRefreshIfStale()`, `bridge.startAutoRefresh()`, `CleanerBridge.parseTimestamp(_:)` — used with these exact names in every task above.
- **Known gap carried from the spec:** no Swift test target, so Tasks 5 and 6 are verified by compilation plus the stated manual smoke checks. Called out in Task 5 Step 7 and Task 6 Step 4.
- **Sandbox discipline:** every new test either stubs `osascript`/`launchctl`/`crontab` on `PATH` or points `MACCLEANER_LAUNCH_AGENTS_DIR` at a tempdir, so no test posts a real notification or touches the real crontab or agents directory.
