#!/usr/bin/env python3
"""MacCleaner test suite — stdlib unittest only, no external deps.

Run:  python3 -m unittest discover -s tests -v
"""

import contextlib
import datetime
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


class TestDoctorSchedule(unittest.TestCase):
    """`doctor`'s Schedule check used to glob the LaunchAgents directory and
    report ✅ purely from a plist's existence, even if launchd never
    successfully loaded it (finding I1). It must ask launchd directly."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.cfg_path = self.tmp / "config.json"
        self.cfg_path.write_text("{}")
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(self.cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log"),
                    "PATH": f"{self.bindir}:{os.environ['PATH']}"}
        # A real crontab may exist on the dev machine; use a stub so this
        # test's outcome depends only on the fabricated LaunchAgents/launchctl.
        stub_crontab = self.bindir / "crontab"
        stub_crontab.write_text('#!/bin/sh\nexit 1\n')
        stub_crontab.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_launchctl(self, body):
        stub = self.bindir / "launchctl"
        stub.write_text(body)
        stub.chmod(0o755)

    def run_doctor(self):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), "doctor", "--json"],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def test_plist_present_but_not_loaded_is_not_ok(self):
        (self.agents / "com.fullex.maccleaner.clean.plist").write_text("<plist/>")
        self.write_launchctl('#!/bin/sh\nexit 1\n')  # `launchctl list <label>` always fails
        r = self.run_doctor()
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        sched = next(c for c in data["checks"] if c["name"] == "Schedule")
        self.assertFalse(sched["ok"], "a plist launchd hasn't loaded must not read as healthy")
        self.assertIn("not loaded", sched["status"].lower())

    def test_plist_loaded_is_ok(self):
        (self.agents / "com.fullex.maccleaner.clean.plist").write_text("<plist/>")
        self.write_launchctl('#!/bin/sh\nexit 0\n')  # `launchctl list <label>` always succeeds
        r = self.run_doctor()
        data = json.loads(r.stdout)
        sched = next(c for c in data["checks"] if c["name"] == "Schedule")
        self.assertTrue(sched["ok"])
        self.assertIn("launchd:", sched["status"])

    def test_no_plist_at_all_reports_not_scheduled(self):
        r = self.run_doctor()
        data = json.loads(r.stdout)
        sched = next(c for c in data["checks"] if c["name"] == "Schedule")
        self.assertTrue(sched["ok"])
        self.assertIn("not scheduled", sched["status"].lower())


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

    def test_same_day_replaces(self):
        cleaner.record_snapshot(100, {})
        cleaner.record_snapshot(200, {})
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1, "same-day snapshots must replace, not append")
        self.assertEqual(snaps[0]["reclaimable_bytes"], 200)

    def test_cap_365(self):
        # Spaced a day apart (not an hour) so each entry is distinct under the
        # daily dedupe key — otherwise this wouldn't actually test a cap of
        # 365 *distinct* days, just 365 pre-written rows that happen to survive
        # because record_snapshot only ever compares against the last one.
        base = datetime.datetime(2025, 1, 1)
        snaps = [{"ts": (base + datetime.timedelta(days=i)).isoformat(),
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
        # Capture stderr to verify warning is emitted
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            cleaner.record_snapshot(42, {})
        stderr_output = stderr_capture.getvalue()
        # Verify the recovery worked
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["reclaimable_bytes"], 42)
        # Verify warning was emitted exactly once (in load_snapshots during recovery)
        self.assertIn("Warning: corrupt or unparseable", stderr_output)
        self.assertIn(str(cleaner.SNAPSHOTS_PATH), stderr_output)

    def test_partially_malformed_list_recovers(self):
        """A list containing a non-dict element must not get load_snapshots
        (and therefore record_snapshot) permanently stuck: snaps[-1].get(...)
        on a non-dict would raise every future run otherwise."""
        cleaner.SNAPSHOTS_PATH.write_text(json.dumps([
            {"ts": "2025-01-01T00:00:00", "disk_total_bytes": 1, "disk_free_bytes": 1,
             "reclaimable_bytes": 1, "categories": {}},
            "not-a-dict-entry",
            42,
            None,
        ]))
        cleaner.record_snapshot(42, {"node": 42})
        snaps = cleaner.load_snapshots()
        self.assertTrue(all(isinstance(s, dict) for s in snaps),
                        "non-dict entries must be dropped, not crash the reader")
        self.assertEqual(snaps[-1]["reclaimable_bytes"], 42)
        self.assertEqual(len(snaps), 2, "the one valid pre-existing entry plus the new one")

    def test_partially_malformed_list_warns_with_count(self):
        """Dropping malformed entries silently would permanently lose trend
        history (90% garbage in -> 90% gone forever on the next write) with
        no visible trace. load_snapshots must warn how many were discarded."""
        cleaner.SNAPSHOTS_PATH.write_text(json.dumps([
            {"ts": "2025-01-01T00:00:00", "disk_total_bytes": 1, "disk_free_bytes": 1,
             "reclaimable_bytes": 1, "categories": {}},
            "not-a-dict-entry",
            42,
            None,
        ]))
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        stderr_output = stderr_capture.getvalue()
        self.assertIn("Warning: discarded 3 malformed snapshot entries", stderr_output)
        self.assertIn(str(cleaner.SNAPSHOTS_PATH), stderr_output)

    def test_fully_valid_list_does_not_warn(self):
        cleaner.record_snapshot(1, {})
        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            cleaner.load_snapshots()
        self.assertEqual(stderr_capture.getvalue(), "")

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


class TestGitAwareProjects(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "Code"
        self.cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))
        self.old = 1_000_000_000
        # Isolate from the user's global git config (gpgsign, hooks, etc.)
        self.git_env = {**os.environ,
                        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, check=True, env=self.git_env)

    def make_project(self, name):
        proj = self.root / name
        (proj / "node_modules" / "x").mkdir(parents=True)
        (proj / "package.json").write_text("{}")
        os.utime(proj / "node_modules", (self.old, self.old))
        return proj

    def make_repo(self, name, dirty=False, pushed=True, unpushed_commit=False):
        proj = self.make_project(name)
        (proj / ".gitignore").write_text("node_modules/\n")
        self._git(proj, "init", "-q")
        self._git(proj, "add", ".gitignore", "package.json")
        self._git(proj, "commit", "-qm", "init")
        if pushed:
            remote = self.tmp / f"{name}-remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                           capture_output=True, check=True, env=self.git_env)
            self._git(proj, "remote", "add", "origin", str(remote))
            self._git(proj, "push", "-q", "origin", "HEAD")
        if unpushed_commit:
            (proj / "more.txt").write_text("more work, never pushed")
            self._git(proj, "add", "more.txt")
            self._git(proj, "commit", "-qm", "more")
        if dirty:
            (proj / "wip.txt").write_text("uncommitted")
        return proj

    def test_unpushed_detected_with_real_remote_present(self):
        """make_repo(pushed=False) never creates a remote at all, so the
        no-remote branch of `unpushed` is the only one that scenario exercises.
        This covers the actual rev-list predicate against a repo that HAS a
        remote and has pushed to it, plus one local commit made afterward —
        a miswritten revision range would still slip past the no-remote case."""
        self.make_repo("aheadproj", dirty=False, pushed=True, unpushed_commit=True)
        self.make_repo("fullypushedproj", dirty=False, pushed=True)
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        by_name = {Path(h["project"]).name: h for h in hits}
        self.assertEqual(by_name["aheadproj"]["git"], {"dirty": False, "unpushed": True})
        self.assertEqual(by_name["fullypushedproj"]["git"], {"dirty": False, "unpushed": False})

    def test_git_states_detected(self):
        self.make_repo("cleanproj", dirty=False, pushed=True)
        self.make_repo("dirtyproj", dirty=True, pushed=True)
        self.make_repo("unpushedproj", dirty=False, pushed=False)
        self.make_project("norepo")
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        by_name = {Path(h["project"]).name: h for h in hits}
        self.assertEqual(by_name["cleanproj"]["git"], {"dirty": False, "unpushed": False})
        self.assertEqual(by_name["dirtyproj"]["git"], {"dirty": True, "unpushed": False})
        self.assertEqual(by_name["unpushedproj"]["git"], {"dirty": False, "unpushed": True})
        self.assertIsNone(by_name["norepo"]["git"])

    def test_git_check_disabled_by_config(self):
        self.make_repo("dirtyproj", dirty=True)
        self.cfg["project_git_check"] = False
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        self.assertTrue(all(h["git"] is None for h in hits))

    def test_flagged_targets_get_badges_and_flag(self):
        self.make_repo("dirtyproj", dirty=True, pushed=True)
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        t = cleaner.projects_to_targets(hits)[0]
        self.assertIn("[dirty]", t["label"])
        self.assertTrue(cleaner._git_flagged(t))
        self.assertEqual(t["git"], {"dirty": True, "unpushed": False})

    def test_clean_repo_not_flagged(self):
        self.make_repo("cleanproj", dirty=False, pushed=True)
        hits, _, _ = cleaner.scan_projects(self.cfg, roots=[str(self.root)])
        t = cleaner.projects_to_targets(hits)[0]
        self.assertNotIn("[", t["label"])
        self.assertFalse(cleaner._git_flagged(t))

    def test_yes_never_sweeps_flagged(self):
        self.make_repo("dirtyproj", dirty=True, pushed=True)
        self.make_repo("cleanproj", dirty=False, pushed=True)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"project_roots": [str(self.root)],
                                        "project_min_age_days": 0}))
        env = {**os.environ, "HOME": str(self.tmp),
               "MACCLEANER_CONFIG": str(cfg_path),
               "MACCLEANER_LOG": str(self.tmp / "report.log"),
               "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log")}
        r = subprocess.run([sys.executable, str(REPO / "cleaner.py"),
                            "projects", "--clean", "--yes", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0)
        self.assertTrue((self.root / "dirtyproj" / "node_modules").exists(),
                        "dirty project must survive --yes")
        self.assertFalse((self.root / "cleanproj" / "node_modules").exists(),
                         "clean project should be swept by --yes")
        self.assertIn("dirtyproj", r.stderr, "skip note should name the project")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                      "root bypasses file permission checks")
    def test_git_info_degrades_to_none_on_partial_failure(self):
        # rev-parse only checks whether the dir is a work tree; it doesn't
        # touch the index. Revoking read access to .git/index leaves
        # rev-parse succeeding while `git status` fails outright (nonzero
        # exit, empty stdout) — exactly the "readable enough to pass
        # rev-parse but fails later" scenario the fix must catch.
        proj = self.make_repo("corruptproj", dirty=False, pushed=True)
        index_path = proj / ".git" / "index"
        self.assertTrue(index_path.exists())
        os.chmod(index_path, 0o000)
        try:
            sanity = subprocess.run(
                ["git", "-C", str(proj), "rev-parse", "--is-inside-work-tree"],
                capture_output=True, text=True, env=self.git_env)
            self.assertEqual(sanity.returncode, 0)
            self.assertEqual(sanity.stdout.strip(), "true")

            broken = subprocess.run(
                ["git", "-C", str(proj), "status", "--porcelain"],
                capture_output=True, text=True, env=self.git_env)
            self.assertNotEqual(broken.returncode, 0)
            self.assertEqual(broken.stdout.strip(), "")

            self.assertIsNone(cleaner._git_info(proj),
                               "a git failure after rev-parse must degrade to None, "
                               "never report a false clean/pushed state")
        finally:
            os.chmod(index_path, 0o644)

    def test_targets_override_cleans_flagged_project(self):
        self.make_repo("dirtyproj", dirty=True, pushed=True)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"project_roots": [str(self.root)],
                                        "project_min_age_days": 0}))
        env = {**os.environ, "HOME": str(self.tmp),
               "MACCLEANER_CONFIG": str(cfg_path),
               "MACCLEANER_LOG": str(self.tmp / "report.log"),
               "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log")}

        # Resolve the generated target id from the same sandboxed env (HOME
        # is the tempdir here, so ids computed elsewhere wouldn't match).
        scan = subprocess.run([sys.executable, str(REPO / "cleaner.py"),
                               "projects", "--json"],
                              capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(scan.returncode, 0)
        artifacts = json.loads(scan.stdout)["artifacts"]
        self.assertEqual(len(artifacts), 1)
        target_id = artifacts[0]["id"]

        r = subprocess.run([sys.executable, str(REPO / "cleaner.py"),
                            "projects", "--clean", "--yes",
                            "--targets", target_id, "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.root / "dirtyproj" / "node_modules").exists(),
                         "explicitly named flagged project must still be cleaned by --targets")


class TestProjectsDryRun(unittest.TestCase):
    """projects --dry-run must mirror exactly what `projects --clean --yes`
    would sweep: git-flagged projects excluded unless named via --targets,
    and (like every dry run) nothing deleted, no report.log/snapshots.log."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "Code"
        # Isolate from the user's global git config (gpgsign, hooks, etc.)
        self.git_env = {**os.environ,
                        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"project_roots": [str(self.root)],
                                        "project_min_age_days": 0}))
        self.log_path = self.tmp / "report.log"
        self.snap_path = self.tmp / "snapshots.log"
        self.env = {**os.environ, "HOME": str(self.tmp),
                    "MACCLEANER_CONFIG": str(cfg_path),
                    "MACCLEANER_LOG": str(self.log_path),
                    "MACCLEANER_SNAPSHOTS": str(self.snap_path)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, check=True, env=self.git_env)

    def make_project(self, name):
        proj = self.root / name
        (proj / "node_modules" / "x").mkdir(parents=True)
        (proj / "package.json").write_text("{}")
        old = 1_000_000_000
        os.utime(proj / "node_modules", (old, old))
        return proj

    def make_repo(self, name, dirty=False, pushed=True):
        proj = self.make_project(name)
        (proj / ".gitignore").write_text("node_modules/\n")
        self._git(proj, "init", "-q")
        self._git(proj, "add", ".gitignore", "package.json")
        self._git(proj, "commit", "-qm", "init")
        if pushed:
            remote = self.tmp / f"{name}-remote.git"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)],
                           capture_output=True, check=True, env=self.git_env)
            self._git(proj, "remote", "add", "origin", str(remote))
            self._git(proj, "push", "-q", "origin", "HEAD")
        if dirty:
            (proj / "wip.txt").write_text("uncommitted")
        return proj

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_dry_run_excludes_git_flagged_projects(self):
        self.make_repo("dirtyproj", dirty=True, pushed=True)
        self.make_repo("cleanproj", dirty=False, pushed=True)

        r = self.run_cli("projects", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        ids = [i["id"] for i in data["items"]]
        self.assertEqual(len(ids), 1, ids)
        self.assertIn("cleanproj", ids[0])
        self.assertTrue(all("dirtyproj" not in i for i in ids),
                        "git-flagged project must be excluded from the preview")

        # a dry run never deletes, regardless of a target's flagged status
        self.assertTrue((self.root / "dirtyproj" / "node_modules").exists())
        self.assertTrue((self.root / "cleanproj" / "node_modules").exists())
        self.assertFalse(self.log_path.exists(), "dry run must not write report.log")
        self.assertFalse(self.snap_path.exists(), "dry run must not record snapshots")

    def test_dry_run_targets_override_includes_flagged(self):
        self.make_repo("dirtyproj", dirty=True, pushed=True)

        # Resolve the generated target id from the same sandboxed env (HOME
        # is the tempdir here, so ids computed elsewhere wouldn't match).
        scan = self.run_cli("projects", "--json")
        self.assertEqual(scan.returncode, 0, scan.stderr)
        artifacts = json.loads(scan.stdout)["artifacts"]
        self.assertEqual(len(artifacts), 1)
        target_id = artifacts[0]["id"]

        r = self.run_cli("projects", "--dry-run", "--targets", target_id, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([i["id"] for i in data["items"]], [target_id],
                          "explicitly named flagged project must appear in the preview")

        self.assertTrue((self.root / "dirtyproj" / "node_modules").exists(),
                        "dry run must not delete, even for an explicitly named flagged project")
        self.assertFalse(self.log_path.exists(), "dry run must not write report.log")
        self.assertFalse(self.snap_path.exists(), "dry run must not record snapshots")


class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".npm" / "_cacache").mkdir(parents=True)
        (self.home / ".npm" / "_cacache" / "blob").write_text("x" * 4096)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"enabled_categories": ["node"]}))
        self.log_path = self.tmp / "report.log"
        self.snap_path = self.tmp / "snapshots.log"
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(cfg_path),
                    "MACCLEANER_LOG": str(self.log_path),
                    "MACCLEANER_SNAPSHOTS": str(self.snap_path)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_dry_run_deletes_nothing_and_reports(self):
        r = self.run_cli("clean", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertTrue(data["dry_run"])
        self.assertTrue((self.home / ".npm" / "_cacache").exists(),
                        "dry run must not delete")
        npm = next(i for i in data["items"] if i["id"] == "npm-cache")
        self.assertEqual(npm["status"], "would-delete")
        self.assertTrue(npm["paths"])
        self.assertIn("_cacache", npm["paths"][0]["path"])
        self.assertGreater(npm["paths"][0]["size_bytes"], 0)
        self.assertGreater(data["freed_bytes"], 0)

    def test_dry_run_writes_no_logs(self):
        r = self.run_cli("clean", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.log_path.exists(), "dry run must not write report.log")
        self.assertFalse(self.snap_path.exists(), "dry run must not record snapshots")

    def test_dry_run_respects_targets(self):
        r = self.run_cli("clean", "--dry-run", "--targets", "npm-cache", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual([i["id"] for i in data["items"]], ["npm-cache"])

    def test_dry_run_human_output(self):
        r = self.run_cli("clean", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Dry run", r.stdout)
        self.assertIn("Would free", r.stdout)
        self.assertTrue((self.home / ".npm" / "_cacache").exists())


class TestDryRunSafeOnlyFilter(unittest.TestCase):
    """TestDryRun only enables 'node', where every path target is safe=True,
    so a regression leaking review targets into a bare `clean --dry-run`
    preview would pass unnoticed. Use 'xcode', which has both safe and
    review-level targets, to actually exercise the filter."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        derived = self.home / "Library/Developer/Xcode/DerivedData/App-abc123"
        derived.mkdir(parents=True)
        (derived / "blob").write_text("x" * 4096)
        archive_dir = self.home / "Library/Developer/Xcode/Archives"
        archive = archive_dir / "2026-01-01" / "App.xcarchive"
        archive.mkdir(parents=True)
        (archive / "blob").write_text("y" * 4096)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"enabled_categories": ["xcode"]}))
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_bare_dry_run_previews_safe_only(self):
        r = self.run_cli("clean", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        ids = {i["id"] for i in data["items"]}
        self.assertIn("xcode-derived-data", ids)
        self.assertNotIn("xcode-archives", ids,
                         "review targets must not leak into a bare dry-run preview")

    def test_targets_dry_run_previews_named_review_target(self):
        r = self.run_cli("clean", "--dry-run", "--targets", "xcode-archives", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual([i["id"] for i in data["items"]], ["xcode-archives"],
                         "naming a review target via --targets must preview it")


class TestDryRunExpansion(unittest.TestCase):
    """AGENTS.md promises empty_only targets list their top-level children,
    and cmd targets report would-run with the command, never executing it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        caches = self.home / "Library/Caches"
        (caches / "child_a").mkdir(parents=True)
        (caches / "child_a" / "f").write_text("x" * 4096)
        (caches / "child_b").mkdir(parents=True)
        (caches / "child_b" / "f").write_text("y" * 4096)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"enabled_categories": ["caches", "docker"]}))
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_empty_only_lists_top_level_children(self):
        r = self.run_cli("clean", "--dry-run", "--targets", "general-caches", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        item = next(i for i in data["items"] if i["id"] == "general-caches")
        names = {Path(p["path"]).name for p in item["paths"]}
        self.assertEqual(names, {"child_a", "child_b"})
        self.assertTrue((self.home / "Library/Caches").exists(),
                        "dry run must not actually delete anything")

    def test_cmd_target_reports_would_run_without_executing(self):
        r = self.run_cli("clean", "--dry-run", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        item = next(i for i in data["items"] if i["id"] == "docker-prune")
        self.assertEqual(item["status"], "would-run")
        self.assertIn("docker system prune", item["cmd"])
        self.assertEqual(item["paths"], [])


class TestSnapshotScope(unittest.TestCase):
    """run_clean's snapshot_scope='full' remaining-targets computation must
    exclude what was cleaned and retain what wasn't."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        self.orig_home = cleaner.HOME
        self.orig_log = cleaner.LOG_PATH
        self.orig_snap = cleaner.SNAPSHOTS_PATH
        cleaner.HOME = self.fake_home
        cleaner.LOG_PATH = self.tmp / "report.log"
        cleaner.SNAPSHOTS_PATH = self.tmp / "snapshots.log"

    def tearDown(self):
        cleaner.HOME = self.orig_home
        cleaner.LOG_PATH = self.orig_log
        cleaner.SNAPSHOTS_PATH = self.orig_snap
        shutil.rmtree(self.tmp, ignore_errors=True)

    def target(self, tid, category, path, safe=True):
        return {"id": tid, "category": category, "label": tid, "description": "",
                "path": Path(path), "glob": None, "safe": safe, "cmd": None,
                "estimate_cmd": None, "estimate_parser": None, "empty_only": False}

    def test_full_scope_non_null_and_excludes_cleaned_retains_uncleaned(self):
        a = self.fake_home / "a"
        a.mkdir()
        (a / "f").write_text("x" * 4096)
        b = self.fake_home / "b"
        b.mkdir()
        (b / "f").write_text("y" * 4096)
        targets = [self.target("clean-me", "node", a, safe=True),
                   self.target("keep-me", "python", b, safe=False)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cleaner.run_clean(targets, auto_approve=True, json_mode=True, explicit=False,
                              snapshot_scope="full")
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertIsNotNone(snaps[0]["reclaimable_bytes"])
        self.assertIsNotNone(snaps[0]["categories"])
        self.assertNotIn("node", snaps[0]["categories"],
                         "the cleaned (safe) target's category must be excluded")
        self.assertIn("python", snaps[0]["categories"],
                      "the skipped (review) target's category must be retained")
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())

    def test_partial_scope_records_null(self):
        a = self.fake_home / "a"
        a.mkdir()
        (a / "f").write_text("x" * 4096)
        targets = [self.target("clean-me", "node", a, safe=True)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cleaner.run_clean(targets, auto_approve=True, json_mode=True, explicit=False,
                              snapshot_scope="partial")
        snaps = cleaner.load_snapshots()
        self.assertEqual(len(snaps), 1)
        self.assertIsNone(snaps[0]["reclaimable_bytes"])
        self.assertIsNone(snaps[0]["categories"])


class TestScanSnapshotScope(unittest.TestCase):
    """Nothing previously asserted that an unscoped `scan` records non-null
    reclaimable_bytes/categories while a scoped one nulls them out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".npm" / "_cacache").mkdir(parents=True)
        (self.home / ".npm" / "_cacache" / "blob").write_text("x" * 4096)
        cfg_path = self.tmp / "config.json"
        cfg_path.write_text(json.dumps({"enabled_categories": ["node"]}))
        self.snap_path = self.tmp / "snapshots.log"
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.snap_path)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def test_unscoped_scan_records_non_null(self):
        r = self.run_cli("scan", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        snaps = json.loads(self.snap_path.read_text())
        self.assertEqual(len(snaps), 1)
        self.assertIsNotNone(snaps[0]["reclaimable_bytes"])
        self.assertIsNotNone(snaps[0]["categories"])

    def test_category_scoped_scan_records_null(self):
        r = self.run_cli("scan", "--category", "node", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        snaps = json.loads(self.snap_path.read_text())
        self.assertEqual(len(snaps), 1)
        self.assertIsNone(snaps[0]["reclaimable_bytes"])
        self.assertIsNone(snaps[0]["categories"])

    def test_min_size_scoped_scan_records_null(self):
        r = self.run_cli("scan", "--min-size", "0", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        snaps = json.loads(self.snap_path.read_text())
        self.assertEqual(len(snaps), 1)
        self.assertIsNone(snaps[0]["reclaimable_bytes"])
        self.assertIsNone(snaps[0]["categories"])


class TestStatePathFallback(unittest.TestCase):
    """SNAPSHOTS_PATH/LOG_PATH must fall back to
    ~/Library/Application Support/MacCleaner when the directory beside
    cleaner.py isn't writable (the bundled-engine-in-a-signed-.app case),
    while the env override always wins regardless."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        self.orig_home = cleaner.HOME
        cleaner.HOME = self.fake_home

    def tearDown(self):
        cleaner.HOME = self.orig_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                      "root bypasses directory write-permission checks")
    def test_falls_back_when_script_dir_not_writable(self):
        readonly_dir = self.tmp / "bundle_resources"
        readonly_dir.mkdir()
        os.chmod(readonly_dir, 0o555)
        try:
            path = cleaner._resolve_state_path("MACCLEANER_LOG_TEST_UNSET", "report.log",
                                               script_dir=readonly_dir)
            expected = self.fake_home / "Library/Application Support/MacCleaner/report.log"
            self.assertEqual(path, expected)
            self.assertTrue(expected.parent.is_dir(), "fallback dir must be created")
        finally:
            os.chmod(readonly_dir, 0o755)

    def test_uses_script_dir_when_writable(self):
        writable_dir = self.tmp / "mac-cleaner"
        writable_dir.mkdir()
        path = cleaner._resolve_state_path("MACCLEANER_LOG_TEST_UNSET", "report.log",
                                           script_dir=writable_dir)
        self.assertEqual(path, writable_dir / "report.log")

    def test_falls_back_when_inside_app_bundle_even_if_writable(self):
        # A user-owned .app's Contents/Resources is drwxr-xr-x (writable),
        # but state files must never land inside the bundle.
        bundle_resources = self.tmp / "MacCleaner.app" / "Contents" / "Resources"
        bundle_resources.mkdir(parents=True)
        self.assertTrue(os.access(bundle_resources, os.W_OK))
        path = cleaner._resolve_state_path("MACCLEANER_LOG_TEST_UNSET", "report.log",
                                           script_dir=bundle_resources)
        expected = self.fake_home / "Library/Application Support/MacCleaner/report.log"
        self.assertEqual(path, expected)
        self.assertTrue(expected.parent.is_dir(), "fallback dir must be created")

    def test_env_override_wins_even_when_inside_app_bundle(self):
        bundle_resources = self.tmp / "MacCleaner.app" / "Contents" / "Resources"
        bundle_resources.mkdir(parents=True)
        override = str(self.tmp / "custom-report.log")
        os.environ["MACCLEANER_LOG_TEST_OVERRIDE"] = override
        try:
            path = cleaner._resolve_state_path("MACCLEANER_LOG_TEST_OVERRIDE", "report.log",
                                               script_dir=bundle_resources)
            self.assertEqual(path, Path(override))
        finally:
            os.environ.pop("MACCLEANER_LOG_TEST_OVERRIDE", None)

    def test_is_inside_app_bundle_detection(self):
        self.assertTrue(cleaner._is_inside_app_bundle(
            Path("/Applications/MacCleaner.app/Contents/Resources")))
        self.assertTrue(cleaner._is_inside_app_bundle(
            Path.home() / "Downloads/MacCleaner.app/Contents/Resources"))
        self.assertFalse(cleaner._is_inside_app_bundle(
            Path.home() / "mac-cleaner"))
        self.assertFalse(cleaner._is_inside_app_bundle(
            Path("/Users/dev/Code/MacCleaner")))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                      "root bypasses directory write-permission checks")
    def test_env_override_wins_even_when_script_dir_not_writable(self):
        readonly_dir = self.tmp / "bundle_resources2"
        readonly_dir.mkdir()
        os.chmod(readonly_dir, 0o555)
        override = str(self.tmp / "custom-report.log")
        os.environ["MACCLEANER_LOG_TEST_OVERRIDE"] = override
        try:
            path = cleaner._resolve_state_path("MACCLEANER_LOG_TEST_OVERRIDE", "report.log",
                                               script_dir=readonly_dir)
            self.assertEqual(path, Path(override))
        finally:
            os.environ.pop("MACCLEANER_LOG_TEST_OVERRIDE", None)
            os.chmod(readonly_dir, 0o755)


class TestDryRunPermissionGuard(unittest.TestCase):
    """run_dry_run's empty_only expansion must guard p.iterdir() the same way
    delete_target guards the equivalent loop — a PermissionError previewing
    one target must not abort the whole preview with a traceback."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fake_home = self.tmp / "home"
        self.fake_home.mkdir()
        self.orig_home = cleaner.HOME
        cleaner.HOME = self.fake_home

    def tearDown(self):
        cleaner.HOME = self.orig_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                      "root bypasses directory permission checks")
    def test_empty_only_permission_error_does_not_abort_preview(self):
        d = self.fake_home / "Caches"
        d.mkdir()
        (d / "readable").mkdir()
        os.chmod(d, 0o000)  # no read/execute -> p.iterdir() raises PermissionError
        try:
            t = {"id": "t", "category": "test", "label": "t", "description": "",
                 "path": d, "glob": None, "safe": True, "cmd": None,
                 "estimate_cmd": None, "estimate_parser": None, "empty_only": True}
            with contextlib.redirect_stdout(io.StringIO()):
                total, items = cleaner.run_dry_run([t])
            self.assertEqual(total, 0)
            self.assertEqual(items[0]["paths"], [])
        finally:
            os.chmod(d, 0o755)


class TestNotify(unittest.TestCase):
    def test_new_config_defaults(self):
        cfg = json.loads(json.dumps(cleaner.DEFAULT_CONFIG))
        self.assertTrue(cfg["notifications"])
        self.assertTrue(cfg["low_disk_alerts"])
        self.assertEqual(cfg["low_disk_threshold_gb"], 10)
        self.assertEqual(cfg["full_refresh_hours"], 6)

    def test_new_keys_merge_into_old_config(self):
        tmp = Path(tempfile.mkdtemp())
        orig = cleaner.CONFIG_PATH
        cleaner.CONFIG_PATH = tmp / "config.json"
        try:
            cleaner.CONFIG_PATH.write_text('{"enabled_categories": ["node"]}')
            cfg = cleaner.load_config()
            self.assertEqual(cfg["enabled_categories"], ["node"])
            self.assertTrue(cfg["low_disk_alerts"])
            self.assertEqual(cfg["low_disk_threshold_gb"], 10)
        finally:
            cleaner.CONFIG_PATH = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_alerts_path_resolution(self):
        """The override wins; otherwise alerts.json sits beside cleaner.py.

        The "installed" directory is a real tempdir rather than ~/mac-cleaner:
        the sibling-directory branch requires the directory to exist and be
        writable, so pointing at a path that happens to exist on a developer's
        machine but not on a fresh CI runner would fall through to the
        Application Support fallback and fail there only."""
        tmp = Path(tempfile.mkdtemp())
        override = tmp / "override.json"
        try:
            self.assertEqual(
                cleaner._resolve_state_path("MACCLEANER_ALERTS", "alerts.json", tmp),
                tmp / "alerts.json")
            os.environ["MACCLEANER_ALERTS"] = str(override)
            try:
                self.assertEqual(
                    cleaner._resolve_state_path("MACCLEANER_ALERTS", "alerts.json", tmp),
                    override)
            finally:
                del os.environ["MACCLEANER_ALERTS"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_escape_applescript(self):
        self.assertEqual(cleaner._escape_applescript('say "hi"'), 'say \\"hi\\"')
        self.assertEqual(cleaner._escape_applescript(r"back\slash"), r"back\\slash")
        self.assertEqual(cleaner._escape_applescript("plain"), "plain")

    def test_notify_argv_shape(self):
        argv = cleaner._notify_argv("MacCleaner freed 1.0 GB", 'a "quoted" note')
        self.assertEqual(argv[0], "osascript")
        self.assertEqual(argv[1], "-e")
        self.assertIn('display notification "a \\"quoted\\" note"', argv[2])
        self.assertIn('with title "MacCleaner freed 1.0 GB"', argv[2])
        self.assertEqual(len(argv), 3)

    def test_notify_survives_missing_binary(self):
        """A notification failure must never raise into the caller."""
        orig = cleaner._notify_argv
        cleaner._notify_argv = lambda t, m: ["definitely-not-a-real-binary-xyz"]
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                result = cleaner._notify("t", "m")
            self.assertFalse(result)
            self.assertIn("notif", err.getvalue().lower())
        finally:
            cleaner._notify_argv = orig


class TestCleanNotify(unittest.TestCase):
    """--notify must be observable without posting a real notification: the
    tests capture the argv _notify would have used by swapping the primitive."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".npm" / "_cacache").mkdir(parents=True)
        (self.home / ".npm" / "_cacache" / "blob").write_text("x" * 4096)
        self.cfg_path = self.tmp / "config.json"
        self.env = {**os.environ, "HOME": str(self.home),
                    "MACCLEANER_CONFIG": str(self.cfg_path),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log"),
                    "MACCLEANER_ALERTS": str(self.tmp / "alerts.json")}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_cfg(self, **extra):
        cfg = {"enabled_categories": ["node"]}
        cfg.update(extra)
        self.cfg_path.write_text(json.dumps(cfg))

    def run_cli(self, *args, fake_osascript=True):
        """Run the CLI with a stub `osascript` early on PATH that records its
        argv to a file, so we can assert on the notification without posting."""
        env = dict(self.env)
        if fake_osascript:
            bindir = self.tmp / "bin"
            bindir.mkdir(exist_ok=True)
            recorded = self.tmp / "notified.txt"
            stub = bindir / "osascript"
            stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$RECORD_FILE"\n')
            stub.chmod(0o755)
            env["PATH"] = f"{bindir}:{env['PATH']}"
            env["RECORD_FILE"] = str(recorded)
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def notified_text(self):
        f = self.tmp / "notified.txt"
        return f.read_text() if f.exists() else ""

    def test_notify_posts_after_clean(self):
        self.write_cfg()
        r = self.run_cli("clean", "--yes", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertGreater(data["freed_bytes"], 0)
        self.assertIn("display notification", self.notified_text())
        self.assertIn("MacCleaner", self.notified_text())

    def test_notifications_disabled_suppresses(self):
        self.write_cfg(notifications=False)
        r = self.run_cli("clean", "--yes", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "",
                         "notifications:false must suppress the notification")
        self.assertFalse((self.home / ".npm" / "_cacache").exists(),
                         "the clean itself must still happen")

    def test_no_flag_no_notification(self):
        self.write_cfg()
        r = self.run_cli("clean", "--yes", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "")

    def test_dry_run_never_notifies(self):
        self.write_cfg()
        r = self.run_cli("clean", "--dry-run", "--notify", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.notified_text(), "")
        self.assertTrue((self.home / ".npm" / "_cacache").exists())


class TestLowDiskThrottle(unittest.TestCase):
    """_low_disk_decision is pure: (alerts, now, free, threshold) -> (notify?, state)."""

    def setUp(self):
        self.now = datetime.datetime(2026, 7, 30, 12, 0, 0)
        self.threshold = 10 * 1024**3

    def decide(self, alerts, free, now=None):
        return cleaner._low_disk_decision(alerts, now or self.now, free, self.threshold)

    def test_first_dip_notifies(self):
        notify, state = self.decide({}, 5 * 1024**3)
        self.assertTrue(notify)
        self.assertEqual(state["state"], "below")
        self.assertEqual(state["last_notified"], self.now.isoformat())

    def test_above_threshold_never_notifies(self):
        notify, state = self.decide({}, 500 * 1024**3)
        self.assertFalse(notify)
        self.assertEqual(state["state"], "above")

    def test_still_below_within_24h_stays_quiet(self):
        alerts = {"low_disk": {"state": "below",
                               "last_notified": (self.now - datetime.timedelta(hours=3)).isoformat()}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertFalse(notify, "must not re-notify hourly")
        self.assertEqual(state["state"], "below")

    def test_still_below_after_24h_renotifies(self):
        alerts = {"low_disk": {"state": "below",
                               "last_notified": (self.now - datetime.timedelta(hours=25)).isoformat()}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify)
        self.assertEqual(state["last_notified"], self.now.isoformat())

    def test_recovery_then_new_dip_notifies_immediately(self):
        recovered = {"low_disk": {"state": "below",
                                  "last_notified": (self.now - datetime.timedelta(hours=1)).isoformat()}}
        notify, state = self.decide(recovered, 500 * 1024**3)
        self.assertFalse(notify)
        self.assertEqual(state["state"], "above")
        notify2, _ = self.decide({"low_disk": state}, 5 * 1024**3)
        self.assertTrue(notify2, "a fresh dip after recovery must notify at once")

    def test_corrupt_last_notified_notifies(self):
        alerts = {"low_disk": {"state": "below", "last_notified": "not-a-timestamp"}}
        notify, _ = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify)

    def test_below_state_missing_stamp_notifies(self):
        """A hand-edited or partially written alerts.json can have `state`
        present but no `last_notified` at all — distinct from a corrupt
        (non-empty but unparseable) stamp. Must still notify."""
        alerts = {"low_disk": {"state": "below", "last_notified": None}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify)
        self.assertEqual(state["last_notified"], self.now.isoformat())

    def test_still_below_at_exact_renotify_boundary_renotifies(self):
        """elapsed >= LOW_DISK_RENOTIFY_HOURS uses >=, so exactly 24h must
        renotify, not just times strictly greater than it."""
        alerts = {"low_disk": {"state": "below",
                               "last_notified": (self.now - datetime.timedelta(
                                   hours=cleaner.LOW_DISK_RENOTIFY_HOURS)).isoformat()}}
        notify, state = self.decide(alerts, 5 * 1024**3)
        self.assertTrue(notify, "exactly the renotify window must still renotify")
        self.assertEqual(state["last_notified"], self.now.isoformat())


class TestRunDiskCheckPersistence(unittest.TestCase):
    """run_disk_check's own persistence decisions (M3, M5) — direct calls
    against a swapped ALERTS_PATH, not a subprocess, so _notify can be
    monkeypatched to simulate a failure."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.orig_alerts_path = cleaner.ALERTS_PATH
        cleaner.ALERTS_PATH = self.tmp / "alerts.json"

    def tearDown(self):
        cleaner.ALERTS_PATH = self.orig_alerts_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failed_notification_does_not_stamp_throttle(self):
        """A failed notification used to stamp last_notified anyway,
        suppressing retries for 24h after a banner the user never saw
        (finding M5). It must be retried on the very next run instead."""
        orig_notify = cleaner._notify
        cleaner._notify = lambda title, message: False
        try:
            cfg = {"low_disk_alerts": True, "low_disk_threshold_gb": 10_000_000}
            with contextlib.redirect_stdout(io.StringIO()):
                r1 = cleaner.run_disk_check(cfg, json_mode=True)
            self.assertTrue(r1["below_threshold"])
            self.assertFalse(r1["notified"])
            self.assertFalse(cleaner.ALERTS_PATH.exists(),
                             "a failed notification must not persist a stamp")

            with contextlib.redirect_stdout(io.StringIO()):
                r2 = cleaner.run_disk_check(cfg, json_mode=True)
            self.assertFalse(r2["notified"], "the stub keeps failing")
            self.assertFalse(cleaner.ALERTS_PATH.exists(),
                             "still no stamp — the next run (with a working "
                             "notifier) will retry immediately")
        finally:
            cleaner._notify = orig_notify


class TestDiskCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.alerts = self.tmp / "alerts.json"
        self.log = self.tmp / "report.log"
        self.snaps = self.tmp / "snapshots.log"
        self.cfg_path = self.tmp / "config.json"
        self.env = {**os.environ, "HOME": str(self.tmp),
                    "MACCLEANER_CONFIG": str(self.cfg_path),
                    "MACCLEANER_LOG": str(self.log),
                    "MACCLEANER_SNAPSHOTS": str(self.snaps),
                    "MACCLEANER_ALERTS": str(self.alerts)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args, **cfg):
        self.cfg_path.write_text(json.dumps(cfg) if cfg else "{}")
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "osascript"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$RECORD_FILE"\n')
        stub.chmod(0o755)
        env = {**self.env, "PATH": f"{bindir}:{os.environ['PATH']}",
               "RECORD_FILE": str(self.tmp / "notified.txt")}
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=env, timeout=60)

    def notified(self):
        f = self.tmp / "notified.txt"
        return f.read_text() if f.exists() else ""

    def test_json_shape_and_exit_zero(self):
        r = self.run_cli("disk-check", "--json")
        self.assertEqual(r.returncode, 0, "disk-check is a monitor: always exit 0")
        data = json.loads(r.stdout)
        for key in ["free_bytes", "free_human", "threshold_bytes",
                    "below_threshold", "notified"]:
            self.assertIn(key, data)
        self.assertIsInstance(data["below_threshold"], bool)

    def test_huge_threshold_triggers_notification(self):
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertTrue(data["below_threshold"])
        self.assertTrue(data["notified"])
        self.assertIn("display notification", self.notified())
        self.assertTrue(self.alerts.exists())
        state = json.loads(self.alerts.read_text())["low_disk"]
        self.assertEqual(state["state"], "below")

    def test_alerts_disabled_reports_but_stays_quiet(self):
        r = self.run_cli("disk-check", "--json",
                         low_disk_threshold_gb=10_000_000, low_disk_alerts=False)
        data = json.loads(r.stdout)
        self.assertTrue(data["below_threshold"], "numbers still reported")
        self.assertFalse(data["notified"])
        self.assertEqual(self.notified(), "")
        self.assertFalse(self.alerts.exists(),
                          "disabled alerts must not persist a stamp, so re-enabling "
                          "later doesn't inherit a stale 'already warned' state")

    def test_malformed_threshold_falls_back_and_warns(self):
        """A non-numeric low_disk_threshold_gb (e.g. hand-edited via `config set`
        with no type validation) must degrade to the documented 10 GB default
        instead of crashing the hourly monitor."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb="high")
        self.assertEqual(r.returncode, 0, "disk-check must always exit 0")
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertEqual(data["free_human"], cleaner.fmt_size(data["free_bytes"]),
                          "numbers must still be usable, not just present")
        self.assertIn("low_disk_threshold_gb", r.stderr)
        self.assertIn("high", r.stderr)

    def test_structurally_wrong_threshold_falls_back_and_warns(self):
        """A list or null (TypeError from float()) must also degrade cleanly,
        not just a non-numeric string (ValueError)."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=None)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertIn("low_disk_threshold_gb", r.stderr)

    def test_numeric_string_threshold_still_works(self):
        """Happy path must be unchanged: a numeric string like "15" still
        parses via float() with no warning."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb="15")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], int(15 * 1024**3))
        self.assertNotIn("low_disk_threshold_gb", r.stderr)

    def test_nan_threshold_falls_back_and_warns(self):
        """json permits the NaN literal, and `config set low_disk_threshold_gb NaN`
        writes a real float('nan') that round-trips through load_config(). float(nan)
        doesn't raise, so this must be caught before int(nan * 1024**3), which raises
        ValueError: cannot convert float NaN to integer."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=float("nan"))
        self.assertEqual(r.returncode, 0, "disk-check must always exit 0")
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertEqual(data["free_human"], cleaner.fmt_size(data["free_bytes"]),
                          "numbers must still be usable, not just present")
        self.assertIn("low_disk_threshold_gb", r.stderr)
        self.assertIn("nan", r.stderr.lower())

    def test_infinity_threshold_falls_back_and_warns(self):
        """float(inf) doesn't raise either, but int(inf * 1024**3) raises
        OverflowError: cannot convert float infinity to integer — a third
        exception type that must also be caught."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=float("inf"))
        self.assertEqual(r.returncode, 0, "disk-check must always exit 0")
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertEqual(data["free_human"], cleaner.fmt_size(data["free_bytes"]))
        self.assertIn("low_disk_threshold_gb", r.stderr)
        self.assertIn("inf", r.stderr.lower())

    def test_negative_infinity_threshold_falls_back_and_warns(self):
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=float("-inf"))
        self.assertEqual(r.returncode, 0, "disk-check must always exit 0")
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertEqual(data["free_human"], cleaner.fmt_size(data["free_bytes"]))
        self.assertIn("low_disk_threshold_gb", r.stderr)
        self.assertIn("inf", r.stderr.lower())

    def test_no_side_effect_files(self):
        r = self.run_cli("disk-check", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.log.exists(), "disk-check must not write report.log")
        self.assertFalse(self.snaps.exists(), "disk-check must not record a snapshot")

    def test_corrupt_alerts_file_self_heals(self):
        self.alerts.write_text("{not json")
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["notified"])
        self.assertIn("low_disk", json.loads(self.alerts.read_text()))

    def test_human_output(self):
        r = self.run_cli("disk-check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("free", r.stdout.lower())

    def test_second_run_stays_quiet_immediately_after_first(self):
        """_low_disk_decision is exhaustively covered as a pure function, but
        nothing previously invoked disk-check twice in a row, so the wiring in
        run_disk_check (load_alerts -> decision -> save_alerts under
        "low_disk" -> the enabled-and-should_notify gate) was only half
        verified (finding I5)."""
        r1 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertTrue(json.loads(r1.stdout)["notified"])
        notified_path = self.tmp / "notified.txt"
        if notified_path.exists():
            notified_path.unlink()

        r2 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        data2 = json.loads(r2.stdout)
        self.assertTrue(data2["below_threshold"])
        self.assertFalse(data2["notified"],
                         "an immediate second run must stay quiet (throttled)")
        self.assertEqual(self.notified(), "",
                         "no new notification must be posted while throttled")

    def test_backdated_last_notified_renotifies(self):
        r1 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        self.assertTrue(json.loads(r1.stdout)["notified"])

        alerts = json.loads(self.alerts.read_text())
        stale = datetime.datetime.now() - datetime.timedelta(
            hours=cleaner.LOW_DISK_RENOTIFY_HOURS + 1)
        alerts["low_disk"]["last_notified"] = stale.isoformat()
        self.alerts.write_text(json.dumps(alerts))
        notified_path = self.tmp / "notified.txt"
        if notified_path.exists():
            notified_path.unlink()

        r2 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=10_000_000)
        data2 = json.loads(r2.stdout)
        self.assertTrue(data2["notified"],
                        "a back-dated last_notified past the renotify window must renotify")
        self.assertIn("display notification", self.notified())

    def test_negative_threshold_falls_back_and_warns(self):
        """math.isfinite(-5) is True, so the NaN/infinity guard alone lets a
        negative low_disk_threshold_gb (e.g. `config set low_disk_threshold_gb
        -5`) through, making below_threshold permanently False (finding M8)."""
        r = self.run_cli("disk-check", "--json", low_disk_threshold_gb=-5)
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["threshold_bytes"], 10 * 1024**3)
        self.assertIn("low_disk_threshold_gb", r.stderr)
        self.assertIn("-5", r.stderr)

    def test_unchanged_state_does_not_rewrite_alerts_file(self):
        """The `above` branch used to stamp alerts.json unconditionally, so an
        hourly agent rewrote the file every run even when nothing changed
        (finding M3)."""
        r1 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=0.0000001)
        self.assertFalse(json.loads(r1.stdout)["below_threshold"])
        self.assertTrue(self.alerts.exists())
        first_mtime = self.alerts.stat().st_mtime_ns

        r2 = self.run_cli("disk-check", "--json", low_disk_threshold_gb=0.0000001)
        self.assertFalse(json.loads(r2.stdout)["below_threshold"])
        second_mtime = self.alerts.stat().st_mtime_ns
        self.assertEqual(first_mtime, second_mtime,
                         "an unchanged low-disk state must not rewrite alerts.json")


class TestScheduler(unittest.TestCase):
    """scheduler.sh against a sandboxed LaunchAgents dir with stub
    launchctl/crontab on PATH — never touches the real agents or crontab."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.calls = self.tmp / "calls.txt"
        self.crontab_file = self.tmp / "crontab.txt"
        self.crontab_file.write_text("")
        for name in ("launchctl", "crontab"):
            stub = self.bindir / name
            stub.write_text(
                '#!/bin/sh\n'
                f'printf "{name} %s\\n" "$*" >> "$CALLS_FILE"\n'
                'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
                'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > "$CRONTAB_FILE"; fi\n'
                'exit 0\n')
            stub.chmod(0o755)
        self.env = {**os.environ,
                    "PATH": f"{self.bindir}:{os.environ['PATH']}",
                    "HOME": str(self.tmp),
                    "MACCLEANER_LAUNCH_AGENTS_DIR": str(self.agents),
                    "CALLS_FILE": str(self.calls),
                    "CRONTAB_FILE": str(self.crontab_file)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sched(self, *args):
        return subprocess.run(["bash", str(REPO / "scheduler.sh"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def plist(self, label):
        return self.agents / f"com.fullex.maccleaner.{label}.plist"

    def test_weekly_installs_both_agents(self):
        r = self.sched("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.plist("clean").exists(), "clean agent missing")
        self.assertTrue(self.plist("diskwatch").exists(), "diskwatch agent missing")

    def test_plists_are_valid(self):
        self.sched("weekly")
        for label in ("clean", "diskwatch"):
            lint = subprocess.run(["plutil", "-lint", str(self.plist(label))],
                                  capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, f"{label}: {lint.stdout}{lint.stderr}")

    def test_clean_agent_content(self):
        self.sched("weekly")
        body = self.plist("clean").read_text()
        self.assertIn("com.fullex.maccleaner.clean", body)
        self.assertIn("cleaner.py", body)
        self.assertIn("--notify", body)
        self.assertIn("StartCalendarInterval", body)
        self.assertIn("<key>Weekday</key>", body)

    def test_monthly_uses_day_not_weekday(self):
        self.sched("monthly")
        body = self.plist("clean").read_text()
        self.assertIn("<key>Day</key>", body)
        self.assertNotIn("<key>Weekday</key>", body)

    def test_diskwatch_agent_content(self):
        self.sched("weekly")
        body = self.plist("diskwatch").read_text()
        self.assertIn("disk-check", body)
        self.assertIn("StartInterval", body)
        self.assertIn("3600", body)

    def test_agents_carry_tool_path(self):
        """Both agents must set EnvironmentVariables/PATH to the same list the
        app's CleanerBridge.runEngine uses, or cmd-based targets (brew, docker,
        pnpm, gem, conda, xcrun simctl, ...) silently no-op under a scheduled
        run because launchd's default PATH is just /usr/bin:/bin:/usr/sbin:/sbin
        (finding I3)."""
        self.sched("weekly")
        for label in ("clean", "diskwatch"):
            body = self.plist(label).read_text()
            self.assertIn("EnvironmentVariables", body)
            self.assertIn("/opt/homebrew/bin", body)
            self.assertIn("/usr/local/bin", body)

    def test_remove_deletes_both(self):
        self.sched("weekly")
        r = self.sched("remove")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.plist("clean").exists())
        self.assertFalse(self.plist("diskwatch").exists())

    def test_reinstall_replaces_not_stacks(self):
        self.sched("weekly")
        self.sched("monthly")
        body = self.plist("clean").read_text()
        self.assertIn("<key>Day</key>", body)
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)

    def test_migrates_cron_weekly(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.sched("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("migrat", (r.stdout + r.stderr).lower())
        self.assertTrue(self.plist("clean").exists())
        self.assertIn("<key>Weekday</key>", self.plist("clean").read_text())
        self.assertNotIn("cleaner.py", self.crontab_file.read_text(),
                         "the cron line must be removed after migration")

    def test_migration_preserves_monthly(self):
        """When the requested cadence matches what the old cron line implies,
        migration must install exactly once (finding I2 — migrate_cron used to
        call install_schedule itself with its *detected* cadence, and the
        outer case statement called install_schedule again with the
        *argument*, printing two, possibly contradictory, schedule lines and
        bootstrapping each agent twice)."""
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.sched("monthly")
        self.assertEqual(r.returncode, 0, r.stderr)
        output = r.stdout + r.stderr
        self.assertEqual(output.count("✅ Scheduled"), 1,
                         "migration must install exactly once")
        self.assertIn("<key>Day</key>", self.plist("clean").read_text())

    def test_migration_reports_detected_cadence_but_argument_wins(self):
        """A monthly-shaped cron line migrated via `weekly` must not install
        monthly behind the scenes — the explicit command always wins, and the
        cron line's own cadence is only ever reported, never installed
        (finding I2)."""
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.sched("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        output = r.stdout + r.stderr
        self.assertEqual(output.count("✅ Scheduled"), 1,
                         "migration must install exactly once, even on a cadence mismatch")
        self.assertIn("monthly", output.lower(),
                     "the detected cadence should still be reported for visibility")
        body = self.plist("clean").read_text()
        self.assertIn("<key>Weekday</key>", body,
                     "the requested cadence (weekly) must win, not the detected one (monthly)")
        self.assertNotIn("<key>Day</key>", body)

    def test_migration_keeps_unrelated_cron_lines(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "*/5 * * * * /usr/local/bin/other-job\n")
        self.sched("weekly")
        remaining = self.crontab_file.read_text()
        self.assertIn("other-job", remaining, "unrelated cron jobs must survive")
        self.assertNotIn("cleaner.py", remaining)

    def test_migration_is_idempotent(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.sched("weekly")
        second = self.sched("weekly")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotIn("migrat", second.stdout.lower(),
                         "second run has nothing to migrate")

    def test_status_is_read_only_with_legacy_cron(self):
        """status must only report a legacy cron line, never touch it or
        install anything — even when one is present (finding 1)."""
        cron_line = ("0 9 * * 1 /usr/bin/python3 "
                     "/Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.crontab_file.write_text(cron_line)
        r = self.sched("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Migrating your cron schedule", r.stdout + r.stderr,
                         "status must not perform migration")
        self.assertNotIn("Removed the old cron entry", r.stdout + r.stderr,
                         "status must not perform migration")
        self.assertIn("run ./scheduler.sh weekly to migrate",
                      (r.stdout + r.stderr))
        self.assertEqual(self.crontab_file.read_text(), cron_line,
                         "status must not touch the crontab")
        self.assertEqual(list(self.agents.glob("*.plist")), [],
                         "status must not create any launchd agents")

    def test_remove_strips_legacy_cron_without_migrating(self):
        """remove must not install anything, but should strip a legacy
        cron line since the user's intent is to stop scheduling entirely."""
        cron_line = ("0 9 * * 1 /usr/bin/python3 "
                     "/Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.crontab_file.write_text(cron_line)
        r = self.sched("remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("migrat", (r.stdout + r.stderr).lower())
        self.assertNotIn("cleaner.py", self.crontab_file.read_text(),
                         "remove should strip the legacy cron line")
        self.assertEqual(list(self.agents.glob("*.plist")), [],
                         "remove must not install any launchd agents")

    def test_bare_invocation_does_not_migrate(self):
        cron_line = ("0 9 * * 1 /usr/bin/python3 "
                     "/Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        self.crontab_file.write_text(cron_line)
        r = self.sched()
        self.assertEqual(self.crontab_file.read_text(), cron_line)
        self.assertEqual(list(self.agents.glob("*.plist")), [])

    def test_status_reports_installed(self):
        self.sched("weekly")
        r = self.sched("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("maccleaner", r.stdout.lower())

    def test_usage_when_no_command(self):
        r = self.sched()
        self.assertIn("weekly", r.stdout)
        self.assertIn("monthly", r.stdout)

    def test_launchctl_failure_surfaces_message_and_propagates(self):
        """A launchctl that fails to load an agent must surface its real
        stderr diagnostic and make scheduler.sh exit non-zero, instead of
        printing a generic warning immediately followed by a success
        banner (finding 2)."""
        failing = self.bindir / "launchctl"
        failing.write_text(
            '#!/bin/sh\n'
            f'printf "launchctl %s\\n" "$*" >> "$CALLS_FILE"\n'
            'case "$1" in\n'
            '  bootstrap|load)\n'
            '    echo "Load failed: 5: Input/output error" >&2\n'
            '    exit 1\n'
            '    ;;\n'
            'esac\n'
            'exit 0\n')
        failing.chmod(0o755)

        r = self.sched("weekly")

        self.assertNotEqual(r.returncode, 0,
                            "a failed launchctl load must not exit 0")
        self.assertIn("Load failed: 5: Input/output error", r.stderr,
                     "the real launchctl diagnostic must be surfaced")
        self.assertNotIn("✅ Scheduled", r.stdout,
                         "must not print a success banner after a load failure")
        # The plist is still written so the user can load it manually.
        self.assertTrue(self.plist("clean").exists())

    def test_status_does_not_checkmark_a_plist_launchd_has_not_loaded(self):
        """A plist can exist on disk (bootstrap once succeeded, or it was
        written but never loaded) without launchd actually having the job
        loaded right now. status used to check only `-f plist`, so it kept
        showing ✅ seconds after scheduler.sh itself printed a load failure.
        It must instead ask launchd directly (finding I1)."""
        self.sched("weekly")
        not_loaded = self.bindir / "launchctl"
        not_loaded.write_text(
            '#!/bin/sh\n'
            f'printf "launchctl %s\\n" "$*" >> "$CALLS_FILE"\n'
            'if [ "$1" = "list" ]; then exit 1; fi\n'
            'exit 0\n')
        not_loaded.chmod(0o755)

        r = self.sched("status")

        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("✅", r.stdout,
                         "must not show a checkmark when launchctl list fails")
        self.assertIn("not loaded", (r.stdout + r.stderr).lower())
        # Still distinguishable from "nothing installed at all".
        self.assertNotIn("Not scheduled", r.stdout)

    def test_third_party_cron_line_survives_migration(self):
        """An unanchored `grep -v cleaner.py` would also strip a user's own
        `db-cleaner.py` cron job. Only MacCleaner's own line may be touched
        (finding I6)."""
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "0 3 * * * /Users/x/bin/db-cleaner.py\n")
        r = self.sched("weekly")
        self.assertEqual(r.returncode, 0, r.stderr)
        remaining = self.crontab_file.read_text()
        self.assertIn("db-cleaner.py", remaining,
                     "a third-party cron job must survive migration")
        self.assertNotIn("mac-cleaner/cleaner.py", remaining)

    def test_third_party_cron_line_survives_remove(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "0 3 * * * /Users/x/bin/db-cleaner.py\n")
        r = self.sched("remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        remaining = self.crontab_file.read_text()
        self.assertIn("db-cleaner.py", remaining,
                     "a third-party cron job must survive remove")
        self.assertNotIn("mac-cleaner/cleaner.py", remaining)

    def test_status_ignores_third_party_cleaner_script(self):
        self.crontab_file.write_text("0 3 * * * /Users/x/bin/db-cleaner.py\n")
        r = self.sched("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("legacy cron entry", (r.stdout + r.stderr).lower())


class TestScheduleSubcommand(unittest.TestCase):
    """`cleaner.py schedule ...` against a fully sandboxed environment.
    Never touches the real crontab, agents, or launchd."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.agents = self.tmp / "LaunchAgents"
        self.agents.mkdir()
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.crontab_file = self.tmp / "crontab.txt"
        self.crontab_file.write_text("")
        self.loaded_flag = self.tmp / "loaded"   # exists => launchctl list succeeds
        self.loaded_flag.write_text("")
        launchctl = self.bindir / "launchctl"
        launchctl.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "list" ]; then [ -e "$LOADED_FLAG" ] && exit 0 || exit 1; fi\n'
            'exit 0\n')
        launchctl.chmod(0o755)
        crontab = self.bindir / "crontab"
        crontab.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
            'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > "$CRONTAB_FILE"; fi\n'
            'exit 0\n')
        crontab.chmod(0o755)
        self.env = {**os.environ,
                    "PATH": f"{self.bindir}:{os.environ['PATH']}",
                    "HOME": str(self.tmp),
                    "MACCLEANER_LAUNCH_AGENTS_DIR": str(self.agents),
                    "MACCLEANER_CONFIG": str(self.tmp / "config.json"),
                    "MACCLEANER_LOG": str(self.tmp / "report.log"),
                    "MACCLEANER_SNAPSHOTS": str(self.tmp / "snapshots.log"),
                    "MACCLEANER_ALERTS": str(self.tmp / "alerts.json"),
                    "CRONTAB_FILE": str(self.crontab_file),
                    "LOADED_FLAG": str(self.loaded_flag)}
        (self.tmp / "config.json").write_text("{}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(REPO / "cleaner.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def status_json(self):
        r = self.run_cli("schedule", "status", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def plist(self, label):
        return self.agents / f"com.fullex.maccleaner.{label}.plist"

    # ── status shapes ──────────────────────────────────────────────────────

    def test_status_empty(self):
        d = self.status_json()
        self.assertIsNone(d["schedule"])
        self.assertEqual(d["agents"], [])
        self.assertFalse(d["legacy_cron"])

    def test_status_after_weekly_install(self):
        r = self.run_cli("schedule", "weekly", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = self.status_json()
        self.assertEqual(d["schedule"], "weekly")
        labels = {a["label"]: a for a in d["agents"]}
        self.assertEqual(set(labels), {"com.fullex.maccleaner.clean",
                                       "com.fullex.maccleaner.diskwatch"})
        for a in labels.values():
            self.assertTrue(a["plist_present"])
            self.assertTrue(a["loaded"])

    def test_status_monthly(self):
        self.run_cli("schedule", "monthly", "--json")
        self.assertEqual(self.status_json()["schedule"], "monthly")

    def test_status_present_but_not_loaded(self):
        self.run_cli("schedule", "weekly", "--json")
        self.loaded_flag.unlink()          # launchctl list now exits 1
        d = self.status_json()
        self.assertEqual(d["schedule"], "weekly")
        for a in d["agents"]:
            self.assertTrue(a["plist_present"])
            self.assertFalse(a["loaded"])

    def test_status_reports_legacy_cron(self):
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        d = self.status_json()
        self.assertTrue(d["legacy_cron"])
        # status is read-only: the cron line must survive
        self.assertIn("mac-cleaner/cleaner.py", self.crontab_file.read_text())
        self.assertEqual(list(self.agents.glob("*.plist")), [])

    # ── install ────────────────────────────────────────────────────────────

    def test_install_writes_valid_plists(self):
        self.run_cli("schedule", "weekly", "--json")
        for label in ("clean", "diskwatch"):
            lint = subprocess.run(["plutil", "-lint", str(self.plist(label))],
                                  capture_output=True, text=True)
            self.assertEqual(lint.returncode, 0, lint.stdout + lint.stderr)

    def test_plists_are_mode_644(self):
        """tempfile.mkstemp creates 0600 and os.replace preserves it — every
        real plist in ~/Library/LaunchAgents (including MacCleaner's own
        live agents) is 0644, so the atomic-write path must restore that
        mode before the swap."""
        self.run_cli("schedule", "weekly", "--json")
        for label in ("clean", "diskwatch"):
            mode = oct(self.plist(label).stat().st_mode & 0o777)
            self.assertEqual(mode, oct(0o644), f"{label} plist has mode {mode}, expected 0o644")

    def test_venv_shaped_python3_first_on_path_is_not_used_in_plist(self):
        """Reproduces the real hazard end-to-end through `schedule weekly`:
        a venv-shaped python3 placed first on PATH (as an activated venv
        would do) must never be embedded in the plist. Either the real
        stable/base interpreter is used instead, or installation refuses
        outright — but the venv path itself must never be written."""
        fake_python = self.bindir / "python3"
        fake_python.write_text("#!/bin/sh\necho fake\n")
        fake_python.chmod(0o755)
        (self.tmp / "pyvenv.cfg").write_text("home = /usr/bin\n")

        r = self.run_cli("schedule", "weekly", "--json")
        if r.returncode == 0:
            import plistlib
            with open(self.plist("clean"), "rb") as f:
                p = plistlib.load(f)
            interpreter = p["ProgramArguments"][0]
            self.assertNotEqual(interpreter, str(fake_python),
                                "venv-shaped python3 must never be embedded in the plist")
            self.assertFalse(cleaner._is_venv_interpreter(interpreter))
        else:
            # No usable non-venv interpreter was found anywhere -- refusing
            # outright is correct too, as long as nothing was written.
            self.assertFalse(self.plist("clean").exists())
            self.assertIn("virtualenv", r.stderr.lower())

    def test_clean_plist_content(self):
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        with open(self.plist("clean"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["Label"], "com.fullex.maccleaner.clean")
        self.assertIn("--notify", p["ProgramArguments"])
        self.assertEqual(p["StartCalendarInterval"]["Weekday"], 1)
        self.assertEqual(p["StartCalendarInterval"]["Hour"], 9)
        self.assertIn("/opt/homebrew/bin", p["EnvironmentVariables"]["PATH"])
        # The interpreter must be the stable, unversioned `python3` that
        # shutil.which() resolves — not a version-pinned Homebrew path like
        # .../python@3.14/bin/python3.14, which brew-autoremove can delete
        # out from under a scheduled agent (finding: fragile interpreter
        # path). Derive the expectation from shutil.which() so this stays
        # correct on any machine, rather than hardcoding a path.
        expected = shutil.which("python3")
        self.assertIsNotNone(expected, "test host must have python3 on PATH")
        interpreter = p["ProgramArguments"][0]
        self.assertEqual(interpreter, expected)
        self.assertTrue(Path(interpreter).exists())
        self.assertNotRegex(interpreter, r"python@\d+\.\d+",
                            "must not be a version-pinned Homebrew formula path")
        self.assertNotRegex(Path(interpreter).name, r"^python\d+\.\d+$",
                            "must not be a version-suffixed binary like python3.14")
        self.assertIn("cleaner.py", p["ProgramArguments"][1])

    def test_monthly_uses_day_not_weekday(self):
        self.run_cli("schedule", "monthly", "--json")
        import plistlib
        with open(self.plist("clean"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["StartCalendarInterval"]["Day"], 1)
        self.assertNotIn("Weekday", p["StartCalendarInterval"])

    def test_diskwatch_plist_content(self):
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        with open(self.plist("diskwatch"), "rb") as f:
            p = plistlib.load(f)
        self.assertEqual(p["StartInterval"], 3600)
        self.assertIn("disk-check", p["ProgramArguments"])

    def test_reinstall_replaces(self):
        self.run_cli("schedule", "weekly", "--json")
        self.run_cli("schedule", "monthly", "--json")
        self.assertEqual(self.status_json()["schedule"], "monthly")
        self.assertEqual(len(list(self.agents.glob("*.plist"))), 2)

    def test_install_json_shape(self):
        r = self.run_cli("schedule", "weekly", "--json")
        d = json.loads(r.stdout)
        self.assertEqual(d["schedule"], "weekly")
        self.assertFalse(d["migrated_cron"])

    # ── cron migration ─────────────────────────────────────────────────────

    def test_install_migrates_cron(self):
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n"
            "0 3 * * * /Users/x/bin/db-cleaner.py\n")
        r = self.run_cli("schedule", "weekly", "--json")
        d = json.loads(r.stdout)
        self.assertTrue(d["migrated_cron"])
        self.assertEqual(d["schedule"], "weekly",
                         "explicitly requested cadence wins over the detected one")
        remaining = self.crontab_file.read_text()
        self.assertNotIn("mac-cleaner/cleaner.py", remaining)
        self.assertIn("db-cleaner.py", remaining, "unrelated cron lines survive")

    def test_migration_reports_detected_cadence_on_stderr(self):
        self.crontab_file.write_text(
            "0 9 1 * * /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.run_cli("schedule", "weekly", "--json")
        self.assertIn("monthly", r.stderr, "detected cadence is reported for visibility")

    def test_crontab_write_failure_does_not_claim_migration(self):
        """`crontab -` exiting non-zero must not be reported as a successful
        migration — the old code only checked for a raised exception, so a
        clean non-zero exit slipped through as `migrated_cron: true` with the
        cron line still present on the real crontab."""
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        crontab = self.bindir / "crontab"
        crontab.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
            'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > /dev/null; echo "write failed" >&2; exit 1; fi\n'
            'exit 0\n')
        crontab.chmod(0o755)
        r = self.run_cli("schedule", "weekly", "--json")
        d = json.loads(r.stdout)
        self.assertFalse(d["migrated_cron"], "a failed crontab write must not be reported as migrated")
        self.assertIn("Could not rewrite crontab", r.stderr)
        # The original line is untouched, since our stub crontab never wrote it.
        self.assertIn("mac-cleaner/cleaner.py", self.crontab_file.read_text())

    def test_off_crontab_write_failure_warns(self):
        self.run_cli("schedule", "weekly", "--json")
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        crontab = self.bindir / "crontab"
        crontab.write_text(
            '#!/bin/sh\n'
            'if [ "$1" = "-l" ]; then cat "$CRONTAB_FILE"; exit 0; fi\n'
            'if [ -z "$1" ] || [ "$1" = "-" ]; then cat > /dev/null; echo "write failed" >&2; exit 1; fi\n'
            'exit 0\n')
        crontab.chmod(0o755)
        r = self.run_cli("schedule", "off", "--json")
        self.assertEqual(r.returncode, 0, "off still succeeds at removing the agents")
        self.assertIn("Could not rewrite crontab", r.stderr)

    # ── off ────────────────────────────────────────────────────────────────

    def test_off_removes_everything(self):
        self.run_cli("schedule", "weekly", "--json")
        self.crontab_file.write_text(
            "0 9 * * 1 /usr/bin/python3 /Users/x/mac-cleaner/cleaner.py --clean --yes\n")
        r = self.run_cli("schedule", "off", "--json")
        self.assertEqual(r.returncode, 0)
        d = json.loads(r.stdout)
        self.assertTrue(d["removed"])
        self.assertEqual(list(self.agents.glob("*.plist")), [])
        self.assertNotIn("mac-cleaner", self.crontab_file.read_text())

    def test_off_when_nothing_installed_is_success(self):
        r = self.run_cli("schedule", "off", "--json")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(json.loads(r.stdout)["removed"])

    # ── failure propagation ────────────────────────────────────────────────

    def test_failed_load_exits_1_but_writes_plists(self):
        bad = self.bindir / "launchctl"
        bad.write_text('#!/bin/sh\necho "Load failed: 5: I/O error" >&2\nexit 1\n')
        r = self.run_cli("schedule", "weekly")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("✅ Scheduled", r.stdout)
        self.assertIn("I/O error", r.stderr, "real launchctl stderr surfaces")
        self.assertTrue(self.plist("clean").exists(),
                        "plist still written so manual loading works")

    def test_failed_load_prints_retry_hint(self):
        """On a failed install, each warning must be followed by a concrete
        retry suggestion (ported from the bash scheduler.sh, which told the
        user to fix the issue and run ./scheduler.sh <kind> again)."""
        bad = self.bindir / "launchctl"
        bad.write_text('#!/bin/sh\necho "Load failed: 5: I/O error" >&2\nexit 1\n')
        r = self.run_cli("schedule", "weekly")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fix the issue above, then run ./scheduler.sh weekly again", r.stderr)

    # ── doctor shares schedule state ─────────────────────────────────────

    def test_doctor_uses_sandboxed_schedule_state(self):
        self.run_cli("schedule", "weekly", "--json")
        r = self.run_cli("doctor", "--json")
        d = json.loads(r.stdout)
        sched = next(c for c in d["checks"] if c["name"] == "Schedule")
        self.assertIn("com.fullex.maccleaner.clean", sched["status"])

    def test_doctor_flags_agent_with_missing_interpreter(self):
        """A plist can stay 'loaded' per launchctl forever even after the
        interpreter it points at is deleted (e.g. brew-autoremove evicting a
        version-pinned python@X.Y). Nothing else would ever catch this, so
        doctor must check the ProgramArguments paths directly."""
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        clean_plist = self.plist("clean")
        with open(clean_plist, "rb") as f:
            p = plistlib.load(f)
        missing_interpreter = str(self.tmp / "gone" / "python3")
        p["ProgramArguments"][0] = missing_interpreter
        with open(clean_plist, "wb") as f:
            plistlib.dump(p, f)
        r = self.run_cli("doctor", "--json")
        d = json.loads(r.stdout)
        paths = next((c for c in d["checks"] if c["name"] == "Schedule paths"), None)
        self.assertIsNotNone(paths, "doctor must report a Schedule paths check")
        self.assertFalse(paths["ok"])
        self.assertIn(missing_interpreter, paths["status"])
        self.assertIn("com.fullex.maccleaner.clean", paths["status"])

    def test_doctor_flags_agent_with_missing_engine(self):
        """Same as the missing-interpreter case above, but for
        ProgramArguments[1] (the engine script) — a plist can also outlive
        the cleaner.py it points at, e.g. if the repo checkout it was
        installed from moved or was deleted."""
        self.run_cli("schedule", "weekly", "--json")
        import plistlib
        clean_plist = self.plist("clean")
        with open(clean_plist, "rb") as f:
            p = plistlib.load(f)
        missing_engine = str(self.tmp / "gone" / "cleaner.py")
        p["ProgramArguments"][1] = missing_engine
        with open(clean_plist, "wb") as f:
            plistlib.dump(p, f)
        r = self.run_cli("doctor", "--json")
        d = json.loads(r.stdout)
        paths = next((c for c in d["checks"] if c["name"] == "Schedule paths"), None)
        self.assertIsNotNone(paths, "doctor must report a Schedule paths check")
        self.assertFalse(paths["ok"])
        self.assertIn(missing_engine, paths["status"])
        self.assertIn("com.fullex.maccleaner.clean", paths["status"])
        self.assertIn("engine", paths["status"])

    def test_doctor_schedule_paths_absent_when_everything_exists(self):
        self.run_cli("schedule", "weekly", "--json")
        r = self.run_cli("doctor", "--json")
        d = json.loads(r.stdout)
        paths = next((c for c in d["checks"] if c["name"] == "Schedule paths"), None)
        self.assertIsNone(paths, "no Schedule paths check should be emitted when nothing's missing")


class TestAgentPython(unittest.TestCase):
    """_agent_python() picks the interpreter embedded in scheduled agents'
    plists — must prefer the stable `python3` on PATH over the (possibly
    version-pinned) running interpreter, and must never fall back to a
    virtualenv interpreter."""

    def test_prefers_stable_python3_on_path(self):
        with mock.patch("cleaner.shutil.which", return_value="/usr/bin/python3"):
            self.assertEqual(cleaner._agent_python(), "/usr/bin/python3")

    def test_falls_back_to_sys_executable_when_not_a_venv(self):
        with mock.patch("cleaner.shutil.which", return_value=None), \
             mock.patch.object(cleaner.sys, "prefix", "/usr"), \
             mock.patch.object(cleaner.sys, "base_prefix", "/usr"):
            self.assertEqual(cleaner._agent_python(), sys.executable)

    def test_refuses_venv_interpreter_when_no_stable_python3(self):
        with mock.patch("cleaner.shutil.which", return_value=None), \
             mock.patch.object(cleaner.sys, "prefix", "/Users/x/project/.venv"), \
             mock.patch.object(cleaner.sys, "base_prefix", "/usr"):
            with self.assertRaises(RuntimeError):
                cleaner._agent_python()

    def test_rejects_venv_shaped_interpreter_first_on_path(self):
        """Reproduces the real hazard, not just the unreachable fallback
        branch: activating a venv puts its bin/ first on PATH, so
        shutil.which('python3') resolves the venv interpreter *directly* —
        the old code only checked for a venv on the fallback branch, so this
        candidate sailed through unchecked and got baked into both plists.
        Build a venv-shaped fixture (bin/python3 + a sibling pyvenv.cfg)
        instead of a real venv, so this stays fast and hermetic."""
        tmp = Path(tempfile.mkdtemp())
        try:
            venv_python = tmp / "myproject" / ".venv" / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n")
            venv_python.chmod(0o755)
            (venv_python.parent.parent / "pyvenv.cfg").write_text("home = /usr/bin\n")

            base_python = tmp / "base" / "bin" / "python3"
            base_python.parent.mkdir(parents=True)
            base_python.write_text("#!/bin/sh\n")
            base_python.chmod(0o755)

            # Empty the well-known-locations list so this test exercises the
            # sys.base_prefix fallback specifically; the real ordering (stable
            # locations first) is covered by
            # test_prefers_stable_location_over_versioned_base_prefix.
            with mock.patch("cleaner.shutil.which", return_value=str(venv_python)), \
                 mock.patch.object(cleaner, "STABLE_PYTHON_CANDIDATES", ()), \
                 mock.patch.object(cleaner.sys, "base_prefix", str(tmp / "base")):
                result = cleaner._agent_python()

            self.assertEqual(result, str(base_python))
            self.assertNotIn(".venv", result)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_raises_when_path_python3_is_venv_and_no_base_fallback(self):
        """When PATH's python3 is a venv AND sys.base_prefix has no usable
        python3 either, refuse outright rather than silently falling through
        to something else."""
        tmp = Path(tempfile.mkdtemp())
        try:
            venv_python = tmp / ".venv" / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n")
            venv_python.chmod(0o755)
            (venv_python.parent.parent / "pyvenv.cfg").write_text("home = /usr/bin\n")

            with mock.patch("cleaner.shutil.which", return_value=str(venv_python)), \
                 mock.patch.object(cleaner, "STABLE_PYTHON_CANDIDATES", ()), \
                 mock.patch.object(cleaner.sys, "base_prefix", str(tmp / "nonexistent")):
                with self.assertRaises(RuntimeError):
                    cleaner._agent_python()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_prefers_stable_location_over_versioned_base_prefix(self):
        """A venv created from Homebrew python has a *version-pinned*
        sys.base_prefix (…/python@3.14/Frameworks/…/3.14/bin/python3), so
        falling straight back to it would trade the venv hazard for the
        brew-autoremove one this function exists to avoid. A stable
        unversioned location must win."""
        tmp = Path(tempfile.mkdtemp())
        try:
            venv_python = tmp / ".venv" / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n")
            (venv_python.parent.parent / "pyvenv.cfg").write_text("home = /usr/bin\n")

            stable = tmp / "stable" / "bin" / "python3"
            stable.parent.mkdir(parents=True)
            stable.write_text("#!/bin/sh\n")

            versioned_base = tmp / "python@3.14" / "bin" / "python3"
            versioned_base.parent.mkdir(parents=True)
            versioned_base.write_text("#!/bin/sh\n")

            with mock.patch("cleaner.shutil.which", return_value=str(venv_python)), \
                 mock.patch.object(cleaner, "STABLE_PYTHON_CANDIDATES", (str(stable),)), \
                 mock.patch.object(cleaner.sys, "base_prefix", str(tmp / "python@3.14")):
                result = cleaner._agent_python()

            self.assertEqual(result, str(stable))
            self.assertNotIn("python@", result)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_is_venv_interpreter_detects_pyvenv_cfg_sibling(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            venv_python = tmp / ".venv" / "bin" / "python3"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("#!/bin/sh\n")
            self.assertFalse(cleaner._is_venv_interpreter(str(venv_python)))
            (venv_python.parent.parent / "pyvenv.cfg").write_text("home = /usr/bin\n")
            self.assertTrue(cleaner._is_venv_interpreter(str(venv_python)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
