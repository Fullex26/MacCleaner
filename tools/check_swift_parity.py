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
- command text: for every target that runs a command, the exact command string
  must be identical. Read from `clean --dry-run` on the Python side, which is
  the only place the engine exposes it.

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
        for tid in sorted(set(ps) | set(ss)):
            p, s = ps.get(tid), ss.get(tid)
            if p is None or s is None:
                failures.append(f"scan/{tid}: present py={p is not None} swift={s is not None}")
                continue
            for k in ("category", "label", "safe", "exists", "size_bytes"):
                if p.get(k) != s.get(k):
                    failures.append(f"scan/{tid}.{k}: py={p.get(k)!r} swift={s.get(k)!r}")
        # Command TEXT, not merely its presence. The engine exposes a target's
        # command only through `clean --dry-run` (scan --json omits it), and a dry
        # run deletes nothing and writes no report.log. Until this existed the
        # gate compared cmd targets on size alone while printing "fully
        # compared", so both hand-written simctl strings in SimulatorScanner.swift
        # kept the suppressed "2>/dev/null ... ; true" form for a release after
        # the engine dropped it — the very form that made a failed delete
        # unreportable (issue #106). Built from BOTH engines so a command target
        # present on only one side is a failure, not a silent omission.
        dry = run_json([sys.executable, str(gen.ENGINE), "clean", "--dry-run",
                        "--json", "--targets", ",".join(sorted(ps))], env)
        py_cmds = {i["id"]: i["cmd"] for i in dry["items"] if i.get("cmd")}
        sw_cmds = {t["id"]: t["cmd"] for t in sw_scan["targets"] if t.get("cmd")}
        cmd_ids = set(py_cmds) | set(sw_cmds)
        for tid in sorted(cmd_ids):
            if py_cmds.get(tid) != sw_cmds.get(tid):
                failures.append(f"cmd/{tid}: py={py_cmds.get(tid)!r} "
                                f"swift={sw_cmds.get(tid)!r}")

        # Pin independent known totals too: two engines returning zero is not parity.
        expected = {"docker-prune": 1078984704, "brew-cleanup": 1610612736,
                    "conda-clean": 1075838976, "pnpm-store": 8192,
                    "simulator-stale-devices": 16384,
                    "simulator-unused-runtimes": 8000000000}
        for tid, size in expected.items():
            for engine, rows in (("py", ps), ("swift", ss)):
                if rows.get(tid, {}).get("size_bytes") != size:
                    failures.append(f"{engine}/{tid}: expected {size} bytes")

        # Real scanner collisions must retain identical IDs in both engines.
        import os, time
        for name in ("foo-bar", "foo_bar"):
            shutil.copytree(root / "tmp-root/ws-derived-top", root / "tmp-root" / name)
        orphan = root / "leftover-lib/Caches/com.gone-app"
        orphan.mkdir()
        (orphan / "blob").write_bytes(b"x" * 4096)
        old = time.time() - gen.LEFTOVER_AGE
        os.utime(orphan, (old, old))
        collision_py = by_id(run_json([sys.executable, str(gen.ENGINE), "scan", "--json", "--all"], env))
        collision_sw = by_id(run_json([str(MCK), "scan", "--json", "--all"], env))
        for tid in sorted(set(collision_py) | set(collision_sw)):
            p, s = collision_py.get(tid), collision_sw.get(tid)
            if p is None or s is None or p.get("size_bytes") != s.get("size_bytes"):
                failures.append(f"collision/{tid}: presence or size differs")
        for prefix, expected_count in (("tmp-foo-bar--", 1), ("leftover-com-gone-app--", 2)):
            if sum(tid.startswith(prefix) for tid in collision_py) != expected_count:
                failures.append(f"collision/{prefix}: unexpected digest-ID count")

        trailing_env = {**env, "MACCLEANER_TMP_ROOT": env["MACCLEANER_TMP_ROOT"] + "/"}
        trailing_py = by_id(run_json([sys.executable, str(gen.ENGINE), "scan", "--json", "--all"], trailing_env))
        trailing_sw = by_id(run_json([str(MCK), "scan", "--json", "--all"], trailing_env))
        if set(trailing_py) != set(trailing_sw) or set(trailing_py) != set(collision_py):
            failures.append("trailing tmp-root separator changed target IDs")

        cfg["skip_paths"] = [str(root / "home/.npm/_cacache/content-v2/aa"),
                             str(root / "home/Library/Caches/pip"),
                             str(root / "tmp-root/ws-derived-top/Build"),
                             str(root / "leftover-lib/Caches/com.gone.app/blob")]
        cfg_path.write_text(json.dumps(cfg))
        excluded_py = by_id(run_json([sys.executable, str(gen.ENGINE), "scan", "--json", "--all"], env))
        excluded_sw = by_id(run_json([str(MCK), "scan", "--json", "--all"], env))
        for tid in sorted(set(excluded_py) | set(excluded_sw)):
            p, s = excluded_py.get(tid), excluded_sw.get(tid)
            if p is None or s is None:
                failures.append(f"excluded/{tid}: presence differs")
            elif any(p.get(k) != s.get(k) for k in ("exists", "size_bytes")):
                failures.append(f"excluded/{tid}: size or existence differs")
        for tid in ("npm-cache", "pip-cache", "general-caches", "tmp-ws-derived-top"):
            if tid in excluded_py or tid in excluded_sw:
                failures.append(f"excluded/{tid}: protected parent still offered")
        cfg["skip_paths"] = []

        cfg["enabled_categories"] = ["docker", "homebrew", "node", "python"]
        cfg_path.write_text(json.dumps(cfg))
        for scenario, body in (("failed", "printf 'free approximately 5GB'; exit 1"),
                               ("malformed", "printf 'unavailable'"),
                               ("missing", None)):
            for tool in samples:
                script = root / "bin" / tool
                if body is None:
                    script.unlink()
                else:
                    script.write_text("#!/bin/sh\n" + body + "\n")
            for engine, cmd in (("py", [sys.executable, str(gen.ENGINE)]),
                                ("swift", [str(MCK)])):
                rows = by_id(run_json(cmd + ["scan", "--json", "--all"], env))
                for tid in ("docker-prune", "brew-cleanup", "conda-clean", "pnpm-store"):
                    row = rows.get(tid, {})
                    if row.get("size_bytes") != 0 or row.get("exists") is not True:
                        failures.append(f"{scenario}/{engine}/{tid}: expected zero bytes, exists=true")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        print(f"PARITY FAILED — {len(failures)} divergence(s):")
        for f in failures[:40]:
            print(" ", f)
        raise SystemExit(1)
    print(f"parity OK: {len(pt)} table entries, {len(ps)} scanned targets "
          f"({len(cmd_ids)} command strings compared verbatim)")

if __name__ == "__main__":
    main()
