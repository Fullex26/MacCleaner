# Contributing to MacCleaner

Thanks for your interest in contributing. MacCleaner is intentionally simple — contributions that keep it that way are most welcome.

## Easiest First Contribution: Add a Cleanup Target

Each cleanup target in `cleaner.py` is a small dictionary entry inside `get_targets()`. Adding a new one (e.g. Cargo cache, CocoaPods, Gradle) is a great first PR:

```python
{
    "name": "Cargo registry",
    "category": "rust",
    "path": Path.home() / ".cargo" / "registry",
    "safe": True,
    "description": "Cached crate downloads"
},
```

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
- [ ] If modifying `--json` output schema: updated Swift structs in `AppDelegate.swift`
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

## Reporting Bugs

Use the [bug report template](/.github/ISSUE_TEMPLATE/bug_report.md). Include your macOS version and the output of `python3 cleaner.py --preview`.

## Code Style

- Python: standard library style, no formatter required. Readability over cleverness.
- Shell: POSIX-compatible bash, `set -e` at the top of scripts.
- Swift: match the existing `AppDelegate.swift` style — minimal, no third-party packages.
