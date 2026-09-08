# V3: Native Swift Engine — Migration Design

**Status: stages 1–3 landed (read-only), plus Stage 4's guards. Still open: Stage 2's
cmd-target estimates, Stage 4's deletion callers, Stage 5's cutover.** This is the roadmap's
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

## Status (2026-09-01)

Stages 1–3 are live: contract fixtures, the full read-only kit (static table
plus all three dynamic scanners), and the in-app dual-engine soak (which
caught one real bug in each engine during its first day). **Stage 4's first
half — the guards — is now ported**: `Guards.safeToDelete` (parent-resolved,
leaf-unresolved, 2.8.1 semantics) and `Guards.tmpScanPathAllowed` (two-level
carve-out), with `mck guard-check` as a read-only verdict query and
`tools/check_guard_parity.py` in CI judging a 20-scenario adversarial corpus
against BOTH engines — divergence or a wrong agreed verdict fails the build.
No deletion API exists yet; per the plan, a deletion caller may only be
written once the guards have parity (done) and the soak has accumulated
clean evidence over real use (in progress).

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
   fixtures. The **simulators and leftovers ports landed too** (`SimulatorScanner.swift`,
   `LeftoversScanner.swift`, #45), so the one thing still open within this
   stage is **cmd-target estimates**: `mck` never executes an estimate command
   and reports those targets presence-only, which is exactly why
   `tools/check_swift_parity.py` compares cmd targets for presence alone.
3. **Dual-engine app.** ✅ Landed (#46): the app bundles `mck` and, after each
   full scan, runs it read-only against the same config, compares both engines'
   target tables and logs any disagreement to `soak.log` (`v3_soak`, default
   on). Python stays the engine of record. **It earned its keep on the first
   live run** — 763 targets each, one divergence, `general-caches` at Python
   0 B against Swift 6.86 GB, which turned out to be a real Python bug
   (`du` exits non-zero on an unreadable subdirectory while still printing a
   correct total, and `get_size()` discarded it). This is the soak stage: keep
   it running for at least one release cycle before Stage 4's callers.
4. **Deletion port, guard-first.** **First half landed** (#51): `Guards.safeToDelete`
   (parent resolved, leaf deliberately unresolved — exact 2.8.1 semantics) and
   `Guards.tmpScanPathAllowed` (the two-level carve-out), exposed read-only as
   `mck guard-check`, with `tools/check_guard_parity.py` judging a 20-scenario
   adversarial corpus against BOTH engines in CI. No deletion API exists yet,
   and none may be written until the guards have parity (done) and the soak has
   accumulated clean evidence over real use (in progress). Still to port:
   `_remove` and the callers.
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
