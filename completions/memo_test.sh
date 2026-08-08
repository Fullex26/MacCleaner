#!/bin/bash
# Drive the real bash completion entry point (_maccleaner) twice within ONE
# process and report how many times the (failing) fake engine subprocess
# actually ran. Proves _maccleaner_values's Layer 1 "already tried this
# shell" memo really persists across calls -- it only does if the completion
# call sites invoke it as a plain statement and read the resulting globals,
# never via a command substitution $(...), which forks a subshell and
# discards any variables it sets the instant it exits.
#
# usage: memo_test.sh <completion-file> <engine> <counter-file> [passes]
COMPFILE=$1
ENGINE=$2
COUNTER=$3
PASSES=${4:-2}

: > "$COUNTER"
export MACCLEANER_ENGINE=$ENGINE
export FAKE_FAIL=1
export FAKE_COUNTER=$COUNTER

# shellcheck disable=SC1090
. "$COMPFILE"
rm -f "${TMPDIR:-/tmp}"/.maccleaner-comp-* 2>/dev/null

_split_line() {
    local line=$1 out=() tok
    line=${line//=/ = }
    for tok in $line; do out[${#out[@]}]=$tok; done
    case $line in
        *' ') out[${#out[@]}]="" ;;
    esac
    COMP_WORDS=("${out[@]}")
    COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))
}

for (( i = 0; i < PASSES; i++ )); do
    _split_line "maccleaner config enable "
    COMP_LINE="maccleaner config enable "
    COMP_POINT=${#COMP_LINE}
    COMPREPLY=()
    _maccleaner
done

wc -l < "$COUNTER" | tr -d ' '
