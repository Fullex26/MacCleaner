#!/usr/bin/env python3
"""V3 Stage 2 parity gate: the Swift kit must agree with the Python engine.

Builds the same deterministic sandbox Stage 1 uses, runs BOTH engines
against it, and compares semantically (per-target dicts, order-free):

- categories: the full 94-target static table — id, label, safe, category —
  must be identical. The Swift table is GENERATED from the Python source
  (tools/gen_swift_target_table.py), so a mismatch here means the generated
  file is stale: regenerate it, don't hand-edit.
- scan --all: for every target, (category, label, safe, exists,
  size_bytes) must be identical, including command estimates and simulator sizes.

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
        cfg_path = Path(env["MACCLEANER_CONFIG"])
        cfg = json.loads(cfg_path.read_text())
        cfg["enabled_categories"] += ["docker", "homebrew", "ruby"]
        cfg_path.write_text(json.dumps(cfg))
        # Strict stubs reject cleanup arguments: only read-only estimates run.
        import shlex
        samples = {
            "docker": ("system df", "TYPE TOTAL ACTIVE SIZE RECLAIMABLE\nImages 3 1 8GB 1GB (12%)\nContainers 2 0 9MB 2MB (22%)\nLocal Volumes 1 0 50GB 50GB (100%)\nBuild Cache 4 0 4MB 3MB (75%)"),
            "brew": ("cleanup --dry-run", "Would free approximately 1.5GB of disk space."),
            "conda": ("clean --all --dry-run", "Will remove 2 tarballs (2 MB).\nWill remove 3 packages (1 GB)."),
            "pnpm": ("store path", str(root / "pnpm-store")),
        }
        (root / "pnpm-store").mkdir()
        (root / "pnpm-store" / "data").write_bytes(b"x" * 8192)
        (root / "bin" / "xargs").symlink_to("/usr/bin/xargs")
        for tool, (args, output) in samples.items():
            script = root / "bin" / tool
            script.write_text("#!/bin/sh\n[ \"$*\" = " + shlex.quote(args) +
                              " ] || exit 91\nprintf '%s\\n' " + shlex.quote(output) + "\n")
            script.chmod(0o755)

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
          f"({len(cmd_ids)} cmd targets fully compared)")

if __name__ == "__main__":
    main()
