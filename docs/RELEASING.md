# Releasing MacCleaner

This is the tracked, canonical release procedure. A `maccleaner-release` skill
also exists at `.claude/skills/maccleaner-release/SKILL.md`, but `.claude/` is
gitignored (see `.gitignore`), so that skill is invisible to CI, to code
review, and to anyone who clones the repo on another machine. This document
is the source of truth; the skill should track it, not the other way around.

It covers: the five signing secrets, what ships when they're absent, the
release steps (including a step the skill currently omits), publishing the
Homebrew tap once notarization is live, bumping the cask after each release,
which `brew audit` invocation is the actual correctness gate, and (new in
v2.6.0) the Sparkle appcast — the sixth secret, the key-correspondence gate,
the stable feed URL, and the framework signing order.

## 1. Signing secrets

`.github/workflows/release.yml` builds the app ad-hoc signed first (that is what
`app/build.sh` does, and it is all a dev build needs), then — only if
`MACOS_CERTIFICATE_P12` is present — re-signs it with a `Developer ID
Application` certificate and submits it to Apple's notary service. Five
repository secrets (Settings → Secrets and variables → Actions) drive this:

| Secret | Shape | Notes |
|---|---|---|
| `MACOS_CERTIFICATE_P12` | Base64 of the exported `.p12` file | `base64 -i cert.p12 \| pbcopy`, then paste as the secret value |
| `MACOS_CERTIFICATE_PWD` | The password you set when exporting the `.p12` from Keychain Access | Not your Apple ID password |
| `APPLE_ID` | The Apple ID email used for notarization | Must belong to the same team as the certificate |
| `APPLE_TEAM_ID` | 10-character Developer Team ID (e.g. `ABCDE12345`) | The workflow cross-checks this against the team id embedded in the signing identity and fails loudly on a mismatch, before ever calling `notarytool` |
| `NOTARY_PASSWORD` | An **app-specific password** generated at appleid.apple.com (Sign-In and Security → App-Specific Passwords) | **Not** the Apple ID account password — that will not work with `notarytool --password` |

`xcrun notarytool` also supports authenticating with an App Store Connect API
key (`--key`/`-k`, `--issuer`/`-i`, `--key-id`/`-d`) instead of an Apple ID +
app-specific password. That form is a valid alternative in general, but it is
**not** what this workflow implements — `release.yml` only ever calls
`notarytool submit --apple-id ... --team-id ... --password ...`. If you want
API-key auth instead, the workflow itself needs to change; setting different
secrets alone won't switch the auth mode.

Exporting the certificate:

```bash
# From Keychain Access: right-click the "Developer ID Application: <Name>
# (<TEAMID>)" certificate → Export → Personal Information Exchange (.p12),
# set an export password, save as cert.p12. Then:
base64 -i cert.p12 | pbcopy
# Paste into the MACOS_CERTIFICATE_P12 secret value, then delete cert.p12.
```

### What the signing step actually does

Reading `.github/workflows/release.yml` end to end (not an idealized
description):

