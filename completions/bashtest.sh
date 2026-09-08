#!/bin/bash
# Drive the bash completion function directly and print COMPREPLY.
# usage: bashtest.sh <completion-file> "<command line>"
#   A trailing space in the command line means "start a new empty word".

COMPFILE=$1
LINE=$2

# shellcheck disable=SC1090
. "$COMPFILE"

# Split the line the way readline would: on whitespace, and on the
# COMP_WORDBREAKS characters we actually care about here ("=").
_split_line() {
    local line=$1 out=() tok
    # make "=" its own word, like readline does
    line=${line//=/ = }
    for tok in $line; do out[${#out[@]}]=$tok; done
    case $line in
        *' ') out[${#out[@]}]="" ;;
    esac
    COMP_WORDS=("${out[@]}")
    COMP_CWORD=$(( ${#COMP_WORDS[@]} - 1 ))
}

_split_line "$LINE"
COMP_LINE=$LINE
COMP_POINT=${#LINE}

COMPREPLY=()
_maccleaner
printf '%s\n' "${COMPREPLY[@]}"
