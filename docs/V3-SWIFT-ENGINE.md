# V3: Native Swift Engine — Migration Design

**Status: stages 1–2 landed (read-only); stages 3–5 not started.** This is the roadmap's
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

1. **Contract fixtures.** ✅ Landed: `tools/gen_contract_fixtures.py` builds a
   deterministic synthetic HOME (stub PATH holding only `du`, 4 KiB-aligned
   file sizes so APFS block rounding is identity, sandboxed scanner roots)
   and writes `tests/fixtures/*.json` for `scan --all`, `categories`,
   `clean --dry-run`, `report --stats`, and `schedule status`.
   `TestContractFixtures` regenerates and diffs on every run, so any engine
   change that moves the JSON contract fails the suite until the fixtures
   are consciously regenerated. (`doctor` is deliberately excluded — its
   output is inherently machine-dependent.)
2. **`MacCleanerKit` (Swift package).** ✅ Landed (`swift/MacCleanerKit`,
   CLI `mck`): target table, path/glob resolution, `du -skx` measurement,
   `scan --json --all` and `categories --json`. Two deliberate deviations
   from the original sketch: the table is **generated** from the Python
   source (`tools/gen_swift_target_table.py`, freshness pinned by
   `TestSwiftTableGenerated`) rather than ported by hand, so transcription
   drift is impossible by construction; and parity is **semantic** (per-id
   field comparison via `tools/check_swift_parity.py`, run by CI on every
   push) rather than byte-for-byte, since JSON key order proves nothing.
   The sabotage test was run before trusting the gate: one wrong path in
   the generated table fails parity naming the exact target and field.
   The **tmp scanner is ported** (`TmpScanner.swift`): content
   classification, the 2.14.2 liveness guard (own-process-tree exclusion
   included), nested build-tree detection, slugify/id parity — covered by
   XCTest unit tests (CI runs `swift test --enable-xctest`; the bare
   `swift test` silently discovers zero XCTest cases on current toolchains)
   plus fixture-sandbox parity: 4 offered and 5 refused tmp scenarios are
   seeded by `tools/gen_contract_fixtures.py` and pinned in the committed
   fixtures. Still open within this stage: simulators and leftovers ports,
   and cmd-target estimates (presence-only in `mck`) — owned by a parallel
   session along with Stage 3's dual-engine soak.
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
