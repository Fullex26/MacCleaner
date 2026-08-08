if (( $+functions[compdef] )); then
    # Something earlier in this rc file (oh-my-zsh, prezto, or the user's own
    # config) already ran compinit -- compdef is already defined. Just
    # register with it. Running compinit a second time here would be
    # wasteful, and some setups deliberately call it with -C (skip all
    # security checks, for speed); a second unconditional call would
    # silently override that intentional choice.
    autoload -Uz _maccleaner
    compdef _maccleaner maccleaner mclean mpreview mreport
else
    # No compinit has run yet in this shell, so compdef isn't defined. We
    # need one, but never pass -u ("insecure": trust group/other-writable
    # fpath directories without asking) -- that disables compaudit's
    # protection for the user's ENTIRE fpath, not just our own directory.
    # -i runs the normal security audit and silently skips any insecure
    # entries instead of either trusting them or interactively prompting.
    autoload -Uz compinit && compinit -i
    compdef _maccleaner maccleaner mclean mpreview mreport
fi
