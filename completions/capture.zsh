#!/usr/bin/env zsh
# usage: capture.zsh <compdir> <buffer...>   -> prints completion candidates
emulate -L zsh
zmodload zsh/zpty

local compdir=${1:A}; shift
local buffer="$*"
local tmp=$(mktemp -d); : > $tmp/out

export COMPTEST_DIR=$compdir COMPTEST_TMP=$tmp
export ZDOTDIR=${0:A:h}/zdot

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
