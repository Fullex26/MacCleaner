maccleaner() { python3 "$HOME/mac-cleaner/cleaner.py" "$@"; }
mclean()     { python3 "$HOME/mac-cleaner/cleaner.py" clean "$@"; }
mpreview()   { python3 "$HOME/mac-cleaner/cleaner.py" scan "$@"; }
mreport()    { python3 "$HOME/mac-cleaner/cleaner.py" report "$@"; }