- A `Detect signing credentials` step probes only `MACOS_CERTIFICATE_P12` via
  `env:` (secrets can't be referenced directly in an `if:`) and publishes an
  `enabled` boolean output. That single secret decides the unsigned/signed
  fork for the rest of the job.
- The `Sign, notarize, and staple` step then runs only when `enabled ==
  'true'`, and its **first action** is to check all five secrets are
  non-empty (`env:` maps an unset secret to `""`, so `set -u` alone can't
  catch a partially-configured setup). If any of the four remaining secrets
  is missing, it fails immediately with `::error::` naming which ones —
  before touching the keychain or re-signing anything — rather than failing
  later with an opaque `notarytool` auth error.
- It imports the certificate into a throwaway keychain scoped to the runner,
  verifies a `Developer ID Application` identity is present (with a clear
  error path that also explains an untrusted/expired cert produces the exact
  same "no identity found" message), and cross-checks the team id embedded in
  that identity against the `APPLE_TEAM_ID` secret.
- It code-signs with `--options runtime --timestamp` (hardened runtime,
  required for notarization), verifies the signature, zips the `.app` with
  `ditto`, and submits it with `xcrun notarytool submit ... --timeout 30m
  --wait`. The `--timeout 30m` bounds the wait client-side — Apple's notary
  service has no documented SLA, and without a bound a service backlog would
  look like a silently hung 360-minute GitHub Actions job. A client-side
  timeout does not lose the submission; Apple keeps processing it.
- If the result isn't `"Accepted"`, it fetches `xcrun notarytool log` for the
  submission before failing, so the job log shows the actual rejection reason
  instead of just "cannot staple" (which is what happens further down when a
  non-`Accepted` submission has no ticket to staple).
- On success it staples the ticket to the `.app` (not the zip), validates the
  staple, and runs `spctl -a -vvv -t exec` as the definitive "will Gatekeeper
  accept this" check.
- A separate `Remove signing keychain` step (`if: always() && ...`) deletes
  the throwaway keychain regardless of whether signing succeeded.

## 2. What happens without the secrets

If `MACOS_CERTIFICATE_P12` is absent, the `Sign, notarize, and staple` step
is skipped entirely and the workflow ships exactly what it ships today: an
ad-hoc-signed `.app` built by `app/build.sh`. The `Generate release notes`
step checks `steps.signing.outputs.enabled` and, when it's not `"true"`,
keeps the existing Gatekeeper caveat in the release notes ("macOS may block
the app... Right-click → Open → confirm").

There is nothing to change in the workflow when the secrets eventually get
added to the repo — the probe step re-evaluates on every run, so the very
next tag push after the secrets are configured produces a signed, notarized
release automatically.

## 3. Release steps

These match the `maccleaner-release` skill, with one addition the skill's
version-sync list currently omits (see the note below).

**Two rules carried over from the skill:** release only from `main` (the tag
must point at a commit that is on `main`, since `release.yml` builds whatever
the tag points at), and tags are `vX.Y.Z` — a tag containing a hyphen (e.g.
`v2.4.0-rc1`) is published as a **prerelease** automatically.

1. **Determine the version** (e.g. `2.4.0`, no `v` prefix in the files
   themselves — the `v` prefix belongs only to the git tag and, downstream of
   that, the release-asset filenames).

2. **Sync version strings.** All of the following must match:
   - `VERSION = "X.Y.Z"` in `cleaner.py`
   - `CFBundleShortVersionString` **and** `CFBundleVersion` in
     `app/Info.plist`
   - **`AGENTS.md`** — omitted from the skill's current list, which is why it
     drifts. As of this writing `AGENTS.md` embeds the version in two
     distinct places that must both change:
     - The intro paragraph: `"... Current version: 2.3.0."`
     - Eleven `"version": "2.3.0"` lines inside the documented JSON response
       examples (`scan`, `clean`, `projects`, `report`, `doctor`,
       `categories`, `schedule status/weekly/monthly/off`, etc.)

     Verify the exact count and locations before editing, since it changes
     release to release as sections are added:
     ```bash
     # Derive the version already in the tree rather than typing it — a literal
     # placeholder here would match nothing and silently look like "no drift".
     OLD=$(python3 -c 'import re;print(re.search(r"VERSION = \"([^\"]+)\"",open("cleaner.py").read()).group(1))')
     grep -n "\"version\": \"$OLD\"" AGENTS.md   # the JSON examples
     grep -n "Current version:" AGENTS.md        # the intro prose
     ```
     **Do not** touch the separate `(new in 2.3.0)` / `New in 2.3.0.`
     feature-provenance callouts scattered through `AGENTS.md` (e.g. next to
     `schedule`, `Schedule paths`, `MACCLEANER_LAUNCH_AGENTS_DIR`) — those
     document *when a feature shipped* and must stay pinned to that
     historical version regardless of what the current release is.

3. **Run the test suite:**
   ```bash
   python3 -m unittest discover -s tests
   ```

4. **Rebuild the committed app bundle** so it ships the current engine and
   version:
   ```bash
   bash app/build.sh && rm -rf MacCleaner.app && cp -R build/MacCleaner.app MacCleaner.app
   ```

5. **Update `CHANGELOG.md`** — move `[Unreleased]` items into a new
   `## [VERSION] — DATE` section; leave `[Unreleased]` empty for what comes
   next.

6. **Commit, tag, push:**
   ```bash
   git add CHANGELOG.md cleaner.py app/Info.plist AGENTS.md MacCleaner.app
   git commit -m "chore: release X.Y.Z"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
   The tag push triggers `.github/workflows/release.yml`, which re-runs the
   tests, builds the universal app from source, optionally signs/notarizes
   (§1–2), and attaches `MacCleaner-vX.Y.Z-macos-universal.zip` and
   `MacCleaner-vX.Y.Z-cli.tar.gz` to the GitHub Release.

## 4. Publishing the Homebrew tap

**Not done yet, deliberately.** Homebrew 6 removed `--no-quarantine` and
never had a `quarantine: false` cask stanza, so an unsigned cask install is
Gatekeeper-blocked with no supported workaround — publishing a tap before
releases are notarized would just ship a cask that fails on first launch for
every user. `Casks/maccleaner.rb` lives in this repo, validated and
version-controlled, so it's one `cp` away the day a signed/notarized release
exists (§1–2 above).

When that day comes:

1. Create `github.com/Fullex26/homebrew-tap` (public repo). The
   `homebrew-` prefix is **required** — it's what makes the short form
   `Fullex26/tap` resolve.
2. Clone it and create the `Casks` directory yourself —
   `brew tap-new Fullex26/tap` scaffolds a tap skeleton but does **not**
   create `Casks/`:
   ```bash
   git clone https://github.com/Fullex26/homebrew-tap.git
   mkdir -p homebrew-tap/Casks
   cp Casks/maccleaner.rb homebrew-tap/Casks/maccleaner.rb
   cd homebrew-tap && git add Casks/maccleaner.rb && git commit -m "add maccleaner cask" && git push
   ```
3. Document (in the release notes / README) the **only** install command
   that actually works under Homebrew 6:
   ```bash
   brew install --cask Fullex26/tap/maccleaner
   ```
   This fully-qualified one-liner auto-taps and auto-trusts in one step. The
   classic two-step form —
   ```bash
   brew tap Fullex26/tap
   brew install --cask maccleaner
   ```
   — hard-fails on tap trust under Homebrew 6. Don't recommend it.

## 5. Bumping the cask after a release

`release.yml`'s asset upload happens asynchronously relative to the tag push
finishing, so poll for the asset before hashing it — don't assume it exists
the instant `git push origin vX.Y.Z` returns.

```bash
VERSION=X.Y.Z   # no leading "v"

# Poll until the asset shows up in the release:
gh release view "v$VERSION" --repo Fullex26/MacCleaner --json assets \
  --jq '.assets[].name'

# Download and hash it:
curl -fsSL -o /tmp/mc.zip \
  "https://github.com/Fullex26/MacCleaner/releases/download/v$VERSION/MacCleaner-v$VERSION-macos-universal.zip"
shasum -a 256 /tmp/mc.zip
```

Note the URL shape: the tag path segment (`v$VERSION`) and the asset
filename (`MacCleaner-v$VERSION-...`) **both** carry the `v` — that's not a
typo to "fix". `release.yml` derives its `VERSION` output directly from
`github.ref_name` (the raw pushed tag, e.g. `v2.4.0`) and uses it verbatim in
the packaged zip's filename, so the asset really is named
`MacCleaner-v2.4.0-macos-universal.zip`. This is also why `Casks/maccleaner.rb`'s
`url` stanza is written as
`.../download/v#{version}/MacCleaner-v#{version}-macos-universal.zip` with
`version` holding the bare `"2.4.0"` — the template supplies one `v` for the
tag segment and one for the filename segment, matching what the workflow
actually publishes (and staying consistent with the already-published
v2.0.0 asset names).

Then, in the tap's clone:

```ruby
version "X.Y.Z"
sha256 "<the shasum output above>"
```

Remove the placeholder comment above `sha256` once a real hash is in place.
Commit and push to `Fullex26/homebrew-tap`. No CI write-token touches the
tap — this step runs locally under the maintainer's own credentials.

`Casks/maccleaner.rb` also sets `auto_updates true` — since v2.6.0 the app
updates itself in place via Sparkle (see §7 below), so Homebrew shouldn't
expect `brew upgrade --cask` to be the only way a user's installed copy ever
moves forward. `auto_updates true` doesn't change what this bump procedure
does (you still bump `version`/`sha256` here so a *fresh* `brew install`
pulls the current release); it only tells `brew outdated --cask` and `brew
upgrade --cask` not to nag about a version drift that Sparkle itself is
already handling for existing installs. Leave it set on every future bump.

## 6. Why `brew audit --cask --new` is not the gate

`brew audit --cask --new` (run from inside a tap — both `audit` and `style`
reject a bare file path: `style Casks/maccleaner.rb` errors with "Homebrew
requires casks to be in a tap", `audit Casks/maccleaner.rb` errors with
"Calling `brew audit [path ...]` is disabled!"; both need
`<user>/<tap>/maccleaner` instead) currently reports failures that have
nothing to do with whether the cask itself is correct:

- It tries to download the release asset to inspect it, and 404s, because no
  `v2.4.0` release exists yet (the version is a placeholder — see the
  comment above `sha256` in the cask file itself, and §5 above).
- It flags the pinned `version "2.4.0"` as disagreeing with what `livecheck`
  resolves from GitHub's latest release (`2.0.0`, the last real tag) — again
  an artifact of the version being a forward-looking placeholder, not a
  cask defect.
- It also flags the `verified:` parameter on the `url` stanza as deprecated.
  This check (`audit_unnecessary_verified` in Homebrew's own `cask/audit.rb`)
  is explicitly gated on `new_cask?` — it only fires under `--new`, and the
  DSL-level deprecation warning for `verified` is still commented out
  (`# odeprecated ...`) in this Homebrew version, i.e. the parameter is fully
  supported and non-deprecated in practice. It'll stop firing once the tap
  audits this cask as an established (non-"new") entry.

None of these three indicate a problem with the cask's syntax, stanza order,
or logic. The check that actually validates correctness — style conformance,
stanza structure, required fields — is the plain form:

```bash
# Both require the cask to resolve through a tap name, not a bare path —
# see the workaround below for validating locally without a real tap.
brew audit --cask <user>/<tap>/maccleaner   # passes clean
brew style --cask <user>/<tap>/maccleaner   # passes clean
```

To validate `Casks/maccleaner.rb` locally without publishing anything, tap a
throwaway local directory and untap it when done — never leave a tap behind
on a dev machine for this:

```bash
mkdir -p /tmp/scratch-tap/Casks && cp Casks/maccleaner.rb /tmp/scratch-tap/Casks/
git -C /tmp/scratch-tap init -q && git -C /tmp/scratch-tap add -A \
  && git -C /tmp/scratch-tap commit -q -m "scratch"
brew tap yourname/scratch /tmp/scratch-tap
brew style --cask yourname/scratch/maccleaner
brew audit --cask yourname/scratch/maccleaner
brew untap yourname/scratch
rm -rf /tmp/scratch-tap
```

Use `brew audit --cask --new` (and expect the download/version/`verified`
noise) only once an actual tagged release exists to audit against, not as a
merge gate on this branch.

## 7. The Sparkle appcast

New in v2.6.0. Installed apps check `SUFeedURL` (`app/Info.plist`) —
`https://github.com/Fullex26/MacCleaner/releases/latest/download/appcast.xml`
— on a daily background timer and whenever the user picks "Check for
Updates…" in Settings. `release.yml` generates and publishes that feed as
part of every tag push, but only when signing is fully set up; there is
nothing to configure beyond the one secret below once §1's five signing
secrets are already in place.

### The secret

| Secret | Shape | Notes |
|---|---|---|
| `SPARKLE_ED_PRIVATE_KEY` | Base64 of a 32-byte Ed25519 seed (44 base64 characters) | Generate with `openssl genpkey -algorithm ed25519`, extract the raw 32-byte seed, base64-encode it. Corresponds to the public key already committed in `app/Info.plist`'s `SUPublicEDKey` (`pozlfRIcd9s0JQwteBAhzxg8A2Ex0/YeZK3su9IDe9k=`) |

`release.yml`'s `Detect signing credentials` step probes this the same way
it probes `MACOS_CERTIFICATE_P12` — via `env:`, publishing a
`sparkle_key_present` boolean output, since secrets can't be referenced
directly in a step `if:`. The `Generate appcast` step only runs when
**both** `steps.signing.outputs.enabled == 'true'` (the app was actually
Developer-ID signed and notarized) **and** `sparkle_key_present == 'true'`.
An ad-hoc-signed build never gets an appcast entry — Sparkle would just
reject its signature on every install, so there's no point advertising an
update nobody could apply.

### The key-correspondence gate

Before generating anything, the step re-derives the Ed25519 **public** key
from the `SPARKLE_ED_PRIVATE_KEY` seed (pure-Python stdlib, no `openssl`
Ed25519 support on the macOS runner's system LibreSSL) and compares it,
byte for byte, against `SUPublicEDKey` read straight out of the just-built
`build/MacCleaner.app/Contents/Info.plist` via `PlistBuddy`. If they don't
match, the job fails immediately (`::error::`) — before signing the zip or
touching the GitHub Release — instead of silently publishing an appcast
whose signature no installed app could ever verify. This is the check that
would catch a rotated `SPARKLE_ED_PRIVATE_KEY` secret that `app/Info.plist`
wasn't updated to match, or vice versa; either half changing without the
other is a shipped-update outage, not a cosmetic mismatch.

### The `releases/latest/download/appcast.xml` contract

`SUFeedURL` doesn't point at a specific tag — it points at GitHub's
"latest release" redirect (`.../releases/latest/download/<asset>`), which
always resolves to whatever release GitHub currently considers latest
(newest non-prerelease, non-draft tag). `release.yml` uploads `appcast.xml`
as a release asset on every signed, appcast-eligible tag push, so an
installed app's daily check always fetches the newest one without needing
to know its own current version's tag ahead of time. The appcast itself
(`gen_appcast.py`, inline in the workflow) contains exactly one `<item>` —
the version being released — not a rolling history; each new release's
appcast simply supersedes the previous one at that same stable URL. The
`<enclosure>` inside it points at that release's
`MacCleaner-vX.Y.Z-macos-universal.zip` (the same asset §5 hashes for the
cask) and carries the `sparkle:edSignature` produced by `sign_update`
against `SPARKLE_ED_PRIVATE_KEY`. A prerelease tag (one containing `-`,
e.g. `v2.6.0-rc1`) still runs this step — nothing in `release.yml` special-
cases prereleases here — but GitHub's `/releases/latest` redirect skips
prereleases by definition, so a prerelease's appcast is generated and
uploaded yet never actually reached by `SUFeedURL` until a real release
supersedes it.

### Inside-out framework signing

Sparkle ships as `Sparkle.framework`, embedded by `app/build.sh` (checksum-
verified fetch of the pinned upstream release — see the top of that
script). A framework is nested code, and Apple's guidance is to sign nested
code first, then the container, **without** `--deep` (which doesn't
propagate signing options correctly to nested code — this is the same
`--deep` caveat `docs/RELEASING.md` §1 already carries for the outer app,
now sharper because there's something nested to get wrong). The `Sign,
notarize, and staple` step in `release.yml` therefore signs, strictly
bottom-up, before ever touching `MacCleaner.app` itself:

1. Any `.xpc` bundles under `Sparkle.framework/Versions/B/XPCServices/`
2. `Sparkle.framework/Versions/B/Autoupdate` and
   `.../Versions/B/Updater.app`
3. `Sparkle.framework` itself
4. `MacCleaner.app` (the outer bundle, as before)

`xattr -cr` runs on the framework immediately beforehand — a freshly
downloaded/extracted framework can carry resource forks or provenance
extended attributes that `codesign --verify --deep --strict` (and
Gatekeeper) reject as "resource fork, Finder information, or similar
detritus not allowed", and stripping them only at the outer-bundle step
would be too late for the nested pieces. This whole block is gated on
`[ -d "$FRAMEWORK" ]`; a non-CI build (or one where the Sparkle fetch fell
back to `-DSPARKLE_DISABLED` — see `app/build.sh`) simply has nothing to
sign here and proceeds straight to step 4.
