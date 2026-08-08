# Distribution v2.4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship shell completions, make the release pipeline sign and notarize automatically the day an Apple Developer ID exists, and land a validated Homebrew cask in-repo (the public tap stays unpublished until releases are notarized).

**Architecture:** One new secret-gated stage in `release.yml` (probe step → `$GITHUB_OUTPUT` → `if:` on the signing steps), hand-written zsh + bash completion files wired by `install.sh` under their own guard string, and a `Casks/maccleaner.rb` kept in-repo with the procedure documented in a tracked `docs/RELEASING.md`.

**Tech Stack:** GitHub Actions (macos-latest), `security`/`codesign`/`xcrun notarytool`/`stapler`, zsh completion (`#compdef`), bash 3.2-compatible completion, Homebrew Cask DSL, Python 3 stdlib for tests.

**Spec:** `docs/superpowers/specs/2026-08-02-distribution-design.md` — approved, with two amendments recorded in Task 5 (target version is 2.4.0 not 2.2.0; the public tap is deliberately NOT created this round).

## Global Constraints

- `cleaner.py` stays a single file, Python 3 **stdlib only**. No runtime dependency may be added (this rules out `argcomplete`/`shtab` for completions — they are hand-written).
- Tests: `python3 -m unittest discover -s tests` from the repo root; **182 passing** on `main` at f16acaf. Zero failures is the gate at every task.
- **This machine has REAL loaded MacCleaner launchd agents** (`com.fullex.maccleaner.clean` weekly + `.diskwatch` hourly) and a live install at `~/mac-cleaner`. Never run `schedule weekly|monthly|off`, `scheduler.sh weekly|monthly|remove`, or `install.sh` outside a sandbox. Never `brew tap`/`brew install` against a real tap without cleaning up.
- Do not touch `_safe_to_delete`, `_remove`, `delete_target`, or any engine behavior. This sub-project adds distribution machinery only; the sole `cleaner.py` change is the `VERSION` constant.
- The committed `MacCleaner.app` bundle must be rebuilt in the task that bumps the version (Task 5) — `install.sh` falls back to it when `swiftc` is absent.
- Release-asset naming is fixed by history: `VERSION` in `release.yml` is the raw tag (`v2.4.0`), so assets are `MacCleaner-v2.4.0-macos-universal.zip`. The cask URL must therefore carry the `v` **twice**. Do not "fix" the workflow to drop it — that breaks continuity with the published v2.0.0 asset names.
- **The public tap is NOT created in this round** (maintainer decision, 2026-08-08): Homebrew 6 removed `--no-quarantine` and never had `quarantine: false`, so an unsigned cask is Gatekeeper-blocked with no supported workaround. The cask lives in-repo, validated, ready to publish the day notarization lands.

## Research already done (do not redo)

A four-agent research pass produced verified artifacts and findings. Key ones, with the consequences already folded into the tasks below:

- `secrets` is unavailable in **any** `if:` (job or step); `env` is available in a **step-level** `if:` only. The supported pattern is a probe step writing a boolean to `$GITHUB_OUTPUT`.
- `notarytool submit` accepts only `.dmg`, signed `.pkg`, or `.zip` — **never a bare `.app`**. Package with `ditto -c -k --keepParent`, submit the zip, then `stapler staple` the **`.app`**.
- `--options runtime` (hardened runtime) is mandatory for notarization. **No entitlements file is needed here**: the app's only Mach-O is `Contents/MacOS/MacCleaner`, and spawning `/usr/bin/python3` via `Process` is a plain exec of a separate Apple-signed binary, not something the parent's hardened runtime restricts.
- Homebrew 6 requires tap trust by default, so the two-step `brew tap … && brew install --cask maccleaner` **hard-fails**. The only form to document is the fully-qualified one-liner, which auto-taps and auto-trusts.
- `brew audit --cask --new` fails this project for reasons unrelated to cask correctness (unsigned binary, repo notability). Plain `brew audit --cask` passes clean and is the correct gate for a personal tap.
- `depends_on macos: :ventura` (bare symbol, meaning ">= Ventura"). The string-comparison form is runtime-deprecated.
- In a `zap`, directives run in a fixed order with `:launchctl` before `:trash`, so one `zap launchctl: [...], trash: [...]` correctly unloads the agents before removing their plists.
- `install.sh`'s existing alias guard greps the bare string `mac-cleaner`, which **every existing install already matches** — completions wiring placed inside that `if` would silently never install for anyone who has ever run the installer. This is the single highest-risk detail in the change.

