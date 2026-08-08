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
eq "targets 1st value"    "78" "$(B 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "targets 2nd value"    "77" "$(B 'maccleaner clean --targets npm-cache,' | wc -l | tr -d ' ')"
eq "targets 3rd value"    "76" "$(B 'maccleaner clean --targets npm-cache,pip-cache,' | wc -l | tr -d ' ')"
eq "comma keeps prefix"   "npm-cache,pip-cache" "$(B 'maccleaner clean --targets npm-cache,pip')"
eq "equals form"          "npm-cache,pip-cache" "$(B 'maccleaner clean --targets=npm-cache,pip')"
eq "no dupes offered"     "0"  "$(B 'maccleaner clean --targets npm-cache,' | grep -cx 'npm-cache')"
eq "config actions"       "show path enable disable set" "$(B 'maccleaner config ' | grep -v '^--' | tr '\n' ' ' | sed 's/ $//')"
eq "config enable cats"   "20" "$(B 'maccleaner config enable ' | wc -l | tr -d ' ')"
eq "engine down -> static" "20" "$(env MACCLEANER_ENGINE=/nope HOME=/tmp/nohome bash "$HERE/bashtest.sh" "$HERE/maccleaner.bash" 'maccleaner config enable ' | wc -l | tr -d ' ')"

echo "Tier 2b: mclean/mpreview/mreport alias completion (each bakes in a subcommand)"
eq "alias mclean flags"    "--yes --targets --category --min-size --trash --dry-run --notify --json --help" "$(B 'mclean ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mclean --y"      "--yes " "$(B 'mclean --y')"
eq "alias mpreview flags"  "--category --min-size --all --json --help" "$(B 'mpreview ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mpreview --a"    "--all " "$(B 'mpreview --a')"
eq "alias mreport flags"   "-n --limit --json --help" "$(B 'mreport ' | tr '\n' ' ' | sed 's/ $//')"
eq "alias mreport --l"     "--limit " "$(B 'mreport --l')"
eq "alias mclean --targets" "78" "$(B 'mclean --targets ' | wc -l | tr -d ' ')"

echo "Tier 3: zsh behaviour (zpty + compadd -O capture)"
Z(){ "$HERE/capture.zsh" "$HERE" "$1"; }
eq "zsh subcommands"      "10" "$(Z 'maccleaner ' | wc -l | tr -d ' ')"
eq "zsh prefix 'sc'"      "scan schedule" "$(Z 'maccleaner sc' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh schedule action"  "monthly off status weekly" "$(Z 'maccleaner schedule ' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh targets 1st"      "78" "$(Z 'maccleaner clean --targets ' | wc -l | tr -d ' ')"
eq "zsh targets 2nd"      "77" "$(Z 'maccleaner clean --targets npm-cache,' | wc -l | tr -d ' ')"
eq "zsh targets 3rd"      "76" "$(Z 'maccleaner clean --targets npm-cache,pip-cache,' | wc -l | tr -d ' ')"
eq "zsh config set keys"  "12" "$(Z 'maccleaner config set ' | wc -l | tr -d ' ')"
eq "zsh engine down"      "20" "$(env MACCLEANER_ENGINE=/nope HOME=/tmp/nohome "$HERE/capture.zsh" "$HERE" 'maccleaner config enable ' | wc -l | tr -d ' ')"

echo "Tier 3b: mclean/mpreview/mreport alias completion (zsh)"
eq "zsh alias mclean flags" "--category --dry-run --help --json --min-size --notify --targets --trash --yes -h" "$(Z 'mclean ' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mclean --y"   "--yes" "$(Z 'mclean --y' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mpreview --a" "--all" "$(Z 'mpreview --a' | tr '\n' ' ' | sed 's/ $//')"
eq "zsh alias mreport --l"  "--limit" "$(Z 'mreport --l' | tr '\n' ' ' | sed 's/ $//')"

echo
echo "passed=$PASS failed=$FAIL"
[ "$FAIL" -eq 0 ]
