#!/usr/bin/env python3
"""Stand-in for cleaner.py's `categories --json` -- the actual completion
data source. There is no `__complete` subcommand in the real engine, so this
fake doesn't implement one either: calling it falls through to the same
"unknown" exit 2 the real engine would give, which is what makes a future
regression to a fictional subcommand fail loudly instead of silently."""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from complete_data import CATS, TGTS

if len(sys.argv) >= 3 and sys.argv[1] == "categories" and sys.argv[2] == "--json":
    if os.environ.get("FAKE_SLOW"):
        time.sleep(float(os.environ["FAKE_SLOW"]))
    if os.environ.get("FAKE_FAIL"):
        sys.stderr.write("boom\n"); sys.exit(1)
    # Real shape: {"categories": [{"name", "description", "enabled",
    # "targets": [{"id", "label", "safe"}]}]}. complete_data.py's CATS/TGTS
    # are flat lists (no real category<->target grouping), so park every
    # target under the first category -- the reshape script only cares about
    # the flattened totals (78 targets, 20 categories), not the grouping.
    categories = [
        {"name": name, "description": desc, "enabled": True, "targets": []}
        for name, desc in CATS
    ]
    if categories:
        categories[0]["targets"] = [
            {"id": ident, "label": label, "safe": True} for ident, label in TGTS
        ]
    print(json.dumps({"version": "0.0.0-fake", "categories": categories}))
    sys.exit(0)

sys.stderr.write("unknown\n")
sys.exit(2)