**Verified artifacts to copy** (built and tested during research; treat as reviewed source, but re-verify after copying):
- `<SCRATCH>/comp/_maccleaner` — zsh completion, 266 lines. Three-layer value lookup (per-shell memo → zsh disk cache → subprocess), `perl -e 'alarm …'` for the wall-clock cap because macOS has no `timeout(1)`, static category fallback.
- `<SCRATCH>/comp/maccleaner.bash` — bash completion, 215 lines, bash 3.2 compatible (macOS ships 3.2.57).
- `<SCRATCH>/comp/run_tests.sh` — 47-line non-interactive harness; 24/24 assertions passed on zsh 5.9 + bash 3.2.57.
- `<SCRATCH>/maccleaner.rb` — cask; `brew style --cask` zero offences, `brew audit --cask` exit 0, validated against the real v2.0.0 release assets.

where `<SCRATCH>` = `/private/tmp/claude-501/-Users-jordanfuller-Documents-Code-Projects-MacOS-MacCleaner/a75fa25d-47e6-4d08-8337-f06fb91ac545/scratchpad`. **If a file is missing** (scratchpads are ephemeral), say so and write it from the descriptions in the task rather than silently shipping something weaker.

## File Structure

- Create: `completions/_maccleaner`, `completions/maccleaner.bash`, `completions/run_tests.sh`
- Create: `Casks/maccleaner.rb` — the cask, in-repo until the tap is published
- Create: `docs/RELEASING.md` — tracked release procedure: signing secrets, cask bump, tap creation
- Modify: `.github/workflows/release.yml` — signing/notarization stage, conditional caveat, completions in the CLI tarball
- Modify: `.github/workflows/ci.yml` — completion syntax checks
- Modify: `install.sh` — copy completions, wire them under a NEW guard
- Modify: `tests/test_cleaner.py` — parser↔completions cross-reference test
- Modify: `cleaner.py` (VERSION only), `app/Info.plist`, `MacCleaner.app` (rebuild), `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`, `CONTRIBUTING.md`

---

### Task 1: Shell completions + parser cross-reference test

**Files:**
- Create: `completions/_maccleaner`, `completions/maccleaner.bash`, `completions/run_tests.sh`
- Modify: `tests/test_cleaner.py`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `build_parser()` in `cleaner.py` (prog `maccleaner`; subcommands `scan clean projects report doctor config categories disk-check schedule install-deps`; `config` sub-subparsers `show path enable disable set`; `schedule` positional choices `status weekly monthly off`), and `cleaner.py categories --json` for dynamic values.
- Produces: the three files above, plus a test that fails when a parser subcommand or flag is missing from either completion file.

- [ ] **Step 1: Copy the verified completion files**

```bash
SCRATCH=/private/tmp/claude-501/-Users-jordanfuller-Documents-Code-Projects-MacOS-MacCleaner/a75fa25d-47e6-4d08-8337-f06fb91ac545/scratchpad
mkdir -p completions
cp "$SCRATCH/comp/_maccleaner" "$SCRATCH/comp/maccleaner.bash" "$SCRATCH/comp/run_tests.sh" completions/
chmod +x completions/run_tests.sh
wc -l completions/*
```

Expected: `_maccleaner` ~266 lines, `maccleaner.bash` ~215, `run_tests.sh` ~47.

- [ ] **Step 2: Read both completion files end to end and confirm they match this project.** Specifically check: the `#compdef` line covers `maccleaner` and the aliases; the subcommand list matches `build_parser()` exactly; the engine lookup honours `MACCLEANER_ENGINE` then `~/mac-cleaner/cleaner.py`; the dynamic lookup has a timeout and falls back to static values. Fix any drift from the current parser. Report anything you changed.

- [ ] **Step 3: Verify they actually work**

```bash
zsh -n completions/_maccleaner && echo "zsh syntax OK"
bash -n completions/maccleaner.bash && echo "bash syntax OK"
bash completions/run_tests.sh
```

Expected: both syntax checks pass; the harness reports all assertions passing. If the harness references paths under `$SCRATCH`, fix it to be self-contained inside the repo before committing.

- [ ] **Step 4: Write the failing cross-reference test** — append to `tests/test_cleaner.py`:

