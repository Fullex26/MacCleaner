# bash completion for maccleaner. Written to run on macOS's stock bash 3.2
# (no compopt, no associative arrays, no ${var,,}) as well as bash 4/5.
# shellcheck shell=bash

_maccleaner_engine() {
    local cand
    for cand in "$MACCLEANER_ENGINE" "$HOME/mac-cleaner/cleaner.py"; do
        if [ -n "$cand" ] && [ -r "$cand" ]; then printf '%s' "$cand"; return 0; fi
    done
    return 1
}

# Run a command with a wall-clock cap. macOS ships no timeout(1); perl is in
# the base system and its alarm(2) does the job.
_maccleaner_run_capped() {
    local secs=$1; shift
    if command -v perl >/dev/null 2>&1; then
        perl -e 'alarm shift; exec @ARGV' "$secs" "$@" 2>/dev/null
    else
        "$@" 2>/dev/null
    fi
}

_MACCLEANER_STATIC_CATEGORIES="xcode docker node python caches logs homebrew go
rust ruby cocoapods gradle maven ai ide browsers system flutter php vms tmp simulators leftovers"

# Populate $_MACCLEANER_CATS / $_MACCLEANER_TGTS for $1 = categories|targets.
# Layer 1: per-shell memo. Layer 2: disk cache keyed on engine mtime.
# Layer 3: the tool itself. Layer 4: a static fallback.
#
# Callers MUST invoke this as a plain statement and read the global
# afterward -- never wrap it in a command substitution $(...). An earlier
# version returned its result on stdout and every caller did exactly that,
# which forks a subshell per call; the Layer 1 "already tried this shell"
# memo (_MACCLEANER_CATS_LOADED / _MACCLEANER_TGTS_LOADED) was being set
# inside that subshell and thrown away the instant it exited, so it never
# actually persisted across calls. The comment here used to claim "one
# subprocess per shell, not one per keypress" -- false: when the engine is
# present but failing (crashing, or just slow), Layer 2's disk cache is never
# written either (see below), so every single TAB press paid the full
# 2-second perl alarm cap, forever, for the life of the shell.
_maccleaner_values() {
    local kind=$1 engine raw ids cache stamp

    # Layer 1 -- memo, set even on failure so a broken engine costs one
    # subprocess per shell rather than one per keypress. Only effective now
    # that this function is called directly (see comment above).
    case $kind in
        categories) [ -n "$_MACCLEANER_CATS_LOADED" ] && return 0 ;;
        targets)    [ -n "$_MACCLEANER_TGTS_LOADED" ] && return 0 ;;
    esac

    engine=$(_maccleaner_engine)

    # Layer 2 -- disk cache, shared across shells, invalidated by engine mtime.
    if [ -n "$engine" ]; then
        stamp=$(/usr/bin/stat -f %m "$engine" 2>/dev/null || stat -c %Y "$engine" 2>/dev/null)
        cache="${TMPDIR:-/tmp}/.maccleaner-comp-${kind}-${stamp}-$(id -u)"
        if [ -s "$cache" ]; then
            ids=$(cat "$cache")
        else
            # There is no `__complete` subcommand; the real data source is
            # `categories --json` (categories with nested targets, 78 targets
            # across 20 categories). The capped call only fetches the JSON --
            # reshaping it into "id<TAB>description" lines happens in a
            # second, uncapped python3 call, since it only parses text we
            # already have and can't hang.
            raw=$(_maccleaner_run_capped 4 python3 "$engine" categories --json 2>/dev/null)
            if [ -n "$raw" ]; then
                raw=$(printf '%s' "$raw" | python3 -c '
import json, sys
kind = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
rows = []
if kind == "targets":
    for cat in data.get("categories", []):
        for t in cat.get("targets", []):
            rows.append((t.get("id", ""), t.get("label", "")))
else:
    for cat in data.get("categories", []):
        rows.append((cat.get("name", ""), cat.get("description", "")))
for i, d in rows:
    print("%s\t%s" % (i, d.replace("\t", " ")))
' "$kind" 2>/dev/null)
            fi
            # engine prints "id<TAB>description"; completion only needs the id
            ids=$(printf '%s\n' "$raw" | cut -f1 | tr '\n' ' ')
            case $ids in
                ' '|'') ids="" ;;
                *) printf '%s' "$ids" > "$cache" 2>/dev/null ;;
            esac
        fi
    fi

    # Layer 4 -- fallback.
    if [ -z "$ids" ] && [ "$kind" = categories ]; then
        ids=$_MACCLEANER_STATIC_CATEGORIES
    fi

    case $kind in
        categories) _MACCLEANER_CATS=$ids; _MACCLEANER_CATS_LOADED=1 ;;
        targets)    _MACCLEANER_TGTS=$ids; _MACCLEANER_TGTS_LOADED=1 ;;
    esac
}

