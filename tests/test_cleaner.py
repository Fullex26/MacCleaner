#!/usr/bin/env python3
"""MacCleaner test suite — stdlib unittest only, no external deps.

Run:  python3 -m unittest discover -s tests -v
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cleaner  # noqa: E402


class TestFormatting(unittest.TestCase):
    def test_fmt_size(self):
        self.assertEqual(cleaner.fmt_size(0), "0.0 B")
        self.assertEqual(cleaner.fmt_size(1023), "1023.0 B")
        self.assertEqual(cleaner.fmt_size(1024), "1.0 KB")
        self.assertEqual(cleaner.fmt_size(1024 ** 2), "1.0 MB")
        self.assertEqual(cleaner.fmt_size(int(2.5 * 1024 ** 3)), "2.5 GB")
        self.assertEqual(cleaner.fmt_size(1024 ** 4), "1.0 TB")

    def test_slugify(self):
        self.assertEqual(cleaner.slugify("Xcode DerivedData"), "xcode-deriveddata")
        self.assertEqual(cleaner.slugify("Log: My App.log"), "log-my-app-log")
        self.assertEqual(cleaner.slugify("a//b__c"), "a-b-c")


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_config_path = cleaner.CONFIG_PATH
        cleaner.CONFIG_PATH = self.tmp / "config.json"

    def tearDown(self):
        cleaner.CONFIG_PATH = self.orig_config_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_when_missing(self):
        cfg = cleaner.load_config()
        self.assertEqual(cfg["enabled_categories"], cleaner.ALL_CATEGORIES)
        self.assertEqual(cfg["delete_mode"], "rm")

    def test_merge_missing_keys(self):
        cleaner.CONFIG_PATH.write_text('{"enabled_categories": ["node"]}')
        cfg = cleaner.load_config()
        self.assertEqual(cfg["enabled_categories"], ["node"])
        self.assertIn("project_roots", cfg)
        self.assertEqual(cfg["log_threshold_mb"], 100)

    def test_defaults_are_copies(self):
        cfg = cleaner.load_config()
        cfg["enabled_categories"].append("bogus")
        self.assertNotIn("bogus", cleaner.DEFAULT_CONFIG["enabled_categories"])

    def test_set_key_parses_json(self):
        cleaner.CONFIG_PATH.write_text(json.dumps(cleaner.DEFAULT_CONFIG))
        cfg = cleaner.load_config()
        cleaner.cmd_config_set_key(cfg, "project_min_age_days", "60")
        reloaded = json.loads(cleaner.CONFIG_PATH.read_text())
        self.assertEqual(reloaded["project_min_age_days"], 60)
        cleaner.cmd_config_set_key(cfg, "delete_mode", "trash")
        reloaded = json.loads(cleaner.CONFIG_PATH.read_text())
        self.assertEqual(reloaded["delete_mode"], "trash")


class TestTargets(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))

    def test_ids_unique_and_slug_shaped(self):
        targets = cleaner.get_targets(self.cfg, all_categories=True)
        ids = [t["id"] for t in targets]
        self.assertEqual(len(ids), len(set(ids)), "duplicate target IDs")
        for tid in ids:
            self.assertRegex(tid, r"^[a-z0-9][a-z0-9-]*$", f"bad id: {tid}")

    def test_all_categories_known(self):
        targets = cleaner.get_targets(self.cfg, all_categories=True)
        for t in targets:
            self.assertIn(t["category"], cleaner.ALL_CATEGORIES)

    def test_every_category_described(self):
        for cat in cleaner.ALL_CATEGORIES:
            self.assertIn(cat, cleaner.CATEGORY_DESCRIPTIONS)

    def test_disabled_category_excluded(self):
        self.cfg["enabled_categories"] = ["node"]
        targets = cleaner.get_targets(self.cfg)
        self.assertTrue(all(t["category"] == "node" for t in targets))
        self.assertTrue(any(t["id"] == "npm-cache" for t in targets))

    def test_skip_paths(self):
        self.cfg["skip_paths"] = ["~/.npm"]
        targets = cleaner.get_targets(self.cfg)
        ids = {t["id"] for t in targets}
        self.assertNotIn("npm-cache", ids)
        self.assertNotIn("npx-cache", ids)
        self.assertIn("yarn-cache", ids)

    def test_dangerous_targets_are_review(self):
        targets = {t["id"]: t for t in cleaner.get_targets(self.cfg, all_categories=True)}
        for tid in ["trash", "ios-backups", "xcode-archives", "maven-repo",
                    "huggingface-hub", "ollama-models", "general-caches"]:
            self.assertFalse(targets[tid]["safe"], f"{tid} must be review-level")

    def test_empty_only_flags(self):
        targets = {t["id"]: t for t in cleaner.get_targets(self.cfg, all_categories=True)}
        self.assertTrue(targets["trash"]["empty_only"])
        self.assertTrue(targets["general-caches"]["empty_only"])


class TestLegacyTranslation(unittest.TestCase):
    def t(self, argv):
        return cleaner.translate_legacy(argv)

    def test_flag_modes(self):
        self.assertEqual(self.t(["--preview"]), ["scan"])
        self.assertEqual(self.t(["--clean"]), ["clean"])
        self.assertEqual(self.t(["--clean", "--yes"]), ["clean", "--yes"])
        self.assertEqual(self.t(["--report"]), ["report"])
        self.assertEqual(self.t(["--json"]), ["scan", "--json"])

    def test_category_passthrough(self):
        self.assertEqual(self.t(["--preview", "--category", "xcode"]),
                         ["scan", "--category", "xcode"])
        self.assertEqual(self.t(["--json", "--category", "node"]),
                         ["scan", "--json", "--category", "node"])

    def test_config_flags(self):
        self.assertEqual(self.t(["--config-show"]), ["config", "show"])
        self.assertEqual(self.t(["--config-enable", "docker"]), ["config", "enable", "docker"])
        self.assertEqual(self.t(["--config-disable", "ruby"]), ["config", "disable", "ruby"])
        self.assertEqual(self.t(["--install-deps"]), ["install-deps"])

    def test_subcommand_aliases(self):
        self.assertEqual(self.t(["preview"]), ["scan"])
        self.assertEqual(self.t(["history"]), ["report"])

    def test_new_style_untouched(self):
        self.assertEqual(self.t(["scan", "--json"]), ["scan", "--json"])
        self.assertEqual(self.t(["clean", "--targets", "npm-cache"]),
                         ["clean", "--targets", "npm-cache"])
        self.assertEqual(self.t(["--version"]), ["--version"])


class TestDeleteSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        self.orig_home = cleaner.HOME
        cleaner.HOME = self.fake_home

    def tearDown(self):
        cleaner.HOME = self.orig_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def target(self, path, **kw):
        base = {"id": "test", "category": "test", "label": "test", "description": "",
                "path": Path(path) if path is not None else None,
                "glob": None, "safe": True, "cmd": None,
                "estimate_cmd": None, "estimate_parser": None, "empty_only": False}
        base.update(kw)
        return base

    def test_refuses_outside_home(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "file").write_text("data")
        freed, err = cleaner.delete_target(self.target(outside))
        self.assertIsNotNone(err)
        self.assertIn("refused", err)
        self.assertTrue(outside.exists(), "outside-home path must survive")

    def test_refuses_home_itself(self):
        self.assertFalse(cleaner._safe_to_delete(self.fake_home))
        self.assertFalse(cleaner._safe_to_delete(Path("/")))

    def test_deletes_dir_inside_home(self):
        victim = self.fake_home / "cache"
        victim.mkdir()
        (victim / "blob").write_text("x" * 1000)
        freed, err = cleaner.delete_target(self.target(victim))
        self.assertIsNone(err)
        self.assertFalse(victim.exists())

    def test_symlink_unlinked_not_followed(self):
        real = self.tmp / "real_data"
        real.mkdir()
        (real / "precious").write_text("keep me")
        link = self.fake_home / "link"
        link.symlink_to(real)
        freed, err = cleaner.delete_target(self.target(link))
        self.assertIsNone(err)
        self.assertFalse(link.is_symlink(), "symlink should be removed")
        self.assertTrue((real / "precious").exists(), "symlink target must survive")

    def test_empty_only_keeps_dir(self):
        d = self.fake_home / "Caches"
        d.mkdir()
        (d / "a").write_text("1")
        (d / "sub").mkdir()
        freed, err = cleaner.delete_target(self.target(d, empty_only=True))
        self.assertIsNone(err)
        self.assertTrue(d.exists(), "empty_only must keep the directory itself")
        self.assertEqual(list(d.iterdir()), [])

    def test_trash_mode_moves(self):
        victim = self.fake_home / "npmcache"
        victim.mkdir()
        (victim / "blob").write_text("x")
        freed, err = cleaner.delete_target(self.target(victim), mode="trash")
        self.assertIsNone(err)
        self.assertFalse(victim.exists())
        trashed = list((self.fake_home / ".Trash").iterdir())
        self.assertEqual(len(trashed), 1)
        self.assertTrue((trashed[0] / "blob").exists())

    def test_trash_target_always_hard_deletes(self):
        trash = self.fake_home / ".Trash"
        trash.mkdir()
        (trash / "old").write_text("x")
        t = self.target(trash, empty_only=True)
        t["id"] = "trash"
        freed, err = cleaner.delete_target(t, mode="trash")
        self.assertIsNone(err)
        self.assertTrue(trash.exists())
        self.assertEqual(list(trash.iterdir()), [], "Trash must end up empty")

    def test_glob_target(self):
        base = self.fake_home / "profiles"
        for name in ["p1", "p2"]:
            (base / name / "cache2").mkdir(parents=True)
            (base / name / "cache2" / "f").write_text("x")
        t = self.target(None, glob=str(base / "*" / "cache2"))
        freed, err = cleaner.delete_target(t)
        self.assertIsNone(err)
        self.assertFalse((base / "p1" / "cache2").exists())
        self.assertFalse((base / "p2" / "cache2").exists())
        self.assertTrue((base / "p1").exists())

    def test_glob_respects_skip_paths(self):
        base = self.fake_home / "profiles"
        for name in ["keep", "clean"]:
            (base / name / "cache2").mkdir(parents=True)
        t = self.target(None, glob=str(base / "*" / "cache2"),
                        skip=[str(base / "keep")])
        freed, err = cleaner.delete_target(t)
        self.assertIsNone(err)
        self.assertTrue((base / "keep" / "cache2").exists(), "skip_paths must protect glob matches")
        self.assertFalse((base / "clean" / "cache2").exists())

    def test_trash_name_collisions_never_nest(self):
        for i in range(3):
            victim = self.fake_home / "cache"
            victim.mkdir()
            (victim / f"round{i}").write_text("x")
            freed, err = cleaner.delete_target(self.target(victim), mode="trash")
            self.assertIsNone(err)
        trashed = sorted((self.fake_home / ".Trash").iterdir())
        self.assertEqual(len(trashed), 3, f"each trashed copy must be a sibling, got {trashed}")
        for d in trashed:
            children = [c.name for c in d.iterdir()]
            self.assertEqual(len(children), 1, "trashed dirs must not nest into each other")


class TestProjectsScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "Code"
        self.cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))
        old = 1_000_000_000  # well in the past

        # Valid stale artifact: node_modules with package.json sibling
        app = self.root / "webapp"
        (app / "node_modules" / "lodash").mkdir(parents=True)
        (app / "package.json").write_text("{}")
        # Nested artifact inside an artifact must not be double-reported;
        # create it BEFORE back-dating so the parent's mtime stays old
        (app / "node_modules" / "dep" / "node_modules").mkdir(parents=True)
        os.utime(app / "node_modules", (old, old))

        # node_modules WITHOUT manifest — must be ignored
        bogus = self.root / "random"
        (bogus / "node_modules").mkdir(parents=True)
        os.utime(bogus / "node_modules", (old, old))

        # Fresh artifact — must be ignored at default min_age
        fresh = self.root / "activeapp"
        (fresh / "node_modules").mkdir(parents=True)
        (fresh / "package.json").write_text("{}")

        # Rust target with Cargo.toml
        rusty = self.root / "rusty"
        (rusty / "target" / "debug").mkdir(parents=True)
        (rusty / "Cargo.toml").write_text("[package]")
        os.utime(rusty / "target", (old, old))

        # 'target' dir with no Cargo.toml — a regular folder, must be ignored
        docs = self.root / "website"
        (docs / "target").mkdir(parents=True)
        os.utime(docs / "target", (old, old))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_only_manifest_backed_stale_artifacts(self):
        hits, roots, min_age = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        kinds = {(h["kind"], Path(h["project"]).name) for h in hits}
        self.assertIn(("node_modules", "webapp"), kinds)
        self.assertIn(("target", "rusty"), kinds)
        self.assertNotIn(("node_modules", "random"), kinds)
        self.assertNotIn(("node_modules", "activeapp"), kinds)
        self.assertNotIn(("target", "website"), kinds)
        self.assertNotIn(("node_modules", "dep"), kinds)
        self.assertEqual(len(hits), 2)

    def test_min_age_zero_includes_fresh(self):
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)], min_age_days=0)
        kinds = {(h["kind"], Path(h["project"]).name) for h in hits}
        self.assertIn(("node_modules", "activeapp"), kinds)

    def test_missing_root_is_skipped(self):
        hits, roots, _ = cleaner.scan_projects(self.cfg, roots=[str(self.tmp / "nope")])
        self.assertEqual(hits, [])
        self.assertEqual(roots, [])

    def test_projects_to_targets(self):
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        targets = cleaner.projects_to_targets(hits)
        for t in targets:
            self.assertFalse(t["safe"], "project artifacts must be review-level")
            self.assertTrue(t["id"].startswith("project-"))
        self.assertEqual(len({t["id"] for t in targets}), len(targets))


class TestCLIIntegration(unittest.TestCase):
    """End-to-end subprocess tests against a sandboxed HOME."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.home = cls.tmp / "home"
        (cls.home / ".npm" / "_cacache").mkdir(parents=True)
        (cls.home / ".npm" / "_cacache" / "blob").write_text("x" * 4096)
        cfg = {"enabled_categories": ["node"], "log_threshold_mb": 100}
        cls.cfg_path = cls.tmp / "config.json"
        cls.cfg_path.write_text(json.dumps(cfg))
        cls.env = {**os.environ,
                   "HOME": str(cls.home),
                   "MACCLEANER_CONFIG": str(cls.cfg_path),
                   "MACCLEANER_LOG": str(cls.tmp / "report.log"),
                   "MACCLEANER_SNAPSHOTS": str(cls.tmp / "snapshots.log")}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_version(self):
        r = self.run_cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn(cleaner.VERSION, r.stdout)

    def test_scan_json_schema(self):
        r = self.run_cli("scan", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        for key in ["version", "timestamp", "disk", "disk_stats",
                    "total_reclaimable_bytes", "total_reclaimable_human", "targets"]:
            self.assertIn(key, data)
        for t in data["targets"]:
            for key in ["id", "category", "label", "description",
                        "size_bytes", "size_human", "safe", "exists"]:
                self.assertIn(key, t)

    def test_legacy_bare_json_is_scan(self):
        """The menu bar app contract: `cleaner.py --json` = scan --json."""
        r = self.run_cli("--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("total_reclaimable_bytes", data)
        self.assertIn("targets", data)

    def test_clean_targets_json(self):
        r = self.run_cli("clean", "--targets", "npm-cache", "--yes", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["items"][0]["id"], "npm-cache")
        self.assertEqual(data["items"][0]["status"], "deleted")
        self.assertFalse((self.home / ".npm" / "_cacache").exists())

    def test_unknown_target_exits_1(self):
        r = self.run_cli("clean", "--targets", "not-a-thing", "--yes")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Unknown target", r.stderr)

    def test_unknown_category_exits_1(self):
        r = self.run_cli("scan", "--category", "warp-drive")
        self.assertEqual(r.returncode, 1)

    def test_doctor_json(self):
        r = self.run_cli("doctor", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("checks", data)
        self.assertTrue(any(c["name"] == "Python" for c in data["checks"]))

    def test_categories_json(self):
        r = self.run_cli("categories", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        names = [c["name"] for c in data["categories"]]
        self.assertEqual(names, cleaner.ALL_CATEGORIES)

    def test_report_after_clean(self):
        r = self.run_cli("report", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("runs", data)

    def test_scan_records_snapshot(self):
        r = self.run_cli("scan", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(Path(self.env["MACCLEANER_SNAPSHOTS"]).exists())

    def test_report_json_has_disk_history(self):
        self.run_cli("scan", "--json")
        r = self.run_cli("report", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertIn("disk_history", data)
        self.assertIn("current", data["disk_history"])
        self.assertIn("snapshots", data["disk_history"])
        self.assertIn("runs", data)  # existing key untouched


class TestNewTargetsV21(unittest.TestCase):
    """Engine v2.1: new categories and targets."""

    def setUp(self):
        self.cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))
        self.targets = {t["id"]: t
                        for t in cleaner.get_targets(self.cfg, all_categories=True)}

    def test_new_categories_registered(self):
        for cat in ["flutter", "php", "vms"]:
            self.assertIn(cat, cleaner.ALL_CATEGORIES)
            self.assertIn(cat, cleaner.CATEGORY_DESCRIPTIONS)

    def test_new_target_ids_present(self):
        for tid in ["dart-pub-cache", "composer-cache", "colima-vm", "vagrant-boxes",
                    "minikube-cache", "yarn-global-cache", "npm-logs", "conda-clean",
                    "sccache-cache", "lm-studio-models", "whisper-models",
                    "xcode-doc-cache", "cypress-cache", "teams-cache", "zoom-updater",
                    "terraform-plugin-cache", "expo-cache"]:
            self.assertIn(tid, self.targets, f"missing target {tid}")

    def test_new_review_flags(self):
        for tid in ["colima-vm", "vagrant-boxes", "lm-studio-models",
                    "whisper-models", "cypress-cache"]:
            self.assertFalse(self.targets[tid]["safe"], f"{tid} must be review-level")
        for tid in ["dart-pub-cache", "composer-cache", "minikube-cache",
                    "conda-clean", "teams-cache", "yarn-global-cache", "npm-logs",
                    "sccache-cache", "xcode-doc-cache", "zoom-updater",
                    "terraform-plugin-cache", "expo-cache"]:
            self.assertTrue(self.targets[tid]["safe"], f"{tid} should be safe")

    def test_conda_is_cmd_target(self):
        t = self.targets["conda-clean"]
        self.assertIsNone(t["path"])
        self.assertIn("conda clean", t["cmd"])
        self.assertIn("--dry-run", t["estimate_cmd"])

    def test_conda_estimate_parser(self):
        out = ("Will remove 132 (1.5 GB) tarball(s).\n"
               "Will remove 10 index cache(s).\n"
               "Will remove 200 (512.0 MB) package(s).\n")
        self.assertEqual(cleaner._parse_conda_estimate(out),
                         int(1.5 * 1024**3) + int(512.0 * 1024**2))


class TestSnapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig = cleaner.SNAPSHOTS_PATH
        cleaner.SNAPSHOTS_PATH = self.tmp / "snapshots.log"

    def tearDown(self):
        cleaner.SNAPSHOTS_PATH = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_creates_file_with_fields(self):
        cleaner.record_snapshot(1234, {"node": 1234})
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        for key in ["ts", "disk_total_bytes", "disk_free_bytes",
                    "reclaimable_bytes", "categories"]:
            self.assertIn(key, snaps[0])
        self.assertEqual(snaps[0]["reclaimable_bytes"], 1234)

    def test_partial_records_null_fields(self):
        cleaner.record_snapshot()
        s = cleaner.load_snapshots()[0]
        self.assertIsNone(s["reclaimable_bytes"])
        self.assertIsNone(s["categories"])
        self.assertGreater(s["disk_free_bytes"], 0)

    def test_same_hour_replaces(self):
        cleaner.record_snapshot(100, {})
        cleaner.record_snapshot(200, {})
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1, "same-hour snapshots must replace, not append")
        self.assertEqual(snaps[0]["reclaimable_bytes"], 200)

    def test_cap_365(self):
        base = datetime.datetime(2025, 1, 1)
        snaps = [{"ts": (base + datetime.timedelta(hours=i)).isoformat(),
                  "disk_total_bytes": 1, "disk_free_bytes": 1,
                  "reclaimable_bytes": i, "categories": {}} for i in range(365)]
        cleaner.SNAPSHOTS_PATH.write_text(json.dumps(snaps))
        cleaner.record_snapshot(999, {})
        out = cleaner.load_snapshots()
        self.assertEqual(len(out), 365)
        self.assertEqual(out[-1]["reclaimable_bytes"], 999)
        self.assertEqual(out[0]["reclaimable_bytes"], 1, "oldest entry must drop")

    def test_corrupt_file_recovers(self):
        cleaner.SNAPSHOTS_PATH.write_text("{not json")
        cleaner.record_snapshot(42, {})
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["reclaimable_bytes"], 42)

    def test_snapshot_fields_sums(self):
        targets = [{"category": "node", "size": 100}, {"category": "node", "size": 50},
                   {"category": "xcode", "size": 25}]
        total, cats = cleaner.snapshot_fields(targets)
        self.assertEqual(total, 175)
        self.assertEqual(cats, {"node": 150, "xcode": 25})

    def test_format_disk_trend_needs_two(self):
        self.assertIsNone(cleaner.format_disk_trend([]))
        cleaner.record_snapshot(1, {})
        self.assertIsNone(cleaner.format_disk_trend(cleaner.load_snapshots()))

    def test_format_disk_trend_lines(self):
        now = datetime.datetime.now()
        snaps = [{"ts": (now - datetime.timedelta(days=8)).isoformat(),
                  "disk_total_bytes": 100, "disk_free_bytes": 50,
                  "reclaimable_bytes": None, "categories": None},
                 {"ts": (now - datetime.timedelta(days=1)).isoformat(),
                  "disk_total_bytes": 100, "disk_free_bytes": 60,
                  "reclaimable_bytes": None, "categories": None}]
        lines = cleaner.format_disk_trend(snaps)
        self.assertTrue(lines[0].startswith("Free now:"))
        self.assertTrue(any("8d ago" in ln for ln in lines))


if __name__ == "__main__":
    unittest.main()
