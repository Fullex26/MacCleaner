#!/usr/bin/env zsh
# usage: buffer_capture.zsh <compdir> <buffer...>   -> prints the resulting
# command-line text after a real TAB-completion pass.
#
# capture.zsh (candidate capture) intercepts compadd itself, so it reports
# the raw candidate set a completion function computed. That's the wrong
# tool for bugs that live in what zle actually inserts onto the line once a
# unique match is chosen -- e.g. _values silently appending a stray "-S ="
# suffix, or an aliased command never reaching a completion function at all.
# This script drives a real interactive zsh (via zpty) exactly like a user
# typing at a prompt: type the buffer, press Tab, read back what's really on
# the command line.
emulate -L zsh
zmodload zsh/zpty

local compdir=${1:A}; shift
local buffer="$*"
local tmp=$(mktemp -d)

export COMPTEST_DIR=$compdir COMPTEST_TMP=$tmp

# ZDOTDIR points at a throwaway dir (not the repo's zdot/) that just sources
# the real, checked-in fixture -- see capture.zsh for why.
local real_zdot=${0:A:h}/zdot
local fresh_zdot=$tmp/zdot
mkdir -p $fresh_zdot
print -r -- "source '$real_zdot/.zshrc'" > $fresh_zdot/.zshrc
export ZDOTDIR=$fresh_zdot

zpty -b zt zsh -i

local acc="" line i
for (( i=0; i<100; i++ )); do
  if zpty -r -t zt line; then acc+=$line; [[ $acc == *@@RDY@@* ]] && break; fi
  sleep 0.05
done
[[ $acc == *@@RDY@@* ]] || { print -ru2 -- "harness: never saw prompt"; zpty -d zt; rm -rf $tmp; return 1 }

zpty -w -n zt "$buffer"$'\t'

# Give the completion widget (which may shell out to the engine) time to run,
# then drain whatever the pty echoed back once it goes quiet.
sleep 1
local out="" got
for (( i=0; i<40; i++ )); do
  if zpty -r -t zt got 2>/dev/null; then out+=$got; else break; fi
done

zpty -d zt 2>/dev/null
rm -rf $tmp

# zle's redraw re-echoes the full line; the pty's last non-blank line is the
# resulting command buffer.
local -a lines
lines=( "${(@f)out}" )
local result="" l
for l in $lines; do
  [[ -n ${l//[[:space:]]/} ]] && result=$l
done
print -r -- "$result"
