# App Experience v2.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scheduling becomes an engine subcommand (`schedule status|weekly|monthly|off`) that the app's Settings can drive, and the Dashboard draws a free-space trend chart from the snapshot data — the two biggest gaps for a GUI-first user.

**Architecture:** Port `scheduler.sh`'s logic into `cleaner.py` using `plistlib` (stdlib — structurally valid plists, no XML-escaping hazards, and trivial plist *parsing* for `status`); `scheduler.sh` becomes a thin delegating wrapper so every existing invocation keeps working; `doctor` reuses the same state helpers. The app gains a Schedule section in Settings and a Swift Charts trend view on the Dashboard, both pure JSON clients.

**Tech Stack:** Python 3 stdlib (`plistlib`, `subprocess`), bash wrapper, SwiftUI + Swift Charts (system framework, macOS 13+ — the app's existing floor).

**Spec:** `docs/superpowers/specs/2026-08-03-app-experience-v2.3-design.md`

## Global Constraints

- `cleaner.py` stays a single file, Python 3 **stdlib only**. The app gains **no** plist, launchctl, or cron logic — it calls `schedule …` and renders JSON.
- JSON contract **additive only**. JSON → stdout, human messages → stderr… with one deliberate exception carried from `scheduler.sh`: in non-JSON mode the schedule commands print their human ✅/⚠️ output to stdout/stderr exactly as the bash version did, because the existing `TestScheduler` assertions (which keep running, against the wrapper) check stdout.
- Behavior preservation is a hard requirement: same agent labels, plist semantics, PATH embedding, cron-migration rules (anchored `mac-cleaner/cleaner.py` marker, unrelated lines spared, echo what's removed, explicit cadence wins), launchctl fallback chain (`bootout`→`bootstrap`, fallback `unload`/`load`), failure propagation (a failed load ⇒ exit 1, no success banner, plist still written).
- **Tests never touch the real machine**: stub `launchctl` and `crontab` on `PATH`, tempdir `MACCLEANER_LAUNCH_AGENTS_DIR`, tempdir `HOME` for subprocess tests. The maintainer's Mac now has REAL loaded MacCleaner agents and a real schedule — an unsandboxed `schedule weekly|off` would mutate it. `schedule status` is read-only and safe.
- Deletion logic (`_safe_to_delete`, `_remove`, `delete_target`) untouched.
- Baseline: 150 tests passing on `main`-derived branch `feat/app-experience-v2.3`. Zero failures is the gate at every task.
- The committed `MacCleaner.app` bundle must be rebuilt in the same task that changes `app/Sources/` version/docs (Task 5) — never ship a stale bundle.
- No Swift test target exists: Swift work is verified by `bash app/build.sh` compiling with zero new warnings plus the maintainer's eyes (documented manual checks).

## File Structure

- Modify: `cleaner.py` — schedule section (constants, plist builders, state readers, `run_schedule_*`), parser, `main()`, `doctor` unification
- Modify: `scheduler.sh` — becomes a ~40-line delegating wrapper
- Modify: `tests/test_cleaner.py` — new `TestScheduleSubcommand` class; existing `TestScheduler` kept nearly intact as the wrapper-compat proof
- Modify: `app/Sources/CleanerBridge.swift` — `ScheduleStatus` models, `loadSchedule()`/`setSchedule(_:)`, `diskSnapshots` published from the light tick
- Create: `app/Sources/DiskTrendView.swift` — the chart, one responsibility
- Modify: `app/Sources/SettingsView.swift`, `app/Sources/DashboardView.swift`
- Modify (Task 5): `app/Info.plist`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`, committed `MacCleaner.app`

---

### Task 1: Engine `schedule` subcommand

**Files:**
- Modify: `cleaner.py` — new section after the low-disk alerts section; `build_parser()`; `main()`
- Test: `tests/test_cleaner.py` (new class `TestScheduleSubcommand`)

**Interfaces:**
- Consumes: existing `_launchd_is_loaded(label) -> bool` (cleaner.py:1330), `LOG_PATH`, `VERSION`, `HOME`.
- Produces (Tasks 2–3 rely on these): `LAUNCH_AGENTS_DIR: Path` (honours `MACCLEANER_LAUNCH_AGENTS_DIR`), `CLEAN_LABEL`/`WATCH_LABEL`/`CRON_MARKER`/`AGENT_PATH` constants, `_schedule_state() -> dict` (`{"schedule", "agents", "legacy_cron"}`), `run_schedule_status(json_mode) -> dict`, `run_schedule_install(kind, json_mode) -> bool`, `run_schedule_off(json_mode) -> None`, and the CLI surface `schedule status|weekly|monthly|off [--json]`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cleaner.py`. The sandbox mirrors `TestScheduler`'s (stub `launchctl`/`crontab` on PATH, tempdir agents dir), with one addition: a `loaded` marker file controls whether the stub's `launchctl list` reports loaded, so the present-but-not-loaded state is constructible.

```python
class TestScheduleSubcommand(unittest.TestCase):
    """`cleaner.py schedule ...` against a fully sandboxed environment.
    Never touches the real crontab, agents, or launchd."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.crontab_file = self.tmp / "crontab.txt"
        self.crontab_file.write_text("")
        self.loaded_flag = self.tmp / "loaded"   # exists => launchctl list succeeds
        self.loaded_flag.write_text("")
        launchctl = self.bindir / "launchctl"
        launchctl.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "list" ]; then [ -e "$LOADED_FLAG" ] && exit 0 || exit 1; fi\n'
            'exit 0\n')
        launchctl.chmod(0o755)
        crontab = self.bindir / "crontab"
        crontab.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
            'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > "$CRONTAB_FILE"; fi\n'
            'exit 0\n')
        crontab.chmod(0o755)
        self.env = {**os.environ,
                    "PATH": f"{self.bindir}:{os.environ['PATH']}",
                    "HOME": str(self.tmp),
                    "MACCLEANER_LAUNCH_AGENTS_DIR": str(self.agents),
                    "MACCLEANER_CONFIG": str(self.tmp / "config.json"),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log"),
                    "MACCLEANER_ALERTS": str(self.tmp / "alerts.json"),
                    "CRONTAB_FILE": str(self.crontab_file),
                    "LOADED_FLAG": str(self.loaded_flag)}
        (self.tmp / "config.json").write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def status_json(self):
        r = self.run_cli("schedule", "status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def plist(self, label):
        return self.agents / f"com.fullex.maccleaner.{label}.plist"

    # ── status shapes ──────────────────────────────────────────────────────

    def test_status_empty(self):
        d = self.status_json()
        self.assertIsNone(d["schedule"])
        self.assertEqual(d["agents"], [])
        self.assertFalse(d["legacy_cron"])

    def test_status_after_weekly_install(self):
        r = self.run_cli("schedule", "weekly", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.status_json()
        self.assertEqual(d["schedule"], "weekly")
        labels = {a["label"]: a for a in d["agents"]}
        self.assertEqual(set(labels), {"com.fullex.maccleaner.clean",
                                       "com.fullex.maccleaner.diskwatch"})
        for a in labels.values():
            self.assertTrue(a["plist_present"])
            self.assertTrue(a["loaded"])

    def test_status_monthly(self):
        self.run_cli("schedule", "monthly", "--json")
        self.assertEqual(self.status_json()["schedule"], "monthly")

    def test_status_present_but_not_loaded(self):
        self.run_cli("schedule", "weekly", "--json")
        self.loaded_flag.unlink()          # launchctl list now exits 1
        d = self.status_json()
        self.assertEqual(d["schedule"], "weekly")
        for a in d["agents"]:
            self.assertTrue(a["plist_present"])
            self.assertFalse(a["loaded"])

    def test_status_reports_legacy_cron(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        d = self.status_json()
        self.assertTrue(d["legacy_cron"])
        # status is read-only: the cron line must survive
        self.assertIn("mac-cleaner/cleaner.py", self.crontab_file.read_text())
        self.assertEqual(list(self.agents.glob("*.plist")), [])

    # ── install ────────────────────────────────────────────────────────────

    def test_install_writes_valid_plists(self):
        self.run_cli("schedule", "weekly", "--json")
        for label in ("clean", "diskwatch"):
            lint = subprocess.run(["plutil", "-lint", str(self.plist(label))],
                                  capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_clean_plist_content(self):
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        with open(self.plist("clean"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["Label"], "com.fullex.maccleaner.clean")
        self.assertIn("--notify", p["ProgramArguments"])
        self.assertEqual(p["StartCalendarInterval"]["Weekday"], 1)
        self.assertEqual(p["StartCalendarInterval"]["Hour"], 9)
        self.assertIn("/opt/homebrew/bin", p["EnvironmentVariables"]["PATH"])
        self.assertTrue(p["ProgramArguments"][0])   # interpreter path non-empty
        self.assertIn("cleaner.py", p["ProgramArguments"][1])

    def test_monthly_uses_day_not_weekday(self):
        self.run_cli("schedule", "monthly", "--json")
        import plistlib
        with open(self.plist("clean"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["StartCalendarInterval"]["Day"], 1)
        self.assertNotIn("Weekday", p["StartCalendarInterval"])

    def test_diskwatch_plist_content(self):
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        with open(self.plist("diskwatch"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["StartInterval"], 3600)
        self.assertIn("disk-check", p["ProgramArguments"])

    def test_reinstall_replaces(self):
        self.run_cli("schedule", "weekly", "--json")
        self.run_cli("schedule", "monthly", "--json")
        self.assertEqual(self.status_json()["schedule"], "monthly")
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)

    def test_install_json_shape(self):
        r = self.run_cli("schedule", "weekly", "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["schedule"], "weekly")
        self.assertFalse(d["migrated_cron"])

    # ── cron migration ─────────────────────────────────────────────────────

    def test_install_migrates_cron(self):
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "0 3 * * * /Users/x/bin/db-cleaner.py\n")
        r = self.run_cli("schedule", "weekly", "--json")
        d = json.loads(r.stdout)
        self.assertTrue(d["migrated_cron"])
        self.assertEqual(d["schedule"], "weekly",
                         "explicitly requested cadence wins over the detected one")
        remaining = self.crontab_file.read_text()
        self.assertNotIn("mac-cleaner/cleaner.py", remaining)
        self.assertIn("db-cleaner.py", remaining, "unrelated cron lines survive")

    def test_migration_reports_detected_cadence_on_stderr(self):
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.run_cli("schedule", "weekly", "--json")
        self.assertIn("monthly", r.stderr, "detected cadence is reported for visibility")

    # ── off ────────────────────────────────────────────────────────────────

    def test_off_removes_everything(self):
        self.run_cli("schedule", "weekly", "--json")
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.run_cli("schedule", "off", "--json")
        self.assertEqual(r.returncode, 0)
        d = json.loads(r.stdout)
        self.assertTrue(d["removed"])
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        self.assertNotIn("mac-cleaner", self.crontab_file.read_text())

    def test_off_when_nothing_installed_is_success(self):
        r = self.run_cli("schedule", "off", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(json.loads(r.stdout)["removed"])

    # ── failure propagation ────────────────────────────────────────────────

    def test_failed_load_exits_1_but_writes_plists(self):
        bad = self.bindir / "launchctl"
        bad.write_text('#!/bin/sh\necho "Load failed: 5: I/O error" >&2\nexit 1\n')
        r = self.run_cli("schedule", "weekly")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("✅ Scheduled", r.stdout)
        self.assertIn("I/O error", r.stderr, "real launchctl stderr surfaces")
        self.assertTrue(self.plist("clean").exists(),
                        "plist still written so manual loading works")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cleaner.TestScheduleSubcommand -v`
Expected: every test errors with returncode 2 (`invalid choice: 'schedule'`).

- [ ] **Step 3: Implement.** New section in `cleaner.py` after the low-disk alerts section, plus parser/main wiring. `import plistlib` joins the stdlib imports at the top.

```python
# ── Scheduling (launchd) ───────────────────────────────────────────────────────
# Port of scheduler.sh (which is now a thin wrapper over this). launchd rather
# than cron: launchd runs a missed calendar job after the Mac wakes; cron
# silently skips it.
LAUNCH_AGENTS_DIR = Path(os.environ.get("MACCLEANER_LAUNCH_AGENTS_DIR",
                                        HOME / "Library/LaunchAgents"))
CLEAN_LABEL = "com.fullex.maccleaner.clean"
WATCH_LABEL = "com.fullex.maccleaner.diskwatch"
# A cron line belongs to MacCleaner only if it references the canonical
# install path — an unanchored "cleaner.py" match would catch a user's own
# db-cleaner.py job.
CRON_MARKER = "mac-cleaner/cleaner.py"
# Homebrew tools aren't on launchd's minimal default PATH; same list
# CleanerBridge.runEngine uses, so cmd targets behave identically under the
# app and under a scheduled agent.
AGENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
CRON_LOG_PATH = LOG_PATH.parent / "cron.log"   # beside report.log wherever that lives


def _agent_plist(label, program_args, trigger):
    """Plist dict for one agent. `trigger` is e.g.
    {"StartInterval": 3600} or {"StartCalendarInterval": {...}}."""
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "EnvironmentVariables": {"PATH": AGENT_PATH},
        **trigger,
        "StandardOutPath": str(CRON_LOG_PATH),
        "StandardErrorPath": str(CRON_LOG_PATH),
    }


def _write_agent_plist(label, plist):
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LAUNCH_AGENTS_DIR / f"{label}.plist", "wb") as f:
        plistlib.dump(plist, f)


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=15)


def _bootstrap_agent(label):
    """bootout → bootstrap, falling back to unload/load for older macOS.
    Returns (ok, error_message). The plist is already on disk either way."""
    plist = str(LAUNCH_AGENTS_DIR / f"{label}.plist")
    uid = os.getuid()
    try:
        _launchctl("bootout", f"gui/{uid}/{label}")
        r = _launchctl("bootstrap", f"gui/{uid}", plist)
        if r.returncode == 0:
            return True, None
        _launchctl("unload", plist)
        r2 = _launchctl("load", plist)
        if r2.returncode == 0:
            return True, None
        err = (r2.stderr or r.stderr or "").strip()
        return False, err
    except Exception as e:
        return False, str(e)


def _unload_agent(label):
    plist = LAUNCH_AGENTS_DIR / f"{label}.plist"
    try:
        uid = os.getuid()
        _launchctl("bootout", f"gui/{uid}/{label}")
        _launchctl("unload", str(plist))
    except Exception:
        pass
    existed = plist.exists()
    plist.unlink(missing_ok=True)
    return existed


def _read_crontab():
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _strip_legacy_cron(say):
    """Remove any MacCleaner cron line, echoing it. Returns True if one was
    removed. Reports (not uses) the line's own cadence — the caller installs
    whatever the user actually asked for."""
    existing = _read_crontab()
    lines = [l for l in existing.splitlines() if CRON_MARKER in l]
    if not lines:
        return False
    detected = "weekly"
    fields = lines[0].split()
    if len(fields) >= 3 and fields[2] != "*":
        detected = "monthly"
    say(f"→ Found a legacy cron schedule (looked {detected}) — migrating to launchd and removing it:")
    for l in lines:
        say(f"    {l}")
    kept = "\n".join(l for l in existing.splitlines() if CRON_MARKER not in l)
    try:
        subprocess.run(["crontab", "-"], input=kept + ("\n" if kept else ""),
                       capture_output=True, text=True, timeout=10)
    except Exception as e:
        say(f"⚠️  Could not rewrite crontab: {e}")
        return False
    return True


def _schedule_state():
    """One source of truth for status/doctor: what's installed and loaded."""
    agents = []
    schedule = None
    for label in (CLEAN_LABEL, WATCH_LABEL):
        plist_path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        present = plist_path.exists()
        agents.append({"label": label,
                       "plist_present": present,
                       "loaded": _launchd_is_loaded(label) if present else False})
        if label == CLEAN_LABEL and present:
            try:
                with open(plist_path, "rb") as f:
                    cal = plistlib.load(f).get("StartCalendarInterval", {})
                schedule = "monthly" if "Day" in cal else "weekly" if "Weekday" in cal else None
            except Exception:
                schedule = None
    agents = [a for a in agents if a["plist_present"]]
    return {"schedule": schedule, "agents": agents,
            "legacy_cron": CRON_MARKER in _read_crontab()}


def _print_schedule_status(state):
    print("── MacCleaner Scheduler Status ──")
    if not state["agents"]:
        print("❌ Not scheduled (run 'maccleaner schedule weekly')")
    for a in state["agents"]:
        if a["loaded"]:
            print(f"✅ {a['label']} (launchd)")
        else:
            print(f"⚠️  {a['label']} — plist present but not loaded "
                  f"(run 'maccleaner schedule weekly' to reload)")
    if state["legacy_cron"]:
        print("⚠️  A legacy cron entry is still present — run 'maccleaner schedule weekly' to migrate.")


def run_schedule_status(json_mode=False):
    state = _schedule_state()
    if json_mode:
        print(json.dumps({"version": VERSION, **state}, indent=2))
    else:
        _print_schedule_status(state)
    return state


def run_schedule_install(kind, json_mode=False):
    """Install/replace both agents. Returns True when both loaded."""
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print
    migrated = _strip_legacy_cron(say)

    engine = Path(__file__).resolve()
    trigger = ({"StartCalendarInterval": {"Day": 1, "Hour": 9, "Minute": 0}}
               if kind == "monthly" else
               {"StartCalendarInterval": {"Weekday": 1, "Hour": 9, "Minute": 0}})
    jobs = [
        (CLEAN_LABEL, [sys.executable, str(engine), "clean", "--yes", "--notify"], trigger),
        (WATCH_LABEL, [sys.executable, str(engine), "disk-check"], {"StartInterval": 3600}),
    ]
    ok = True
    for label, args, trig in jobs:
        _write_agent_plist(label, _agent_plist(label, args, trig))
        loaded, err = _bootstrap_agent(label)
        if not loaded:
            ok = False
            print(f"⚠️  Could not load {label} with launchctl.{' (' + err + ')' if err else ''}",
                  file=sys.stderr)
            print(f"    The plist is written to {LAUNCH_AGENTS_DIR / (label + '.plist')} — "
                  f"load it manually with:\n"
                  f"    launchctl bootstrap gui/{os.getuid()} \"{LAUNCH_AGENTS_DIR / (label + '.plist')}\"",
                  file=sys.stderr)

    if json_mode:
        print(json.dumps({"version": VERSION, **_schedule_state(),
                          "migrated_cron": migrated}, indent=2))
    elif ok:
        print("✅ Scheduled: 1st of every month at 9am (launchd)" if kind == "monthly"
              else "✅ Scheduled: every Monday at 9am (launchd)")
        print("   Low-disk check: hourly")
        print(f"   Log: {CRON_LOG_PATH}")
    if not ok:
        print("❌ Scheduling incomplete — see the warning(s) above.", file=sys.stderr)
    return ok


def run_schedule_off(json_mode=False):
    say = (lambda *a: print(*a, file=sys.stderr)) if json_mode else print
    existing = _read_crontab()
    if CRON_MARKER in existing:
        for l in existing.splitlines():
            if CRON_MARKER in l:
                say("   Removing legacy cron entry:")
                say(f"     {l}")
        kept = "\n".join(l for l in existing.splitlines() if CRON_MARKER not in l)
        try:
            subprocess.run(["crontab", "-"], input=kept + ("\n" if kept else ""),
                           capture_output=True, text=True, timeout=10)
        except Exception as e:
            say(f"⚠️  Could not rewrite crontab: {e}")
    removed_clean = _unload_agent(CLEAN_LABEL)
    removed_watch = _unload_agent(WATCH_LABEL)
    removed = removed_clean or removed_watch
    if json_mode:
        print(json.dumps({"version": VERSION, **_schedule_state(),
                          "removed": removed}, indent=2))
    else:
        print("✅ Removed MacCleaner launchd agents" if removed
              else "Nothing scheduled — nothing to remove.")
```

Parser — add before the `install-deps` parser in `build_parser()`:

```python
    p_sched = sub.add_parser("schedule",
                             help="Manage the launchd cleanup schedule (weekly/monthly/off/status)")
    p_sched.add_argument("action", choices=["status", "weekly", "monthly", "off"])
    p_sched.add_argument("--json", action="store_true", help="Machine-readable output")
```

`main()` — add a branch next to the other early-return commands:

```python
    if args.command == "schedule":
        if args.action == "status":
            run_schedule_status(json_mode=args.json)
        elif args.action in ("weekly", "monthly"):
            if not run_schedule_install(args.action, json_mode=args.json):
                sys.exit(1)
        else:
            run_schedule_off(json_mode=args.json)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_cleaner.TestScheduleSubcommand -v` → all PASS.
Run: `python3 -m unittest discover -s tests` → 166 tests, OK (150 + 16).

- [ ] **Step 5: Commit**

```bash
git add cleaner.py tests/test_cleaner.py
git commit -m "feat: schedule subcommand — launchd scheduling as engine logic"
```

---

### Task 2: `scheduler.sh` wrapper + `doctor` unification

**Files:**
- Modify: `scheduler.sh` (full replacement — thin wrapper)
- Modify: `cleaner.py` — `run_doctor()`'s Schedule check reuses `_schedule_state()`
- Test: `tests/test_cleaner.py` — the existing `TestScheduler` class is the compat proof; adjust only where the plan below says so

**Interfaces:**
- Consumes: `schedule status|weekly|monthly|off` (Task 1), `_schedule_state()`.
- Produces: unchanged `scheduler.sh weekly|monthly|remove|status` surface with pass-through exit codes.

- [ ] **Step 1: Replace `scheduler.sh`**

```bash
#!/bin/bash
# MacCleaner Scheduler — thin wrapper over `cleaner.py schedule ...`.
# The scheduling logic lives in the engine (single source of truth, and the
# app's Settings drives the same code). This wrapper keeps every documented
# invocation working: weekly | monthly | remove | status.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLEANER="$SCRIPT_DIR/cleaner.py"
PYTHON="$(command -v python3)"

if [ -z "$PYTHON" ]; then
    echo "⚠️  python3 not found on PATH — cannot manage the schedule." >&2
    exit 1
fi

case "${1:-}" in
    weekly|monthly) exec "$PYTHON" "$CLEANER" schedule "$1" ;;
    remove)         exec "$PYTHON" "$CLEANER" schedule off ;;
    status)         exec "$PYTHON" "$CLEANER" schedule status ;;
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
        echo "An existing cron schedule is migrated to launchd automatically"
        echo "when you pick weekly or monthly. (Same as: maccleaner schedule ...)"
        ;;
esac
```

- [ ] **Step 2: Reconcile `TestScheduler` with the wrapper.** The class keeps running `scheduler.sh` — that's the point. Required adjustments, and only these:
  - The stub `launchctl` in `TestScheduler.setUp` keys `list` handling off `CALLS_FILE` grep; replace that stub with the simpler Task-1-style stub (a `list` invocation exits 0 — the "loaded" default), since the engine now drives launchctl. Keep `CRONTAB_FILE` handling identical.
  - Tests asserting exact plist text like `<key>Weekday</key>` still pass (plistlib emits XML plists with those keys) — do not weaken them.
  - Tests asserting human strings (`✅ Scheduled: every Monday at 9am (launchd)`, `Found a legacy cron schedule (looked monthly)`, status lines) must pass **unchanged** — if one fails, fix the engine's message, not the test: the wrapper's output-compat is the requirement.
  - `test_usage_when_no_command` keeps passing against the new usage text (asserts `weekly`/`monthly` present).

- [ ] **Step 3: Unify `doctor`.** Replace the body of the Schedule check in `run_doctor()` (the `try:` block currently globbing `HOME/Library/LaunchAgents`) so it derives everything from `_schedule_state()` — same message wording as today, but sourced from the shared helper (and therefore honouring `MACCLEANER_LAUNCH_AGENTS_DIR`, which finally makes it sandbox-testable):

```python
    try:
        st = _schedule_state()
        loaded = [a["label"] for a in st["agents"] if a["loaded"]]
        not_loaded = [a["label"] for a in st["agents"] if not a["loaded"]]
        if loaded:
            note = f"launchd: {', '.join(loaded)}"
            if not_loaded:
                note += (f" (plist present but not loaded: {', '.join(not_loaded)}"
                         " — run scheduler.sh weekly to reload)")
            if st["legacy_cron"]:
                note += " (plus a legacy cron entry — run scheduler.sh weekly to clean up)"
            check("Schedule", note)
        elif not_loaded:
            check("Schedule",
                  f"plist present but not loaded: {', '.join(not_loaded)}"
                  " — run scheduler.sh weekly to reload", ok=False)
        elif st["legacy_cron"]:
            check("Schedule", "legacy cron entry (run scheduler.sh weekly to migrate to launchd)")
        else:
            check("Schedule", "not scheduled (run scheduler.sh weekly)")
    except Exception:
        check("Schedule", "could not determine schedule")
```

Add one test to `TestScheduleSubcommand` proving doctor now sees the sandboxed state:

```python
    def test_doctor_uses_sandboxed_schedule_state(self):
        self.run_cli("schedule", "weekly", "--json")
        r = self.run_cli("doctor", "--json")
        d = json.loads(r.stdout)
        sched = next(c for c in d["checks"] if c["name"] == "Schedule")
        self.assertIn("com.fullex.maccleaner.clean", sched["status"])
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover -s tests` → 167 tests, OK. Pay attention to `TestScheduler` — every green test there is wrapper-compat evidence.

- [ ] **Step 5: Commit**

```bash
git add scheduler.sh cleaner.py tests/test_cleaner.py
git commit -m "feat: scheduler.sh delegates to the engine; doctor shares schedule state"
```

---

### Task 3: Settings — Schedule section

**Files:**
- Modify: `app/Sources/CleanerBridge.swift` — models + bridge methods
- Modify: `app/Sources/SettingsView.swift` — new Section above Notifications

**Interfaces:**
- Consumes: `schedule status --json` → `{"version", "schedule": "weekly"|"monthly"|null, "agents": [...], "legacy_cron": bool}`; `schedule weekly|monthly|off`.
- Produces: `bridge.scheduleStatus: ScheduleStatus?`, `bridge.scheduleSupported: Bool`, `loadSchedule()`, `setSchedule(_ choice: String)` where choice ∈ "weekly"/"monthly"/"off".

- [ ] **Step 1: Models + bridge.** In `CleanerBridge.swift`, near the other report models:

```swift
struct AgentStatus: Codable, Identifiable {
    let label: String
    let plist_present: Bool
    let loaded: Bool
    var id: String { label }
}

struct ScheduleStatus: Codable {
    let schedule: String?
    let agents: [AgentStatus]
    let legacy_cron: Bool
}
```

In the `@Published` block:

```swift
    @Published var scheduleStatus: ScheduleStatus?
    @Published var scheduleSupported = true
```

Methods, following the `loadSettings`/`setDeleteMode` patterns:

```swift
    func loadSchedule() async {
        do {
            scheduleStatus = try await run(ScheduleStatus.self, ["schedule", "status", "--json"])
            scheduleSupported = true
        } catch {
            // An older engine exits 2 on the unknown subcommand; treat any
            // failure here as "can't manage scheduling", not an error banner.
            scheduleStatus = nil
            scheduleSupported = false
        }
    }

    func setSchedule(_ choice: String) async {
        do {
            try await runPlain(["schedule", choice])
            await loadSchedule()
        } catch {
            statusMessage = "Schedule change failed: \(error.localizedDescription)"
            await loadSchedule()   // revert the picker to the real state
        }
    }
```

- [ ] **Step 2: Settings section.** In `SettingsView.swift`, insert before the Notifications section:

```swift
            Section {
                if bridge.scheduleSupported {
                    Picker("Automatic cleanup", selection: Binding(
                        get: { bridge.scheduleStatus?.schedule ?? "off" },
                        set: { choice in Task { await bridge.setSchedule(choice) } }
                    )) {
                        Text("Off").tag("off")
                        Text("Weekly — Mondays at 9am").tag("weekly")
                        Text("Monthly — 1st at 9am").tag("monthly")
                    }
                    .pickerStyle(.radioGroup)

                    Text(scheduleCaption)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Update the MacCleaner CLI to manage scheduling here.")
                        .foregroundStyle(.secondary)
                }
            } header: {
                Text("Schedule")
            }
```

with the caption helper on `SettingsView`:

```swift
    private var scheduleCaption: String {
        guard let status = bridge.scheduleStatus else { return "" }
        if status.legacy_cron {
            return "A legacy cron schedule exists — choosing an option migrates it to launchd."
        }
        if status.schedule != nil {
            let allLoaded = status.agents.allSatisfy(\.loaded)
            return allLoaded
                ? "Active — cleans run in the background and notify when done. Low-disk check: hourly."
                : "Installed but not loaded — pick the schedule again to reload it."
        }
        return "No automatic cleanup. Scans and cleans only run when you start them."
    }
```

and extend the view's existing `.task` to also load the schedule:

```swift
        .task { await bridge.loadSettings(); await bridge.loadSchedule() }
```

- [ ] **Step 3: Build and verify**

Run: `bash app/build.sh` → compiles, zero new warnings.
Run: `python3 -m unittest discover -s tests` → 167 tests, OK (untouched).
Manual note for the report: the Settings section renders with three radio options (visual check is the maintainer's; state what you could not verify).

- [ ] **Step 4: Commit**

```bash
git add app/Sources/CleanerBridge.swift app/Sources/SettingsView.swift
git commit -m "feat: manage the cleanup schedule from Settings"
```

---

### Task 4: Dashboard — disk trend chart

**Files:**
- Create: `app/Sources/DiskTrendView.swift`
- Modify: `app/Sources/CleanerBridge.swift` — snapshot model + published data
- Modify: `app/Sources/DashboardView.swift` — mount the chart

**Interfaces:**
- Consumes: `report --json`'s `disk_history.snapshots` (already fetched by the 60s light tick), `bridge.lowDiskThresholdGB`, `CleanerBridge.parseTimestamp(_:)`.
- Produces: `DiskSnapshot` model, `bridge.diskSnapshots: [DiskSnapshot]`, `DiskTrendView`.

- [ ] **Step 1: Model + data flow.** In `CleanerBridge.swift`, extend the history models:

```swift
struct DiskSnapshot: Codable, Identifiable {
    let ts: String
    let disk_free_bytes: Int
    let disk_total_bytes: Int
    var id: String { ts }
}

struct DiskHistory: Codable {
    let current: DiskCurrent
    let snapshots: [DiskSnapshot]?
}
```

Add `@Published var diskSnapshots: [DiskSnapshot] = []` and set it inside `performLightRefresh()` where `freeBytes` is already assigned:

```swift
        diskSnapshots = report.disk_history?.snapshots ?? []
```

- [ ] **Step 2: Create `app/Sources/DiskTrendView.swift`**

```swift
import SwiftUI
import Charts

/// Free-space-over-time from the engine's daily snapshots, with the low-disk
/// threshold drawn as a rule line. Pure rendering: all data comes from
/// `report --json` via the bridge's light tick.
struct DiskTrendView: View {
    @EnvironmentObject var bridge: CleanerBridge

    private struct Point: Identifiable {
        let day: Date
        let freeGB: Double
        var id: Date { day }
    }

    private var points: [Point] {
        bridge.diskSnapshots.compactMap { snap in
            guard let day = CleanerBridge.parseTimestamp(snap.ts) else { return nil }
            return Point(day: day, freeGB: Double(snap.disk_free_bytes) / 1_073_741_824)
        }
    }

    private var thresholdGB: Double { bridge.lowDiskThresholdGB }

    var body: some View {
        Group {
            if points.count >= 2 {
                Chart {
                    ForEach(points) { p in
                        LineMark(x: .value("Day", p.day),
                                 y: .value("Free (GB)", p.freeGB))
                    }
                    RuleMark(y: .value("Low-disk warning", thresholdGB))
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                        .foregroundStyle(.orange)
                        .annotation(position: .top, alignment: .trailing) {
                            Text("Low-disk warning")
                                .font(.caption2)
                                .foregroundStyle(.orange)
                        }
                }
                .chartYScale(domain: 0...maxY)
                .chartYAxisLabel("Free (GB)")
            } else {
                Text("Disk trends appear after a couple of days of scans.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .center)
            }
        }
        .frame(height: 140)
    }

    private var maxY: Double {
        let peak = points.map(\.freeGB).max() ?? thresholdGB
        return max(peak, thresholdGB * 1.2)
    }
}
```

- [ ] **Step 3: Mount it.** In `DashboardView.swift`'s `body`, between `header` and the first `Divider()`:

```swift
            DiskTrendView()
                .padding(.horizontal)
                .padding(.bottom, 8)
```

(Adjust to `VStack` structure as found; the chart sits directly under the header block, above the target list.)

- [ ] **Step 4: Build and verify**

Run: `bash app/build.sh` → compiles, zero new warnings. If `import Charts` fails to autolink under plain `swiftc`, add `-framework Charts` in `app/build.sh`'s compile invocation and note it in the report.
Run: `python3 -m unittest discover -s tests` → 167 tests, OK.
Report what could not be verified visually (the rendered chart is the maintainer's check).

- [ ] **Step 5: Commit**

```bash
git add app/Sources/DiskTrendView.swift app/Sources/CleanerBridge.swift app/Sources/DashboardView.swift
git commit -m "feat: disk trend chart on the dashboard"
```

---

### Task 5: Version 2.3.0 + docs + bundle rebuild

**Files:**
- Modify: `cleaner.py` (VERSION), `app/Info.plist`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`, committed `MacCleaner.app`

- [ ] **Step 1:** `VERSION = "2.3.0"` in `cleaner.py`; both version keys in `app/Info.plist` → `2.3.0`.

- [ ] **Step 2: `AGENTS.md`** — document the `schedule` subcommand fully (the four actions, JSON shapes with exact keys — including `migrated_cron` on installs and `removed` on off — exit codes: status/off always 0, installs exit 1 when a load failed; `MACCLEANER_LAUNCH_AGENTS_DIR` override; note that `scheduler.sh` is now a wrapper over it). Verify every documented claim against the code before writing it. All additive.

- [ ] **Step 3: `CLAUDE.md`** — `schedule` in the CLI command list; scheduler.sh described as a wrapper; `DiskTrendView.swift` in the app structure list; updated test count (verify with a real run — expect 167).

- [ ] **Step 4: `README.md`** — Settings can now manage the schedule (no terminal needed); the Dashboard shows a free-space trend. Brief, matching tone. **`ROADMAP.md`** — tick the app-side schedule/preferences items this closes. **`CHANGELOG.md`** — new `## [2.3.0] — Unreleased` section: schedule subcommand + wrapper, in-app schedule management, disk trend chart.

- [ ] **Step 5: Rebuild the committed bundle** (mandatory — `app/Sources/` changed in Tasks 3–4):

```bash
bash app/build.sh && rm -rf MacCleaner.app && cp -R build/MacCleaner.app MacCleaner.app
```

Verify: `grep -c '2.3.0' MacCleaner.app/Contents/Info.plist` → 2, and `diff -q cleaner.py MacCleaner.app/Contents/Resources/cleaner.py` → identical.

- [ ] **Step 6: Verify and commit**

Run: `python3 -m unittest discover -s tests` → 167 OK. `python3 cleaner.py --version` → `MacCleaner 2.3.0`.

```bash
git add cleaner.py app/Info.plist AGENTS.md CLAUDE.md README.md ROADMAP.md CHANGELOG.md MacCleaner.app
git commit -m "docs: v2.3.0 — schedule subcommand, in-app scheduling, disk trends"
```

---

### Task 6: End-to-end verification

**Files:** none (fixes only if something fails).

- [ ] **Step 1:** Full suite (`python3 -m unittest discover -s tests -v`) → 167 OK, output pristine.

- [ ] **Step 2: Sandboxed schedule smoke** (stub `launchctl` + `crontab` on PATH, tempdir agents — **never unsandboxed: this machine has real loaded agents**):

```bash
d=$(mktemp -d); b="$d/bin"; mkdir -p "$b" "$d/agents"
printf '#!/bin/sh\nexit 0\n' > "$b/launchctl"; printf '#!/bin/sh\nexit 0\n' > "$b/crontab"
chmod +x "$b"/*
PATH="$b:$PATH" MACCLEANER_LAUNCH_AGENTS_DIR="$d/agents" python3 cleaner.py schedule weekly
PATH="$b:$PATH" MACCLEANER_LAUNCH_AGENTS_DIR="$d/agents" python3 cleaner.py schedule status --json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['schedule'], len(d['agents']))"
plutil -lint "$d"/agents/*.plist
PATH="$b:$PATH" MACCLEANER_LAUNCH_AGENTS_DIR="$d/agents" bash scheduler.sh status
```

Expected: `weekly 2`, both plists lint OK, wrapper status output matches the engine's.

- [ ] **Step 3: Real-machine read-only checks** (safe — no mutation): `python3 cleaner.py schedule status` shows the two real loaded agents from the 2.2 install; `python3 cleaner.py doctor --json` Schedule check agrees.

- [ ] **Step 4:** Legacy contract (`python3 cleaner.py --json | head -3` still scan JSON), app build already verified in Task 5, `git status --porcelain` clean of runtime files.

- [ ] **Step 5:** Commit any fixes as `fix: <what>`.

---

## Self-Review Notes

- **Spec coverage:** §1 subcommand → Task 1; wrapper + doctor → Task 2; §2 Settings → Task 3; §3 chart → Task 4; §4 version/docs/bundle → Task 5; testing → per-task + Task 6. plistlib upgrade is deliberate (structurally valid plists, closes the v2.2 review's unescaped-XML minor).
- **Type consistency:** `_schedule_state() -> {"schedule","agents","legacy_cron"}` consumed by `run_schedule_status`/`run_schedule_install`/`run_schedule_off`/doctor and mirrored by Swift's `ScheduleStatus`; `setSchedule` choices `"weekly"|"monthly"|"off"` match the CLI's action choices; `DiskSnapshot` keys match the engine's snapshot record (`ts`, `disk_free_bytes`, `disk_total_bytes`).
- **Test-count checkpoints** (150 → 166 → 167) are expectations, not gates; zero failures is the gate.
- **Output-compat is load-bearing:** `TestScheduler` keeps running against the wrapper with its human-string assertions unchanged — the engine's non-JSON messages must match the old bash output. Task 2 explicitly forbids weakening those assertions.
- **Safety:** every schedule-mutating command in tests and smoke runs behind stub `launchctl`/`crontab` + tempdir agents dir; the only real-machine calls are read-only `status`/`doctor`.
