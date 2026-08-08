#!/usr/bin/env zsh
# usage: capture.zsh <compdir> <buffer...>   -> prints completion candidates
emulate -L zsh
zmodload zsh/zpty

local compdir=${1:A}; shift
local buffer="$*"
local tmp=$(mktemp -d); : > $tmp/out

export COMPTEST_DIR=$compdir COMPTEST_TMP=$tmp

# ZDOTDIR points at a throwaway dir (not the repo's zdot/) that just sources
# the real, checked-in fixture -- so compinit's dump file and any other
# ZDOTDIR-relative state lands in $tmp, never in the repo.
local real_zdot=${0:A:h}/zdot
local fresh_zdot=$tmp/zdot
mkdir -p $fresh_zdot
print -r -- "source '$real_zdot/.zshrc'; source '$real_zdot/.zshrc-capture'" > $fresh_zdot/.zshrc
export ZDOTDIR=$fresh_zdot

zpty -b zt zsh -i

# Wait for the shell to reach its first prompt (ZLE is reading by then).
local acc="" line i
for (( i=0; i<100; i++ )); do
  if zpty -r -t zt line; then acc+=$line; [[ $acc == *@@RDY@@* ]] && break; fi
  sleep 0.05
done
[[ $acc == *@@RDY@@* ]] || { print -ru2 -- "harness: never saw prompt"; zpty -d zt; rm -rf $tmp; return 1 }

zpty -w -n zt "$buffer"$'\t'

# Wait for candidates to land (or give up).
for (( i=0; i<60; i++ )); do
  sleep 0.05
  [[ -s $tmp/out ]] && { sleep 0.25; break }
done

zpty -d zt 2>/dev/null
sort -u $tmp/out 2>/dev/null
rm -rf $tmp
