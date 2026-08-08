#!/usr/bin/env python3
"""Stand-in for cleaner.py implementing the proposed `__complete` subcommand."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complete_data import CATS, TGTS

if len(sys.argv) >= 3 and sys.argv[1] == "__complete":
    if os.environ.get("FAKE_SLOW"):
        time.sleep(float(os.environ["FAKE_SLOW"]))
    if os.environ.get("FAKE_FAIL"):
        sys.stderr.write("boom\n"); sys.exit(1)
    kind = sys.argv[2]
    rows = CATS if kind == "categories" else TGTS if kind == "targets" else []
    for ident, desc in rows:
        # tab-separated: id <TAB> description
        print("%s\t%s" % (ident, desc.replace("\t", " ")))
    sys.exit(0)
sys.stderr.write("unknown\n"); sys.exit(2)
