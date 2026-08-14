#!/bin/bash
# Completion test suite. Tier 1 = syntax, Tier 2 = bash behaviour, Tier 3 = zsh behaviour.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf '  ok   %s\n' "$1"; }
no(){ FAIL=$((FAIL+1)); printf '  FAIL %s\n     want: %s\n     got:  %s\n' "$1" "$2" "$3"; }
eq(){ [ "$2" = "$3" ] && ok "$1" || no "$1" "$2" "$3"; }

export MACCLEANER_ENGINE=$HERE/fake_engine.py
rm -f "${TMPDIR:-/tmp}"/.maccleaner-comp-* 2>/dev/null

echo "Tier 1: syntax"
zsh  -n "$HERE/_maccleaner"     && ok "zsh -n _maccleaner"     || no "zsh -n _maccleaner" 0 1
bash -n "$HERE/maccleaner.bash" && ok "bash -n maccleaner.bash" || no "bash -n maccleaner.bash" 0 1

echo "Tier 2: bash behaviour (COMPREPLY assertions, runs on bash 3.2)"
B(){ bash "$HERE/bashtest.sh" "$HERE/maccleaner.bash" "$1"; }
eq "subcommands"          "10" "$(B 'maccleaner ' | grep -cv '^--')"
eq "prefix filter 'sc'"   "scan schedule" "$(B 'maccleaner sc' | tr '\n' ' ' | sed 's/ $//')"
eq "schedule actions"     "status weekly monthly off" "$(B 'maccleaner schedule ' | grep -v '^--' | tr '\n' ' ' | sed 's/ $//')"
eq "schedule after action" "--json --help" "$(B 'maccleaner schedule weekly ' | tr '\n' ' ' | sed 's/ $//')"
eq "clean flags"          "9"  "$(B 'maccleaner clean --' | wc -l | tr -d ' ')"
eq "targets 1st value"    "83" "$(B 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "targets 2nd value"    "82" "$(B 'maccleaner clean --targets npm-cache,' | wc -l | tr -d ' ')"
eq "targets 3rd value"    "81" "$(B 'maccleaner clean --targets npm-cache,pip-cache,' | wc -l | tr -d ' ')"
eq "comma keeps prefix"   "npm-cache,pip-cache" "$(B 'maccleaner clean --targets npm-cache,pip')"
eq "equals form"          "npm-cache,pip-cache" "$(B 'maccleaner clean --targets=npm-cache,pip')"
eq "no dupes offered"     "0"  "$(B 'maccleaner clean --targets npm-cache,' | grep -cx 'npm-cache')"
eq "projects --targets is freeform, not cleanup ids" "0" "$(B 'maccleaner projects --targets ' | grep -cx 'npm-cache')"
eq "projects --targets freeform w/ prefix"           "0" "$(B 'maccleaner projects --targets npm' | grep -cx 'npm-cache')"
eq "config actions"       "show path enable disable set" "$(B 'maccleaner config ' | grep -v '^--' | tr '\n' ' ' | sed 's/ $//')"
eq "config enable cats"   "23" "$(B 'maccleaner config enable ' | wc -l | tr -d ' ')"
eq "engine down -> static" "23" "$(env MACCLEANER_ENGINE=/nope HOME=/tmp/nohome bash "$HERE/bashtest.sh" "$HERE/maccleaner.bash" 'maccleaner config enable ' | wc -l | tr -d ' ')"

echo "Tier 2b: mclean/mpreview/mreport alias completion (each bakes in a subcommand)"
eq "alias mclean flags"    "--yes --targets --category --min-size --trash --dry-run --notify --json --help" "$(B 'mclean ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mclean --y"      "--yes " "$(B 'mclean --y')"
eq "alias mpreview flags"  "--category --min-size --all --json --help" "$(B 'mpreview ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mpreview --a"    "--all " "$(B 'mpreview --a')"
eq "alias mreport flags"   "-n --limit --json --help" "$(B 'mreport ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mreport --l"     "--limit " "$(B 'mreport --l')"
eq "alias mclean --targets" "83" "$(B 'mclean --targets ' | wc -l | tr -d ' ')"

echo "Tier 3: zsh behaviour (zpty + compadd -O capture)"
Z(){ "$HERE/capture.zsh" "$HERE" "$1"; }
eq "zsh subcommands"      "10" "$(Z 'maccleaner ' | wc -l | tr -d ' ')"
eq "zsh prefix 'sc'"      "scan schedule" "$(Z 'maccleaner sc' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh schedule action"  "monthly off status weekly" "$(Z 'maccleaner schedule ' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh targets 1st"      "83" "$(Z 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "zsh targets 2nd"      "82" "$(Z 'maccleaner clean --targets npm-cache,' | wc -l | tr -d ' ')"
eq "zsh targets 3rd"      "81" "$(Z 'maccleaner clean --targets npm-cache,pip-cache,' | wc -l | tr -d ' ')"
eq "zsh config set keys"  "15" "$(Z 'maccleaner config set ' | wc -l | tr -d ' ')"
eq "zsh engine down"      "23" "$(env MACCLEANER_ENGINE=/nope HOME=/tmp/nohome "$HERE/capture.zsh" "$HERE" 'maccleaner config enable ' | wc -l | tr -d ' ')"

