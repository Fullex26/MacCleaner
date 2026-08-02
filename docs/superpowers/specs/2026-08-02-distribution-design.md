# Distribution — Signing-Ready Pipeline, Homebrew Cask, Shell Completions

**Date:** 2026-08-02
**Status:** Approved — QUEUED (maintainer reprioritized App Experience first; implement this after)
**Scope:** `release.yml`, new `Fullex26/homebrew-tap` repo, `completions/`, `install.sh`, `.claude/skills/maccleaner-release/`, docs. No engine behavior changes; everything folds into the still-unreleased 2.2.0, which becomes the first cask-installable tag.

Sub-project 3 of the v2.x program (Engine → Native experience → **Distribution**). Deprioritized 2026-08-02 in favor of an App Experience sub-project because the maintainer uses the GUI ~95% of the time; two of this spec's three pieces serve CLI users or third parties.

---

## Decisions already made (with the maintainer)

- No Apple Developer account yet, but one is planned: the pipeline is **signing-ready, env-gated** — it ships unsigned exactly as today until the secrets exist, then lights up with no workflow changes.
- Tap repo `Fullex26/homebrew-tap` is created at implementation time (public repo — creation itself was approved 2026-08-01). The **release skill** updates the cask (version + sha256) locally at release time under the maintainer's credentials; no CI write-token to the tap.
- Completions: **zsh + bash, hybrid** — static subcommands/flags, dynamic `--category`/`--targets` values from `categories --json` (fast; no measuring) with a 1s timeout falling back to static-only.
- Sparkle stays deferred until a Developer ID exists.

## 1. Signing-ready release pipeline

`app/build.sh` stays ad-hoc. In `release.yml`, after the build step, one stage gated on the presence of `MACOS_CERTIFICATE_P12`: import the .p12 into a throwaway keychain; `codesign --deep --force --options runtime` with the Developer ID identity; `xcrun notarytool submit --wait` (Apple ID + team ID + app-specific password secrets); `xcrun stapler staple`; then package. Signing-stage **failure fails the release loudly** (a half-signed artifact is worse than an unsigned one); secret **absence skips silently** with a log line. Release notes emit the right-click-to-open caveat only for unsigned builds. Secrets documented in `docs/RELEASING.md`: `MACOS_CERTIFICATE_P12`, `MACOS_CERTIFICATE_PWD`, `APPLE_ID`, `APPLE_TEAM_ID`, `NOTARY_PASSWORD`.

## 2. Homebrew tap + cask

`Fullex26/homebrew-tap` → `Casks/maccleaner.rb`: `version`, `sha256`, release-zip `url`, `app "MacCleaner.app"`, `zap` stanza for `~/Library/Application Support/MacCleaner` and the state files, and a `caveats` block with the Gatekeeper dance — dropped the day releases are notarized. Install: `brew install --cask fullex26/tap/maccleaner`. The cask ships the app only — its bundled fallback engine plus the v2.1 Application-Support state-file fallback make an app-only install fully functional; caveats point CLI users at `install.sh`. The `maccleaner-release` skill gains a final step: after the release workflow's assets exist, download the app zip, `shasum -a 256`, update the cask, commit and push to the tap. Validated with `brew style --cask` locally at implementation.

## 3. Shell completions

`completions/_maccleaner` (zsh) and `completions/maccleaner.bash`, committed. Static: subcommands, per-subcommand flags, config keys. Dynamic: category and target-ID values from `python3 ~/mac-cleaner/cleaner.py categories --json`, 1s timeout, silent static-only fallback. `install.sh` copies them to `~/mac-cleaner/completions/` and appends the zsh `fpath`/bash `source` wiring to the rc file with the same guarded-append pattern as the aliases. Shipped in the CLI tarball. Cover both the `maccleaner` alias and direct invocation.

## 4. Testing & docs

- CI syntax-checks both completion files (`zsh -n`, `bash -n`).
- A Python test cross-references `build_parser()`: every subcommand and flag must appear in both completion files, so adding a flag without updating completions fails the suite.
- The unsigned release path is identical to today's proven one; the signed path is validated by inspection and the first real signed tag. No dry-run workflow (YAGNI).
- Docs: new `docs/RELEASING.md`; README gains the brew one-liner; ROADMAP ticks Homebrew Cask + signing groundwork; CHANGELOG 2.2.0 gains completions + cask lines.

## Non-goals

- Sparkle auto-update (needs Developer ID).
- A brew formula for the CLI (cask + install.sh cover both audiences).
- Mac App Store anything.
