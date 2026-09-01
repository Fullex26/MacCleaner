#!/usr/bin/env python3
"""V3 Stage 4 gate: verdict-level parity for the deletion GUARDS.

Builds a sandbox holding the full adversarial corpus — every attack shape the
Python guards were hardened against, including the 2.8.1 symlinked-ancestor
near-miss — then asks BOTH engines for a verdict on every scenario and fails
on any disagreement. Deletion callers may not be written for the Swift engine
until this gate exists and passes; that is the whole meaning of "guard-first".

Scenarios marked expect=None are judged for agreement only; ones with an
explicit expectation additionally pin the agreed verdict, so both engines
agreeing on a WRONG answer still fails.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "cleaner.py"
MCK = REPO / "swift" / "MacCleanerKit" / ".build" / "release" / "mck"


def build_corpus(root: Path):
    """Returns [(name, path, guard, expect)] — guard is 'home' or 'tmp'."""
    home = root / "home"
    outside = root / "outside"
    tmproot = root / "tmp-root"
    for d in (home / "Library" / "Caches", outside, tmproot / "ws" / "derived" / "Build"):
        d.mkdir(parents=True)
    (outside / "victim.txt").write_text("must never be deletable via home guard")
    (home / "Library" / "Caches" / "real-cache").mkdir()

    # The 2.8.1 attack: a symlinked ANCESTOR smuggling a lexically-inside
    # path to a physically-outside location.
    (home / "Library" / "Caches" / "Vendor").symlink_to(outside)
    # A symlink LEAF pointing outside: deletable (only the link dies).
    (home / "leaf-link").symlink_to(outside / "victim.txt")
    # A symlink chain: link -> link -> outside directory, used as ancestor.
    (root / "hop").symlink_to(outside)
    (home / "chain").symlink_to(root / "hop")
    # /tmp-style root symlink (like /tmp -> /private/tmp).
    (root / "tmp-alias").symlink_to(tmproot)

    scenarios = [
        # ── home guard: refusals ──
        ("outside_home",            str(outside / "victim.txt"),            "home", False),
        ("home_itself",             str(home),                              "home", False),
        ("root",                    "/",                                    "home", False),
        ("home_trailing_slash",     str(home) + "/",                        "home", False),
        ("dotdot_leaf",             str(home / "Library" / ".."),           "home", False),
        ("dotdot_smuggle",          str(home / "Library" / ".." / ".." / "outside" / "victim.txt"), "home", False),
        ("symlinked_ancestor",      str(home / "Library" / "Caches" / "Vendor" / "victim.txt"), "home", False),
        ("symlink_chain_ancestor",  str(home / "chain" / "victim.txt"),     "home", False),
        ("parent_of_home",          str(home.parent),                       "home", False),
        ("sibling_prefix",          str(root / (home.name + "2")),          "home", False),
        # ── home guard: allowed ──
        ("inside_home",             str(home / "Library" / "Caches" / "real-cache"), "home", True),
        ("symlink_leaf_outside",    str(home / "leaf-link"),                "home", True),
        ("nonexistent_inside",      str(home / "not-yet-created"),          "home", True),
        # ── tmp carve-out ──
        ("tmp_root_itself",         str(tmproot),                           "tmp", False),
        ("tmp_child",               str(tmproot / "ws"),                    "tmp", True),
        ("tmp_grandchild",          str(tmproot / "ws" / "derived"),        "tmp", True),
        ("tmp_great_grandchild",    str(tmproot / "ws" / "derived" / "Build"), "tmp", False),
        ("tmp_outside",             str(outside),                           "tmp", False),
        ("tmp_via_root_symlink",    str(root / "tmp-alias" / "ws"),         "tmp", True),
        ("tmp_dotdot_escape",       str(tmproot / "ws" / ".." / ".." / "outside"), "tmp", False),
    ]
    return home, tmproot, scenarios


def python_verdicts(home, tmproot, scenarios):
    code = """
import sys, json
sys.path.insert(0, %r)
import cleaner
from pathlib import Path
cleaner.HOME = Path(%r)
cleaner.TMP_SCAN_ROOT = Path(%r)
out = {}
for name, path, guard in json.load(sys.stdin):
    if guard == "home":
        out[name] = bool(cleaner._safe_to_delete(Path(path)))
    else:
        out[name] = bool(cleaner._tmp_scan_path_allowed(path))
print(json.dumps(out))
""" % (str(REPO), str(home), str(tmproot))
    payload = json.dumps([(n, p, g) for n, p, g, _ in scenarios])
    r = subprocess.run([sys.executable, "-c", code], input=payload,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        sys.exit(1)
    return json.loads(r.stdout)


def swift_verdicts(home, tmproot, scenarios):
    out = {}
    env = {**os.environ, "HOME": str(home), "MACCLEANER_TMP_ROOT": str(tmproot)}
    for name, path, guard, _ in scenarios:
        r = subprocess.run([str(MCK), "guard-check", path, "--json"],
                           capture_output=True, text=True, timeout=30, env=env)
        if r.returncode != 0:
            print(f"mck guard-check failed for {name}: {r.stderr}", file=sys.stderr)
            sys.exit(1)
        d = json.loads(r.stdout)
        out[name] = d["safe_to_delete"] if guard == "home" else d["tmp_scan_path_allowed"]
    return out


def main():
    if not MCK.exists():
        print(f"mck not built at {MCK}; run: swift build -c release "
              f"--package-path swift/MacCleanerKit", file=sys.stderr)
        sys.exit(1)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home, tmproot, scenarios = build_corpus(root)
        py = python_verdicts(home, tmproot, scenarios)
        sw = swift_verdicts(home, tmproot, scenarios)
        failures = []
        for name, _, guard, expect in scenarios:
            if py[name] != sw[name]:
                failures.append(f"DIVERGE  {name} ({guard}): python={py[name]} swift={sw[name]}")
            elif expect is not None and py[name] != expect:
                failures.append(f"BOTH-WRONG {name} ({guard}): agreed {py[name]}, expected {expect}")
        if failures:
            print("\n".join(failures), file=sys.stderr)
            sys.exit(1)
        print(f"guard parity OK: {len(scenarios)} adversarial scenarios, "
              f"verdict-identical across both engines, all expectations met")


if __name__ == "__main__":
    main()