```python
class TestCompletions(unittest.TestCase):
    """The completion files are hand-written, so they can drift from the
    parser silently. This test is the tripwire: adding a subcommand or flag
    without updating both completion files fails the suite."""

    @classmethod
    def setUpClass(cls):
        cls.zsh = (REPO / "completions" / "_maccleaner").read_text()
        cls.bash = (REPO / "completions" / "maccleaner.bash").read_text()
        cls.parser = cleaner.build_parser()

    def _subparser_action(self):
        import argparse
        for action in self.parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        self.fail("no subparsers found in build_parser()")

    def test_every_subcommand_in_both_files(self):
        for name in self._subparser_action().choices:
            self.assertIn(name, self.zsh, f"zsh completion missing subcommand {name}")
            self.assertIn(name, self.bash, f"bash completion missing subcommand {name}")

    def test_every_flag_in_both_files(self):
        missing = []
        for name, sub in self._subparser_action().choices.items():
            for action in sub._actions:
                for flag in action.option_strings:
                    if flag in ("-h", "--help"):
                        continue
                    if flag not in self.zsh:
                        missing.append(f"zsh: {name} {flag}")
                    if flag not in self.bash:
                        missing.append(f"bash: {name} {flag}")
        self.assertEqual(missing, [], "completion files are stale:\n" + "\n".join(missing))

    def test_schedule_actions_present(self):
        """schedule's positional choices are values, not flags — easy to miss."""
        for action in self._subparser_action().choices["schedule"]._actions:
            if action.dest == "action":
                for choice in action.choices:
                    self.assertIn(choice, self.zsh, f"zsh missing schedule {choice}")
                    self.assertIn(choice, self.bash, f"bash missing schedule {choice}")
                return
        self.fail("schedule subparser has no 'action' positional")

    def test_config_subcommands_present(self):
        for action in self._subparser_action().choices["config"]._actions:
            if action.choices and "show" in action.choices:
                for choice in action.choices:
                    self.assertIn(choice, self.zsh, f"zsh missing config {choice}")
                    self.assertIn(choice, self.bash, f"bash missing config {choice}")
                return
        self.fail("config subparser has no sub-subparsers")
```

- [ ] **Step 5: Run it**

Run: `python3 -m unittest tests.test_cleaner.TestCompletions -v`
Expected: PASS if the copied files are current. **If any assertion fails, the completion file is genuinely stale — fix the completion file, not the test.** Report which flags were missing.

- [ ] **Step 6: Add CI syntax checks.** In `.github/workflows/ci.yml`, inside the first job ("Tests + Smoke Test"), after the unit-test step:

```yaml
      - name: Check shell completion syntax
        run: |
          zsh -n completions/_maccleaner
          bash -n completions/maccleaner.bash
          echo "✅ completion files parse"
```

- [ ] **Step 7: Full suite, then commit**

Run: `python3 -m unittest discover -s tests` → 187 tests, OK (182 + 5).

```bash
git add completions tests/test_cleaner.py .github/workflows/ci.yml
git commit -m "feat: zsh and bash completions with a parser cross-reference test"
```

---

### Task 2: Wire completions into install.sh and the CLI tarball

**Files:**
- Modify: `install.sh`, `.github/workflows/release.yml` (CLI tarball step only)

**Interfaces:**
- Consumes: `completions/` from Task 1.
- Produces: completions installed to `~/mac-cleaner/completions/`, sourced from the user's rc file; shipped inside the CLI tarball.

**The critical detail:** `install.sh`'s alias block is guarded by `if ! grep -q "mac-cleaner" "$SHELL_RC"`. Every existing install's `.zshrc` already contains `~/mac-cleaner/cleaner.py`, so that guard is already satisfied for them. Completions wiring must use its **own** guard string (grep for `mac-cleaner/completions`) or it will silently never install for any existing user — which is most users.

- [ ] **Step 1: Copy completions during install.** In `install.sh`'s "# 2. Copy files" section, after the `scheduler.sh` copy:

```bash
if [ -d "$SCRIPT_DIR/completions" ]; then
    mkdir -p "$INSTALL_DIR/completions"
    cp "$SCRIPT_DIR/completions/"* "$INSTALL_DIR/completions/" 2>/dev/null || true
fi
```

The `-d` guard matters: the CLI tarball may or may not carry the directory, and a missing directory must not fail the install.

- [ ] **Step 2: Wire them into the shell.** Add a new section after the existing "# 4. Shell aliases" block, before the schedule section, and renumber the following comments:

```bash
# 5. Shell completions (own guard — the alias guard above already matches for
# anyone who has ever run this installer, so reusing it would silently skip
# completions for every existing user)
COMPLETIONS_DIR="$INSTALL_DIR/completions"
if [ -d "$COMPLETIONS_DIR" ]; then
    ZSHRC="$HOME/.zshrc"
    if ! grep -q "mac-cleaner/completions" "$ZSHRC" 2>/dev/null; then
        {
            echo ""
            echo "# MacCleaner completions"
            echo "fpath=(\"\$HOME/mac-cleaner/completions\" \$fpath)"
            echo "autoload -Uz compinit && compinit -u"
        } >> "$ZSHRC"
        echo "→ Added zsh completions (restart your shell to use them)"
    fi

    # bash: macOS ships bash 3.2, and there may be no bash-completion install,
    # so source the file directly from whichever rc file bash actually reads.
    for BASHRC in "$HOME/.bash_profile" "$HOME/.bashrc"; do
        [ -f "$BASHRC" ] || continue
        if ! grep -q "mac-cleaner/completions" "$BASHRC" 2>/dev/null; then
            {
                echo ""
                echo "# MacCleaner completions"
                echo "[ -r \"\$HOME/mac-cleaner/completions/maccleaner.bash\" ] && \\"
                echo "    . \"\$HOME/mac-cleaner/completions/maccleaner.bash\""
            } >> "$BASHRC"
            echo "→ Added bash completions to $(basename "$BASHRC")"
        fi
    done
fi
```