# Complete a comma-separated list.
#   $1 = the current word (may be "a,b,pref")
#   $2 = space-separated candidate pool
# Because "," is not in COMP_WORDBREAKS the whole list arrives as one word, so
# we complete only the tail and re-attach the committed prefix to every match.
_maccleaner_comma_complete() {
    local cur=$1 pool=$2
    local prefix tail chosen c filtered=""

    case $cur in
        *,*) prefix=${cur%,*}, ; tail=${cur##*,} ;;
        *)   prefix=""         ; tail=$cur       ;;
    esac

    # Drop values already present in the list.
    # NB: do NOT do this with `local IFS=, ; set -- $prefix ; chosen=" $* "` --
    # $* re-joins on the FIRST char of IFS, i.e. a comma, so only the first
    # element would ever match the " $c " test below.
    chosen=" ${prefix//,/ } "
    for c in $pool; do
        case $chosen in *" $c "*) continue ;; esac
        filtered="$filtered $c"
    done

    COMPREPLY=()
    local m
    for m in $(compgen -W "$filtered" -- "$tail"); do
        COMPREPLY[${#COMPREPLY[@]}]="${prefix}${m}"
    done

    # Keep the cursor glued to the word so the user can type the next comma.
    if type compopt >/dev/null 2>&1; then
        compopt -o nospace          # bash >= 4.0
    fi
    # On bash 3.2 there is no compopt; the completion is registered with
    # -o nospace instead and _maccleaner_finish() re-adds spaces elsewhere.
}

# bash 3.2 has no compopt, so the whole completion runs with -o nospace and we
# append the trailing space by hand for non-list completions.
_maccleaner_finish() {
    if type compopt >/dev/null 2>&1; then
        # bash >= 4: `complete -o nospace` (below) sets nospace as this
        # spec's default, and nothing was cancelling it for ordinary
        # completions, so the cursor stayed glued to the word forever. Cancel
        # it here so bash's own single-vs-multiple-match logic decides
        # whether to add a trailing space. Comma-list completions want the
        # opposite (cursor glued so the user can type the next comma) and
        # already re-enable it themselves via `compopt -o nospace` in
        # _maccleaner_comma_complete -- those code paths return before ever
        # reaching this function, so there's no conflict.
        compopt +o nospace 2>/dev/null
        return 0
    fi
    if [ ${#COMPREPLY[@]} -eq 1 ]; then
        COMPREPLY[0]="${COMPREPLY[0]} "
    fi
}

_maccleaner() {
    local cur prev words cword i sub

    # mclean/mpreview/mreport are shell aliases for `maccleaner <cmd>` (see
    # install.sh), registered against this same function via `complete`
    # below. Splice the implied subcommand into COMP_WORDS so the rest of
    # this function sees an ordinary `maccleaner <cmd> ...` invocation
    # instead of offering the top-level command list again.
    case ${COMP_WORDS[0]} in
        mclean)   COMP_WORDS=( maccleaner clean  "${COMP_WORDS[@]:1}" ); COMP_CWORD=$((COMP_CWORD+1)) ;;
        mpreview) COMP_WORDS=( maccleaner scan   "${COMP_WORDS[@]:1}" ); COMP_CWORD=$((COMP_CWORD+1)) ;;
        mreport)  COMP_WORDS=( maccleaner report "${COMP_WORDS[@]:1}" ); COMP_CWORD=$((COMP_CWORD+1)) ;;
    esac

    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD-1]}

    # "=" IS in COMP_WORDBREAKS, so "--targets=npm" arrives as 3 words.
    # Normalise "--opt = value" back into a simple prev/cur pair.
    if [ "$cur" = "=" ]; then
        prev=${COMP_WORDS[COMP_CWORD-1]}; cur=""
    elif [ "$prev" = "=" ]; then
        prev=${COMP_WORDS[COMP_CWORD-2]}
    fi

    local value_opts=" --targets --category --min-size --roots --min-age-days -n --limit "

    # Locate the subcommand: first non-flag word that is not an option's value.
    sub=""
    i=1
    while [ $i -lt $COMP_CWORD ]; do
        local w=${COMP_WORDS[i]}
        case $w in
            =) : ;;
            -*) case $value_opts in
                    *" $w "*) i=$((i+2)); continue ;;
                esac ;;
            *) sub=$w; break ;;
        esac
        i=$((i+1))
    done

    # Value position for an option that takes one.
    case $prev in
        --targets)
            # `projects --targets` takes generated project-artifact IDs
            # (project-<slug>), a different namespace from cleanup target
            # IDs, and those only exist after a `projects` scan -- which is
            # too slow to run for a completion. Leave it freeform, matching
            # zsh's `projects` spec.
            if [ "$sub" = "projects" ]; then
                COMPREPLY=(); return 0
            fi
            _maccleaner_values targets
            _maccleaner_comma_complete "$cur" "$_MACCLEANER_TGTS"; return 0 ;;
        --category)
            _maccleaner_values categories
            _maccleaner_comma_complete "$cur" "$_MACCLEANER_CATS"; return 0 ;;
        --roots)
            COMPREPLY=( $(compgen -d -- "$cur") ); _maccleaner_finish; return 0 ;;
        --min-size|--min-age-days|-n|--limit)
            COMPREPLY=(); return 0 ;;
    esac

    if [ -z "$sub" ]; then
        COMPREPLY=( $(compgen -W "scan clean projects report doctor config \
categories schedule disk-check storage-insights install-deps --help --version" -- "$cur") )
        _maccleaner_finish; return 0
    fi

    case $sub in
        scan)   COMPREPLY=( $(compgen -W "--category --min-size --all --json --help" -- "$cur") ) ;;
        clean)  COMPREPLY=( $(compgen -W "--yes --targets --category --min-size --trash \
--dry-run --notify --json --help" -- "$cur") ) ;;
        projects) COMPREPLY=( $(compgen -W "--roots --min-age-days --clean --yes --targets \
--trash --dry-run --json --help" -- "$cur") ) ;;
        report) COMPREPLY=( $(compgen -W "-n --limit --json --help" -- "$cur") ) ;;
        doctor|categories|storage-insights)
                COMPREPLY=( $(compgen -W "--json --help" -- "$cur") ) ;;
        disk-check)
                COMPREPLY=( $(compgen -W "--json --no-post --help" -- "$cur") ) ;;
        schedule)
                # positional action, plus --json
                local seen="" j=0
                for (( j=1; j<COMP_CWORD; j++ )); do
                    case ${COMP_WORDS[j]} in
                        status|weekly|monthly|off) seen=1 ;;
                    esac
                done
                if [ -n "$seen" ]; then
                    COMPREPLY=( $(compgen -W "--json --help" -- "$cur") )
                else
                    COMPREPLY=( $(compgen -W "status weekly monthly off --json --help" -- "$cur") )
                fi ;;
        config)
                local action=${COMP_WORDS[$((i+1))]}
                case $action in
                    enable|disable)
                        _maccleaner_values categories
                        COMPREPLY=( $(compgen -W "$_MACCLEANER_CATS" -- "$cur") ) ;;
                    set)
                        if [ "$prev" = set ]; then
                            COMPREPLY=( $(compgen -W "enabled_categories skip_paths \
log_threshold_mb auto_approve delete_mode project_roots project_min_age_days \
project_git_check notifications low_disk_alerts low_disk_threshold_gb \
full_refresh_hours tmp_min_age_days simulator_stale_days \
app_leftover_min_age_days" -- "$cur") )
                        else
                            COMPREPLY=()
                        fi ;;
                    *)  COMPREPLY=( $(compgen -W "show path enable disable set --help" -- "$cur") ) ;;
                esac ;;
        install-deps) COMPREPLY=( $(compgen -W "--help" -- "$cur") ) ;;
        *)      COMPREPLY=() ;;
    esac
    _maccleaner_finish
    return 0
}

# -o nospace is required on bash 3.2 (no compopt); _maccleaner_finish puts the
# space back for ordinary completions.
complete -o nospace -F _maccleaner maccleaner mclean mpreview mreport
