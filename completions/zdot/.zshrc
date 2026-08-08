# Completion-test harness zshrc, BASE layer. This mirrors exactly what
# install.sh writes to a real ~/.zshrc -- it sources the SAME template files
# install.sh cats into the user's rc (../shell-shortcuts.sh,
# ../zsh-compdef-init.zsh), so a future edit to either file is exercised by
# this suite automatically instead of silently drifting from what actually
# ships. This is what catches bugs like: zsh only completes through a bare
# alias when `complete_aliases` is set (off by default), so an alias-based
# `maccleaner` never reached _maccleaner at all -- a fixture that never
# wired shortcuts up the way install.sh does could never have caught that.
#
# capture.zsh/buffer_capture.zsh source this file from a fresh per-run
# ZDOTDIR (not this directory), so compinit's dump file and other
# ZDOTDIR-relative state land in a throwaway tmp dir, never here.
#
# Deliberately NO compadd override in this file -- see .zshrc-capture for
# why that has to live separately, sourced only by capture.zsh.
PS1='@@RDY@@ '
unset zle_bracketed_paste
setopt no_beep
unsetopt list_beep always_last_prompt auto_list

source "$COMPTEST_DIR/shell-shortcuts.sh"

# install.sh writes this fpath line itself (it's the one piece that embeds a
# real path); the compdef logic proper is the shared, path-independent file.
fpath=($COMPTEST_DIR $fpath)
source "$COMPTEST_DIR/zsh-compdef-init.zsh"
