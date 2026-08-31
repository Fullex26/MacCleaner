# V3: Native Swift Engine — Migration Design

**Status: design accepted, implementation not started.** This is the roadmap's
"Full Swift rewrite of cleaner engine" item, deliberately staged rather than
attempted as one rewrite. The Python engine is ~4,500 lines with 490+ tests
guarding deletion behaviour; a big-bang port cannot be verified to parity, and
an unverified deletion engine is the one artifact this project must never ship.

## Why (and why not yet)

- **Why:** removes the Python 3 requirement (macOS no longer guarantees a
  usable `python3` without Command Line Tools), enables a single-binary
  distribution, and is a precondition for any Mac App Store variant.
- **Why not yet:** the JSON contract (`AGENTS.md`) and the safety rules
  (home-only deletes, symlink handling, the tmp carve-out) live in tested
  Python. Parity must be *proved*, not assumed.

## Staged plan

1. **Contract fixtures.** Generate golden JSON fixtures from the Python engine
   (`scan`, `clean --dry-run`, `doctor`, `categories`, `schedule status`,
   `report --stats`) against a synthetic filesystem layout checked into
   `tests/fixtures/`. These are the parity oracle for every later stage.
2. **`MacCleanerKit` (Swift package).** Port read-only pieces first: target
   table, size measurement, scan. The Swift `scan --json` must byte-match the
   fixtures (modulo the machine-dependent `logs` targets).
3. **Dual-engine app.** The app gains an engine toggle (default: Python).
   Swift scan results are compared against the Python engine's in the
   background; divergences are logged, never acted on. This is the
   soak-test stage — run it for at least one release cycle.
4. **Deletion port, guard-first.** Port `_safe_to_delete` / `_remove` /
   `_tmp_scan_path_allowed` with the full adversarial suite (symlinked
   ancestors, the 48-scenario attack suite from 2.8.1) BEFORE porting any
   caller. Property tests: for every path the Python guard refuses, Swift
   must refuse.
5. **Cutover.** Swift becomes the default engine; Python ships one more
   release as `--engine python` fallback, then becomes contract documentation.

## Non-goals

- No behaviour changes during the port. Feature work continues on Python
  until stage 4 lands; anything added must come with a contract fixture.
- The v1 flag shim (`translate_legacy`) ports last — it is pure argv
  translation and can wrap either engine.

## Estimate

Stages 1–2 ≈ one focused week; stage 3 is calendar time (a release cycle of
soak); stages 4–5 ≈ two weeks including the adversarial re-verification.
Not a tail-end task — schedule it as its own cycle.
