#!/usr/bin/env python3
"""V3 Stage 2 parity gate: the Swift kit must agree with the Python engine.

Builds the same deterministic sandbox Stage 1 uses, runs BOTH engines
against it, and compares semantically (per-target dicts, order-free):

- categories: the full 94-target static table — id, label, safe, category —
  must be identical. The Swift table is GENERATED from the Python source
  (tools/gen_swift_target_table.py), so a mismatch here means the generated
  file is stale: regenerate it, don't hand-edit.
- scan --all: for every non-cmd target, (category, label, safe, exists,
  size_bytes) must be identical. cmd targets are compared for presence
  only — Stage 2 deliberately does not execute estimate commands.

Exit 0 on parity, 1 with a readable diff otherwise.
"""
import json, subprocess, sys, tempfile, shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import gen_contract_fixtures as gen

MCK = REPO / "swift" / "MacCleanerKit" / ".build" / "release" / "mck"

def run_json(cmd, env):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed ({r.returncode}):\n{r.stderr}")
    return json.loads(r.stdout)

def main():
    if not MCK.exists():
        raise SystemExit(f"mck binary missing — build with:\n"
                         f"  swift build -c release --package-path swift/MacCleanerKit\n"
                         f"  (expected at {MCK})")
    root = Path(tempfile.mkdtemp(prefix="mc-parity-"))
    failures = []
    try:
        gen.build_sandbox(root)
        env = gen.base_env(root)

        py_cats = run_json([sys.executable, str(gen.ENGINE), "categories", "--json"], env)
        sw_cats = run_json([str(MCK), "categories", "--json"], env)
        def table(d):
            return {t["id"]: (c["name"], t["label"], t["safe"])
                    for c in d["categories"] for t in c["targets"]}
        pt, st = table(py_cats), table(sw_cats)
        for tid in sorted(set(pt) | set(st)):
            if pt.get(tid) != st.get(tid):
                failures.append(f"categories/{tid}: py={pt.get(tid)} swift={st.get(tid)}")

        py_scan = run_json([sys.executable, str(gen.ENGINE), "scan", "--json", "--all"], env)
        sw_scan = run_json([str(MCK), "scan", "--json", "--all"], env)
        def by_id(d): return {t["id"]: t for t in d["targets"]}
        ps, ss = by_id(py_scan), by_id(sw_scan)
        cmd_ids = {t["id"] for t in sw_scan["targets"] if t.get("cmd")}
        for tid in sorted(set(ps) | set(ss)):
            p, s = ps.get(tid), ss.get(tid)
            if p is None or s is None:
                failures.append(f"scan/{tid}: present py={p is not None} swift={s is not None}")
                continue
            if tid in cmd_ids:
                continue
            for k in ("category", "label", "safe", "exists", "size_bytes"):
                if p.get(k) != s.get(k):
                    failures.append(f"scan/{tid}.{k}: py={p.get(k)!r} swift={s.get(k)!r}")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print(f"PARITY FAILED — {len(failures)} divergence(s):")
        for f in failures[:40]:
            print(" ", f)
        raise SystemExit(1)
    print(f"parity OK: {len(pt)} table entries, {len(ps)} scanned targets "
          f"({len(cmd_ids)} cmd targets presence-only)")

if __name__ == "__main__":
    main()