echo "Tier 3b: mclean/mpreview/mreport alias completion (zsh)"
eq "zsh alias mclean flags" "--category --dry-run --help --json --min-size --notify --targets --trash --yes -h" "$(Z 'mclean ' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mclean --y"   "--yes" "$(Z 'mclean --y' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mpreview --a" "--all" "$(Z 'mpreview --a' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mreport --l"  "--limit" "$(Z 'mreport --l' | tr '\n' ' ' | sed 's/ $//')"

# Everything above is a zero- or single-word tail after the alias name, which
# the old "${words[2,-1]}" splice (missing the (@) flag) handled fine -- it
# only breaks once the tail has 2+ words, because that's when zsh's implicit
# join-to-scalar actually loses structure. These reproduce the parity numbers
# checked by hand against the non-alias path (`maccleaner clean --targets ...`).
eq "zsh alias mclean --targets (multi-word tail)" \
   "82" "$(Z 'mclean --targets npm-cache,' | wc -l | tr -d ' ')"
eq "zsh alias mclean --yes --targets (3-word tail)" \
   "82" "$(Z 'mclean --yes --targets npm-cache,' | wc -l | tr -d ' ')"
eq "zsh non-alias parity for the same case" \
   "82" "$(Z 'maccleaner clean --targets npm-cache,' | wc -l | tr -d ' ')"

echo "Tier 3c: real inserted text (buffer_capture drives an actual TAB press,"
echo "not just the candidate set -- this is what catches C1/I2-class bugs)"
Buf(){ "$HERE/buffer_capture.zsh" "$HERE" "$1"; }
# $(...) strips trailing NEWLINES only, never trailing spaces -- a unique
# zsh completion match genuinely inserts a trailing space (so the next word
# can be typed immediately), so the expected values below intentionally
# carry one where real zsh would add it.
eq "buffer: alias maccleaner completes a real subcommand (not a filename)" \
   "maccleaner clean " "$(Buf 'maccleaner clea')"
eq "buffer: alias mclean --y completes to a real flag" \
   "mclean --yes " "$(Buf 'mclean --y')"
eq "buffer: --targets insertion has no stray '=' (_values id:desc bug)" \
   "maccleaner clean --targets npm-cache," "$(Buf 'maccleaner clean --targets npm-ca')"
# _values -s ',' deliberately appends the separator after a completed value
# so the user can keep typing more list items -- a trailing comma here is
# correct zsh behaviour, not the stray "=" bug this tier exists to catch.
eq "buffer: --category insertion has no stray '=' (trailing ',' is _values -s ',' working as designed)" \
   "maccleaner scan --category xcode," "$(Buf 'maccleaner scan --category xco')"

echo "Tier 3d: bash Layer-1 memo really persists across calls within one process"
echo "(a broken-but-present engine must cost exactly ONE subprocess call, not one per TAB)"
COUNTER=$(mktemp)
bash "$HERE/memo_test.sh" "$HERE/maccleaner.bash" "$HERE/fake_engine.py" "$COUNTER" 3 >/dev/null
eq "memo: failing engine called once across 3 completion passes" "1" "$(cat "$COUNTER")"
rm -f "$COUNTER"

echo "Tier 4: real engine (categories --json is the actual completion data source)"
REAL_ENGINE="$HERE/../cleaner.py"
RB(){ env MACCLEANER_ENGINE="$REAL_ENGINE" bash "$HERE/bashtest.sh" "$HERE/maccleaner.bash" "$1"; }
RZ(){ env MACCLEANER_ENGINE="$REAL_ENGINE" "$HERE/capture.zsh" "$HERE" "$1"; }
rm -f "${TMPDIR:-/tmp}"/.maccleaner-comp-* 2>/dev/null
eq "real engine bash: --targets returns real target ids" \
   "1" "$(RB 'maccleaner clean --targets ' | grep -cx 'npm-cache')"
# The live target count has a static floor of 83 (v2.8) but the `logs`
# category adds one dynamic target per ~/Library/Logs folder over the size
# threshold, so the exact count is environment-dependent. Assert the floor,
# not equality — an == here flakes on any dev machine with a fat log dir.
ge(){ [ "$3" -ge "$2" ] 2>/dev/null && ok "$1" || no "$1" ">=$2" "$3"; }
ge "real engine bash: --targets count reaches the static floor" \
   "83" "$(RB 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "real engine bash: --category reflects the live engine, not the static list" \
   "1" "$(RB 'maccleaner config enable ' | grep -cx 'xcode')"
eq "real engine zsh: --targets returns real target ids" \
   "1" "$(RZ 'maccleaner clean --targets ' | grep -cx 'npm-cache')"
ge "real engine zsh: --targets count reaches the static floor" \
   "83" "$(RZ 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "real engine zsh: --category reflects the live engine, not the static list" \
   "1" "$(RZ 'maccleaner config enable ' | grep -cx 'xcode')"
rm -f "${TMPDIR:-/tmp}"/.maccleaner-comp-* 2>/dev/null

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