Note the deliberate choice: bash wiring only touches rc files that **already exist**, so the installer never creates a bash profile for a zsh-only user.

- [ ] **Step 3: Ship completions in the CLI tarball.** In `.github/workflows/release.yml`'s "Package CLI-only tarball" step, extend the copy so the directory travels:

```bash
          cp cleaner.py install.sh scheduler.sh config.json README.md AGENTS.md LICENSE "${CLI_DIR}/"
          cp -R completions "${CLI_DIR}/"
```

- [ ] **Step 4: Verify install.sh in a sandbox — never against the real home**

```bash
d=$(mktemp -d)
HOME="$d" bash install.sh >"$d/out.txt" 2>&1 </dev/null; echo "exit=$?"
tail -20 "$d/out.txt"
ls "$d/mac-cleaner/completions/"
grep -n "mac-cleaner" "$d/.zshrc"
```

Expected: exit 0; `completions/` contains all three files; `.zshrc` has both the alias block and a separate completions block. `</dev/null` keeps the interactive schedule prompt from hanging, and `HOME=$d` keeps the real `~/.zshrc`, `~/mac-cleaner`, and launchd agents untouched. Confirm afterwards that the real `~/.zshrc` was not modified (`git diff` won't show it — check its mtime, or diff against a copy taken before the run).

- [ ] **Step 5: Verify the idempotence that matters most** — re-run the installer in the same sandbox and confirm the completions block is **not** duplicated, and that a sandbox seeded with only the *alias* block (simulating an existing user) still gets completions added:

```bash
HOME="$d" bash install.sh >/dev/null 2>&1 </dev/null
grep -c "MacCleaner completions" "$d/.zshrc"   # must be 1

d2=$(mktemp -d)
printf '\n# MacCleaner\nalias maccleaner=%s\n' "'python3 ~/mac-cleaner/cleaner.py'" > "$d2/.zshrc"
HOME="$d2" bash install.sh >/dev/null 2>&1 </dev/null
grep -c "mac-cleaner/completions" "$d2/.zshrc"  # must be >= 1 — this is the regression the own-guard exists to prevent
```

- [ ] **Step 6: Commit**

```bash
git add install.sh .github/workflows/release.yml
git commit -m "feat: install shell completions under their own rc guard"
```

---

### Task 3: Secret-gated signing and notarization in release.yml

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the built `build/MacCleaner.app` from the existing "Build app bundle from source" step.
- Produces: a `signed` output consumed by the release-notes step; a signed, notarized, stapled `.app` for the existing packaging step to zip.

**Placement is load-bearing:** the new stage goes **after** "Build app bundle from source" and **before** "Package app bundle as zip" — `ditto` is what captures the signature and the stapled ticket, so signing after packaging would ship an unsigned zip.

**Why re-signing is safe:** `app/build.sh` ad-hoc signs with `codesign --force --sign -` as the very last thing it does, and adds nothing to the bundle afterward. `codesign --force` replaces that signature and regenerates `Contents/_CodeSignature/CodeResources`. Leave `build.sh` alone.

- [ ] **Step 1: Add the probe step.** Insert immediately after "Build app bundle from source":

```yaml
      - name: Detect signing credentials
        id: signing
        env:
          CERT: ${{ secrets.MACOS_CERTIFICATE_P12 }}
        run: |
          # `secrets` cannot be referenced in any `if:` — job or step. `env`
          # can, but only in a step-level `if:`. So probe here, publish a
          # boolean, and gate the real steps on the output.
          if [ -n "$CERT" ]; then
            echo "enabled=true" >> "$GITHUB_OUTPUT"
            echo "→ Signing credentials present — will sign and notarize"
          else
            echo "enabled=false" >> "$GITHUB_OUTPUT"
            echo "→ No signing credentials — shipping an ad-hoc signed build"
          fi
```

- [ ] **Step 2: Add the signing step:**

```yaml
      - name: Sign, notarize, and staple
        if: steps.signing.outputs.enabled == 'true'
        env:
          CERT_P12: ${{ secrets.MACOS_CERTIFICATE_P12 }}
          CERT_PWD: ${{ secrets.MACOS_CERTIFICATE_PWD }}
          APPLE_ID: ${{ secrets.APPLE_ID }}
          APPLE_TEAM_ID: ${{ secrets.APPLE_TEAM_ID }}
          NOTARY_PASSWORD: ${{ secrets.NOTARY_PASSWORD }}
        run: |
          set -euo pipefail
          APP="build/MacCleaner.app"

          # 1. Import the certificate into a throwaway keychain.
          KEYCHAIN="$RUNNER_TEMP/signing.keychain-db"
          KEYCHAIN_PWD="$(uuidgen)"
          security create-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN"
          security set-keychain-settings -lut 900 "$KEYCHAIN"
          security unlock-keychain -p "$KEYCHAIN_PWD" "$KEYCHAIN"
          echo "$CERT_P12" | base64 --decode > "$RUNNER_TEMP/cert.p12"
          security import "$RUNNER_TEMP/cert.p12" -k "$KEYCHAIN" -P "$CERT_PWD" \
            -T /usr/bin/codesign -T /usr/bin/security
          rm -f "$RUNNER_TEMP/cert.p12"
          # Without this, codesign blocks on a GUI keychain prompt that never comes.
          security set-key-partition-list -S apple-tool:,apple:,codesign: \
            -s -k "$KEYCHAIN_PWD" "$KEYCHAIN" >/dev/null
          security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')

          IDENTITY="$(security find-identity -v -p codesigning "$KEYCHAIN" \
            | awk '/Developer ID Application/ {print $2; exit}')"
          if [ -z "$IDENTITY" ]; then
            echo "::error::No 'Developer ID Application' identity in the imported certificate."
            exit 1
          fi

          # 2. Sign. --options runtime (hardened runtime) is mandatory for
          # notarization; --timestamp is required for a distributable signature.
          # No entitlements file is needed: the only Mach-O is Contents/MacOS/
          # MacCleaner, and it launches /usr/bin/python3 as a separate
          # Apple-signed process, which the hardened runtime does not restrict.
          codesign --force --deep --options runtime --timestamp \
            --sign "$IDENTITY" --keychain "$KEYCHAIN" "$APP"
          codesign --verify --deep --strict --verbose=2 "$APP"

          # 3. Notarize. notarytool cannot take a bare .app — only .zip/.dmg/.pkg.
          ditto -c -k --keepParent "$APP" "$RUNNER_TEMP/notarize.zip"
          xcrun notarytool submit "$RUNNER_TEMP/notarize.zip" \
            --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
            --password "$NOTARY_PASSWORD" --wait

          # 4. Staple the ticket to the .app (not the zip) so it validates offline.
          xcrun stapler staple "$APP"
          xcrun stapler validate "$APP"
          echo "✅ Signed, notarized, and stapled"
```

- [ ] **Step 3: Always clean up the keychain,** even when signing fails — add after the signing step:

```yaml
      - name: Remove signing keychain
        if: always() && steps.signing.outputs.enabled == 'true'
        run: security delete-keychain "$RUNNER_TEMP/signing.keychain-db" || true
```

- [ ] **Step 4: Make the Gatekeeper caveat conditional.** In "Generate release notes", the two hard-coded lines about the app not being notarized must only appear for unsigned builds. Replace them with:

```bash
            if [ "${{ steps.signing.outputs.enabled }}" != "true" ]; then
              echo "> **First launch:** macOS may block the app (it is not yet notarized)."
              echo "> Right-click MacCleaner.app → Open → confirm."
              echo ""
            fi
```

Keep the surrounding notes text unchanged.

- [ ] **Step 5: Validate the workflow parses and the gate is correct**

```bash
python3 -c "import json,subprocess; print('yaml ok')" # placeholder-free check below instead
ruby -ryaml -e 'YAML.load_file(".github/workflows/release.yml"); puts "release.yml parses"'
ruby -ryaml -e 'YAML.load_file(".github/workflows/ci.yml"); puts "ci.yml parses"'
grep -n "steps.signing.outputs.enabled" .github/workflows/release.yml
```

Expected: both files parse (ruby ships with macOS); the gate appears on the signing step, the cleanup step, and in the release-notes conditional — three occurrences minimum. Confirm no step uses `secrets` inside an `if:`:

```bash
grep -n "if:.*secrets\." .github/workflows/*.yml || echo "✅ no secrets in any if: (would silently never run)"
```

- [ ] **Step 6: Confirm the unsigned path is unchanged.** Diff the step list before and after to prove only additions were made:

```bash
git diff .github/workflows/release.yml | grep '^-' | grep -v '^---' | grep -v 'not yet notarized' | grep -v 'Right-click'
```

Expected: no removals other than the two caveat lines that moved into the conditional. Anything else means an existing step was altered — investigate.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "feat: secret-gated signing and notarization in the release workflow"
```

---

### Task 4: Cask + tracked release procedure

**Files:**
- Create: `Casks/maccleaner.rb`, `docs/RELEASING.md`

**Interfaces:**
- Consumes: the release-asset naming (`MacCleaner-v<version>-macos-universal.zip`) and the launchd agent labels (`com.fullex.maccleaner.clean`, `com.fullex.maccleaner.diskwatch`).
- Produces: a validated cask ready to copy into a public tap, and the procedure for doing so.

The public tap repo is deliberately **not** created — see Global Constraints. `Casks/maccleaner.rb` sits in this repo so it is reviewed, version-controlled, and one `cp` away from publication.

- [ ] **Step 1: Copy the validated cask**

```bash
SCRATCH=/private/tmp/claude-501/-Users-jordanfuller-Documents-Code-Projects-MacOS-MacCleaner/a75fa25d-47e6-4d08-8337-f06fb91ac545/scratchpad
mkdir -p Casks
cp "$SCRATCH/maccleaner.rb" Casks/maccleaner.rb
cat Casks/maccleaner.rb
```

If the scratchpad file is gone, write it fresh with: stanza order `version, sha256, url + verified:, name, desc, homepage, livecheck, depends_on, app, zap`; `url "https://github.com/Fullex26/MacCleaner/releases/download/v#{version}/MacCleaner-v#{version}-macos-universal.zip", verified: "github.com/Fullex26/MacCleaner/"`; `depends_on macos: :ventura`; `app "MacCleaner.app"`; and a `zap launchctl: ["com.fullex.maccleaner.clean", "com.fullex.maccleaner.diskwatch"], trash: [...]` listing `~/Library/Application Support/MacCleaner`, both `~/Library/LaunchAgents/com.fullex.maccleaner.*.plist`, and the usual `~/Library/{Caches,HTTPStorages,Preferences,Saved Application State}` entries for `com.fullex.MacCleaner`.

- [ ] **Step 2: Set the version to 2.4.0 and leave the sha256 as an explicit placeholder.** The real hash cannot exist until the release is published. Set `version "2.4.0"` and keep the all-zeros sha256, with a comment directly above it:

```ruby
  # Placeholder — replaced at release time from the published asset.
  # See docs/RELEASING.md. A wrong sha256 makes `brew install` fail loudly,
  # which is the desired failure mode for an unpublished cask.
```

- [ ] **Step 3: Validate it, without leaving a tap behind**

```bash
brew style --cask Casks/maccleaner.rb && echo "style clean"
ruby -c Casks/maccleaner.rb
```

Expected: `brew style` reports no offences; ruby reports `Syntax OK`. Note in your report that `brew audit --cask` needs the file inside a tap; do **not** create a real tap on this machine — if you do for validation, `brew untap` it afterwards and say so.

- [ ] **Step 4: Write `docs/RELEASING.md`** — the tracked procedure. It must cover, concretely:

1. **The five signing secrets** and their exact shapes: `MACOS_CERTIFICATE_P12` (base64 of the exported .p12 — show the `base64 -i cert.p12 | pbcopy` command), `MACOS_CERTIFICATE_PWD`, `APPLE_ID`, `APPLE_TEAM_ID`, `NOTARY_PASSWORD` (an app-specific password from appleid.apple.com, **not** the Apple ID password). Note the alternative App Store Connect API-key auth (`-k/-d/-i`) and that the Apple-ID form is what the workflow implements.
2. **What happens without them**: the workflow ships ad-hoc signed exactly as today, and the release notes keep the Gatekeeper caveat. Nothing to change when the secrets appear — the probe step picks them up.
3. **Release steps**, matching the existing skill: sync `VERSION` in `cleaner.py` and both keys in `app/Info.plist`, **and the version prose in `AGENTS.md`** (the skill currently omits this — AGENTS.md carries the version in its intro and in ~13 embedded JSON examples); run the suite; rebuild the committed bundle; update `CHANGELOG.md`; commit; tag; push.
4. **Publishing the tap** (for when notarization lands): create `github.com/Fullex26/homebrew-tap` (the `homebrew-` prefix is required for the short form), `mkdir Casks` (`brew tap-new` does not create it), copy `Casks/maccleaner.rb`, and document the **only** install command that works under Homebrew 6: `brew install --cask Fullex26/tap/maccleaner` — the two-step `brew tap` + `brew install` hard-fails on tap trust.
5. **Bumping the cask after a release**: poll for the asset before hashing, since the workflow publishes asynchronously —

```bash
gh release view "v$VERSION" --repo Fullex26/MacCleaner --json assets \
  --jq '.assets[].name'
curl -fsSL -o /tmp/mc.zip \
  "https://github.com/Fullex26/MacCleaner/releases/download/v$VERSION/MacCleaner-v$VERSION-macos-universal.zip"
shasum -a 256 /tmp/mc.zip
```

…then update `version` and `sha256` in the cask, and push to the tap.
6. **Why `brew audit --cask --new` is not the gate**: it fails on the unsigned binary and on repo notability, neither of which indicates a cask defect. Plain `brew audit --cask` is the check that matters.

- [ ] **Step 5: Commit**

```bash
git add Casks docs/RELEASING.md
git commit -m "docs: validated Homebrew cask and tracked release procedure"
```

---

### Task 5: Version 2.4.0, documentation, and the stale-docs sweep

**Files:**
- Modify: `cleaner.py` (VERSION), `app/Info.plist`, `MacCleaner.app` (rebuild), `AGENTS.md`, `CLAUDE.md`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`, `CONTRIBUTING.md`

- [ ] **Step 1: Bump versions.** `cleaner.py` → `VERSION = "2.4.0"`; `app/Info.plist` → both `CFBundleShortVersionString` and `CFBundleVersion` `2.4.0`. Then grep `AGENTS.md` for the old version and update its intro prose and every embedded `"version": "2.3.0"` in the documented JSON examples — the release skill's version-sync list omits this file, which is why it drifts.

- [ ] **Step 2: `AGENTS.md`** — document what agents can now rely on: the `completions/` directory exists and is shipped in the CLI tarball. Do not invent contract changes; the engine's JSON is untouched this round. Verify before writing.

- [ ] **Step 3: `CLAUDE.md`** — a new `## Distribution` section after `## Install Path vs. Source`, covering: completions (files, install wiring, the parser cross-reference test as the drift tripwire), release-time signing (secret-gated, `docs/RELEASING.md` is the reference, `app/build.sh` still ad-hoc signs for dev builds), and the in-repo cask with the tap deliberately unpublished. Also update `### App Build` — it is the only place documenting build.sh's signing — and fix the **test count** (it says 168; the real number is whatever the suite reports now).

- [ ] **Step 4: `README.md`** — in `## Install`, add the completions fact (installed automatically, restart your shell). Do **not** advertise a `brew install` line: the tap is unpublished, and pointing users at a command that fails is worse than saying nothing. Leave the existing "First launch: macOS may warn about unsigned apps" caveat — still true until notarization.

- [ ] **Step 5: `ROADMAP.md`** — tick **Shell completions**; mark **Code signing** and **Notarization** as "pipeline ready, waiting on an Apple Developer ID"; for **Homebrew Cask** note the cask is written and validated but the tap is unpublished pending notarization, with the Homebrew 6 reason. Fix the stale `## Current State — v2.0 ✅` header and the stale test count. Leave Sparkle deferred.

- [ ] **Step 6: `CHANGELOG.md`** — new section at the top following the file's format:

```markdown
## [2.4.0] — Unreleased

### Added
- Shell completions for zsh and bash — subcommands, per-subcommand flags, config keys, and live category/target-ID completion from the engine (cached, with a timeout and static fallback). Installed automatically by `install.sh` and shipped in the CLI tarball
- Release-time code signing and notarization, gated on repository secrets: the workflow ships ad-hoc signed exactly as before when they are absent, and signs, notarizes, and staples automatically once they exist — no workflow change needed
- `Casks/maccleaner.rb` — a validated Homebrew cask, plus `docs/RELEASING.md` documenting the signing secrets, the release steps, and how to publish the tap

### Notes
- The public Homebrew tap is intentionally unpublished until releases are notarized: Homebrew 6 removed `--no-quarantine`, so an unsigned cask cannot launch cleanly and there is no supported workaround
```

- [ ] **Step 7: `CONTRIBUTING.md`** — fix the two stale references the audit found: the PR checklist points at `AppDelegate.swift` (a v1 file; the app is now `app/Sources/`, e.g. `CleanerBridge.swift`), and the add-a-target example uses the pre-v2 dict shape with a `name` key instead of the `add(...)` helper with stable IDs. Add a checklist line: if you add a subcommand or flag, update both completion files.

- [ ] **Step 8: Rebuild the committed bundle** (mandatory — the version changed):

```bash
bash app/build.sh && rm -rf MacCleaner.app && cp -R build/MacCleaner.app MacCleaner.app
grep -c '2.4.0' MacCleaner.app/Contents/Info.plist   # expect 2
diff -q cleaner.py MacCleaner.app/Contents/Resources/cleaner.py && echo "engine identical"
ls MacCleaner.app/Contents/Resources/MacCleaner.icns
```

- [ ] **Step 9: Verify and commit**

Run: `python3 -m unittest discover -s tests` → all passing, zero failures. `python3 cleaner.py --version` → `MacCleaner 2.4.0`.

```bash
git add cleaner.py app/Info.plist MacCleaner.app AGENTS.md CLAUDE.md README.md ROADMAP.md CHANGELOG.md CONTRIBUTING.md
git commit -m "docs: v2.4.0 — completions, signing readiness, cask"
```

---

### Task 6: End-to-end verification

**Files:** none (fixes only if something fails).

- [ ] **Step 1:** Full suite — `python3 -m unittest discover -s tests -v` → all passing, output pristine.

- [ ] **Step 2: Completions, for real.** Load them in a real shell and confirm they produce completions, not just that they parse:

```bash
bash completions/run_tests.sh
zsh -n completions/_maccleaner && bash -n completions/maccleaner.bash && echo "syntax OK"
```

Then confirm the dynamic path degrades: point the engine lookup at a nonexistent path and check the static fallback still yields categories.

```bash
MACCLEANER_ENGINE=/nonexistent/cleaner.py bash completions/run_tests.sh 2>&1 | tail -5
```

Report what happened — a hang here would be a real defect (the timeout exists for this).

- [ ] **Step 3: Workflows parse and gates are wired**

```bash
ruby -ryaml -e 'YAML.load_file(".github/workflows/release.yml"); YAML.load_file(".github/workflows/ci.yml"); puts "both parse"'
grep -c "steps.signing.outputs.enabled" .github/workflows/release.yml   # expect >= 3
grep -n "if:.*secrets\." .github/workflows/*.yml || echo "no secrets in any if:"
```

- [ ] **Step 4: Cask**

```bash
brew style --cask Casks/maccleaner.rb && ruby -c Casks/maccleaner.rb
grep -n 'MacCleaner-v#{version}' Casks/maccleaner.rb   # the v must appear twice in the URL
```

- [ ] **Step 5: install.sh in a sandbox** (never the real home) — fresh install and existing-user upgrade, as in Task 2 Steps 4–5. Then confirm the real environment is untouched: `~/.zshrc` unchanged, `~/mac-cleaner` untouched, and `launchctl list | grep -c maccleaner` still reports 2.

- [ ] **Step 6: Engine unaffected** — `python3 cleaner.py --version` → 2.4.0; `python3 cleaner.py schedule status` (read-only) still reports the two real loaded agents; `python3 cleaner.py --json | head -3` still emits scan JSON (the v1 menu bar contract).

- [ ] **Step 7:** `git status --porcelain` clean of runtime artifacts. Commit any fixes as `fix: <what>`.

---

## Self-Review Notes

- **Spec coverage:** §1 signing pipeline → Task 3 + the secrets documentation in Task 4; §2 tap/cask → Task 4 (cask in-repo; tap publication documented, deliberately deferred per the 2026-08-08 decision); §3 completions → Tasks 1–2; §4 testing/docs → per-task plus Tasks 5–6.
- **Two spec amendments**, both recorded in Task 5 and the Global Constraints: the target version is **2.4.0** (the spec said 2.2.0, written before 2.2 and 2.3 shipped), and the **public tap is not created** this round.
- **Type/name consistency:** the signing gate is `steps.signing.outputs.enabled == 'true'` in all three places; secret names are `MACOS_CERTIFICATE_P12`, `MACOS_CERTIFICATE_PWD`, `APPLE_ID`, `APPLE_TEAM_ID`, `NOTARY_PASSWORD` in both the workflow and `RELEASING.md`; completion files are `completions/_maccleaner` and `completions/maccleaner.bash` everywhere.
- **Test-count checkpoints** are expectations, not gates — the gate is `OK` with zero failures. The suite is 182 before Task 1 and gains 5 there.
- **The highest-risk single detail** is `install.sh`'s guard string (Task 2). The alias guard greps bare `mac-cleaner`, which every existing install already matches; Task 2 Step 5's second sandbox exists specifically to catch a regression there, and it is the one test in this plan that would silently pass if written carelessly.
- **Not touched deliberately:** `app/build.sh` (its ad-hoc signature is correctly replaced at release time and it is the last mutation to the bundle); the engine's JSON contract; dependabot PRs #2 and #3, which also edit `release.yml` — flag the conflict risk to the maintainer rather than merging their PRs unasked.
