# Completion-test harness zshrc.
PS1='@@RDY@@ '
unset zle_bracketed_paste
setopt no_beep
unsetopt list_beep always_last_prompt auto_list
fpath=($COMPTEST_DIR $fpath)
autoload -Uz compinit
compinit -u -d "$COMPTEST_TMP/zcompdump"

# Intercept compadd. `-O arr` makes compadd compute the matches it *would*
# have added and store them, honouring every other option (-a, -d, prefixes,
# match specs) instead of us re-parsing them by hand.
compadd() {
  local -a _cap
  builtin compadd -O _cap "$@" 2>/dev/null
  (( $#_cap )) && print -l -- "${_cap[@]}" >> "$COMPTEST_TMP/out"
  builtin compadd "$@"
}
