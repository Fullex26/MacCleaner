# Contributing to MacCleaner

Thanks for your interest in contributing. MacCleaner is intentionally simple — contributions that keep it that way are most welcome.

## Easiest First Contribution: Add a Cleanup Target

Each cleanup target in `cleaner.py` is one call to the `add(...)` helper inside `get_targets()`. Adding a new one (e.g. Cargo cache, CocoaPods, Gradle) is a great first PR:

```python
add("rust", "cargo-registry", "Cargo registry", "~/.cargo/registry",
    desc="Downloaded crate sources and archives")
```

`add(category, id, label, path, safe=True, cmd=None, desc="", empty_only=False)` — the third positional argument is the target's **stable kebab-case `id`** (e.g. `cargo-registry`); it's the identifier agents and `clean --targets` use, and it must not be renamed once it ships. `path` accepts `~`-relative strings and glob patterns (`*`); pass `path=None` with `cmd=` instead for a command-based target (e.g. `docker system prune`); `safe` defaults to `True` (see the Guidelines below); `empty_only=True` clears a directory's contents while keeping the directory itself (used for `~/Library/Caches` and `~/.Trash`).

Check the [Roadmap](ROADMAP.md) — Phase 1 lists targets we know we want. Open an issue first if you're unsure whether a target fits.

## Development Setup

No special setup needed. The project has no build step for the CLI.

```bash
git clone https://github.com/Fullex26/MacCleaner
cd maccleaner

# Test the CLI directly
python3 cleaner.py --preview
python3 cleaner.py --json

# Run a syntax check
python3 -m py_compile cleaner.py
```

## Guidelines

- **Keep it simple.** MacCleaner is a focused tool. Don't add features that belong in a different app.
- **Safe by default.** New cleanup targets that touch non-cache data should default to `safe: False` (requires confirmation).
- **No new dependencies.** The CLI should run on any Mac with Python 3 and no external packages. `rich` is the one allowed optional dep.
- **Update CHANGELOG.md** with your change under `[Unreleased]`.

## Pull Request Checklist

- [ ] `python3 -m py_compile cleaner.py` passes
- [ ] `python3 cleaner.py --preview` runs without errors
- [ ] `python3 cleaner.py --json` produces valid JSON
- [ ] If modifying `--json` output schema: updated the Swift `Codable` models in `app/Sources/CleanerBridge.swift` (additive changes only — see `CLAUDE.md`'s Superset rule) and the documented contract in `AGENTS.md`
- [ ] If adding a subcommand or flag: updated both `completions/_maccleaner` and `completions/maccleaner.bash` (a test in `tests/test_cleaner.py` cross-references them against `build_parser()` and fails the suite if they drift)
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

## Reporting Bugs

Use the [bug report template](/.github/ISSUE_TEMPLATE/bug_report.md). Include your macOS version and the output of `python3 cleaner.py --preview`.

## Code Style

- Python: standard library style, no formatter required. Readability over cleverness.
- Shell: POSIX-compatible bash, `set -e` at the top of scripts.
- Swift: match the existing style in `app/Sources/` — minimal, no third-party packages, no cleaning logic (the app is a thin client over `cleaner.py`'s JSON).
