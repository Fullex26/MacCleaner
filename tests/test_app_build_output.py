import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class TestAppBuildOutput(unittest.TestCase):
    def test_embeds_swift_reported_universal_output(self):
        self.check_output('universal')

    def test_embeds_swift_reported_native_fallback_output(self):
        self.check_output('native')

    def test_failed_swift_build_remains_optional(self):
        self.check_output("unavailable")

    def test_failed_output_query_remains_optional(self):
        self.check_output("query-failure")

    def test_failed_build_does_not_embed_stale_output(self):
        self.check_output("stale-output")

    def check_output(self, mode):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'app/Sources').mkdir(parents=True)
            (root / 'swift/MacCleanerKit').mkdir(parents=True)
            (root / 'app/Info.plist').write_text('fixture')
            (root / 'cleaner.py').write_text('fixture')
            shutil.copy2(REPO / 'app/build.sh', root / 'app/build.sh')
            cache = root / 'build/sparkle-2.9.5'
            (cache / 'Sparkle.framework').mkdir(parents=True)
            (cache / '.verified-sha256').write_text(
                '015336b601493e05c237964954bff6191370003d94edefe663724c88840d73cc\n')
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            def stub(name, code):
                path = bin_dir / name
                path.write_text(f'#!{sys.executable}\n' + code)
                path.chmod(0o755)
            stub('swift', '''import os, sys
from pathlib import Path
args = sys.argv[1:]
if os.environ['FIXTURE_MODE'] == 'unavailable' or (os.environ['FIXTURE_MODE'] == 'query-failure' and '--show-bin-path' in args):
    sys.exit(1)
if os.environ['FIXTURE_MODE'] == 'native' and '--arch' in args:
    sys.exit(1)
package = Path(args[args.index('--package-path') + 1])
output = package / 'custom-output' / ('universal' if '--arch' in args else 'native')
if '--show-bin-path' in args:
    print(output)
else:
    output.mkdir(parents=True, exist_ok=True)
    binary = output / 'mck'
    binary.write_text('fixture Swift engine')
    binary.chmod(0o755)
    if os.environ['FIXTURE_MODE'] == 'stale-output':
        sys.exit(1)
''')
            stub('swiftc', '''import sys
from pathlib import Path
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('fixture app')
''')
            stub('lipo', '''import sys
from pathlib import Path
if '-output' in sys.argv:
    Path(sys.argv[sys.argv.index('-output') + 1]).write_text('fixture universal app')
''')
            stub('codesign', '')
            stub('xattr', '')
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
                       FIXTURE_MODE=mode)
            result = subprocess.run(['bash', str(root / 'app/build.sh')], env=env,
                                    text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            embedded = root / 'build/MacCleaner.app/Contents/Resources/mck'
            if mode in ('unavailable', 'query-failure', 'stale-output'):
                self.assertFalse(embedded.exists())
                self.assertIn('app ships without the V3 soak engine', result.stderr)
                self.assertTrue((root / 'build/MacCleaner.app/Contents/MacOS/MacCleaner').is_file())
                return
            self.assertTrue(embedded.is_file(), result.stdout + result.stderr)
            self.assertEqual(embedded.read_text(), 'fixture Swift engine')


class TestAdhocSigning(unittest.TestCase):
    """`adhoc_sign_bundle` from app/build.sh, sliced out and run in a
    throwaway bash against a stub `codesign` on PATH. No real bundle, no
    real signing, no real install is ever touched.

    The regression these pin: build.sh used to end with `codesign ... ||
    echo "codesign unavailable"`, so a codesign that RAN AND REFUSED — what
    iCloud's File Provider daemon causes in a synced checkout by re-stamping
    the bundle root between `xattr -cr` and `codesign` — left an unsigned
    bundle behind while the script still exited 0. Caught by hand while
    cutting 2.20.0; nothing in the suite would have caught it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.bundle = self.tmp / "build" / "MacCleaner.app"
        (self.bundle / "Contents" / "MacOS").mkdir(parents=True)
        (self.bundle / "Contents" / "MacOS" / "MacCleaner").write_text("fixture app")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _stub(self, name, code):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o755)

    def _sign(self, env=None, path=None):
        script = (
            f'eval "$(sed -n \'/^adhoc_sign_bundle()/,/^}}/p\' "{REPO}/app/build.sh")"\n'
            'adhoc_sign_bundle "$1"\n')
        full = dict(os.environ)
        full.pop("MACCLEANER_ALLOW_UNSIGNED", None)
        full.pop("CI", None)
        full["PATH"] = path if path is not None else (
            str(self.bin) + os.pathsep + os.environ["PATH"])
        full.update(env or {})
        # /bin/bash by absolute path: the absent-codesign case hands this a
        # PATH that deliberately holds almost nothing.
        return subprocess.run(["/bin/bash", "-c", script, "_", str(self.bundle)],
                              capture_output=True, text=True, env=full, timeout=60)

    # --- a codesign that refuses must never be laundered into success ---

    def test_refused_codesign_fails_the_build(self):
        self._stub("codesign", "import sys; sys.exit(1)")
        r = self._sign()
        self.assertNotEqual(r.returncode, 0,
                            "a refused codesign must not exit 0:\n" + r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)

    def test_refused_verify_also_fails_the_build(self):
        # Signing "succeeds" but the signature does not verify — just as bad
        # as a refusal, and the old code checked neither.
        self._stub("codesign", "import sys; sys.exit(1 if '--verify' in sys.argv else 0)")
        r = self._sign()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("REFUSED", r.stderr)

    def test_a_refusal_leaves_the_bundle_in_place_to_inspect(self):
        self._stub("codesign", "import sys; sys.exit(1)")
        self._sign()
        self.assertTrue((self.bundle / "Contents" / "MacOS" / "MacCleaner").is_file())
        self.assertEqual(sorted(p.name for p in self.bundle.parent.iterdir()),
                         ["MacCleaner.app"], "no .unsigned backup left behind")

    # --- the genuine fallback stays distinguishable, and non-fatal -------

    def test_absent_codesign_is_not_fatal_and_says_so_differently(self):
        # A PATH with the handful of tools the function needs, but no
        # codesign: the case the original soft notice was written for.
        sandbox = self.tmp / "sandbox-bin"
        sandbox.mkdir()
        for tool in ("sed", "mktemp", "cp", "rm", "mv"):
            (sandbox / tool).symlink_to(shutil.which(tool))
        r = self._sign(path=str(sandbox))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT INSTALLED", r.stderr)
        self.assertNotIn("REFUSED", r.stderr)

    def test_the_opt_out_allows_an_unsigned_local_build(self):
        self._stub("codesign", "import sys; sys.exit(1)")
        r = self._sign(env={"MACCLEANER_ALLOW_UNSIGNED": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("MACCLEANER_ALLOW_UNSIGNED", r.stderr)

    def test_the_opt_out_is_ignored_under_ci(self):
        # Same reasoning as the Sparkle fetch: a release must never silently
        # ship unsigned, whatever the environment claims to allow.
        self._stub("codesign", "import sys; sys.exit(1)")
        r = self._sign(env={"MACCLEANER_ALLOW_UNSIGNED": "1", "CI": "true"})
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)

    # --- the happy path, and what it does NOT do ------------------------

    def test_signing_happens_outside_the_source_tree(self):
        # The whole point of the fix: the directory codesign is pointed at
        # must not be inside the (iCloud-synced) checkout.
        self._stub("codesign", f"""import os, sys
if '--verify' not in sys.argv:
    open({str(self.tmp / 'signed-path')!r}, 'a').write(sys.argv[-1] + chr(10))
""")
        r = self._sign()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        signed = (self.tmp / "signed-path").read_text().strip()
        self.assertFalse(signed.startswith(str(self.tmp / "build")),
                         f"signed in the source tree: {signed}")
        self.assertTrue(signed.endswith("MacCleaner.app"), signed)

    def test_an_in_tree_strict_verify_failure_does_not_fail_the_build(self):
        """The iCloud behaviour, simulated. Once the signed bundle is back in
        the synced checkout, the File Provider daemon re-stamps
        com.apple.FinderInfo on every bundle directory inside it, and
        `codesign --verify --strict` rejects precisely that — measured to
        pass for a few seconds after an `xattr -cr` and fail again within
        ten. So `--deep --strict` runs only on the out-of-tree copy, where it
        is deterministic; the in-place gate is a plain `--verify`, which
        xattrs do not affect. Making the in-place check strict would move the
        race rather than remove it, and this test is what says so."""
        self._stub("codesign", f"""import sys
args = sys.argv[1:]
target = args[-1]
strict = '--strict' in args
# Stand in for the daemon: anything inside the checkout carries detritus.
if strict and target.startswith({str(self.tmp / 'build')!r}):
    sys.stderr.write('detritus not allowed' + chr(10))
    sys.exit(1)
""")
        r = self._sign()
        self.assertEqual(r.returncode, 0,
                         "a strict-verify failure in the synced tree must not "
                         "fail the build:\n" + r.stdout + r.stderr)

    def test_a_signed_bundle_comes_back_intact(self):
        self._stub("codesign", "import sys; sys.exit(0)")
        r = self._sign()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(
            (self.bundle / "Contents" / "MacOS" / "MacCleaner").read_text(),
            "fixture app")
        self.assertEqual(sorted(p.name for p in self.bundle.parent.iterdir()),
                         ["MacCleaner.app"], "no scratch or backup left behind")


class TestShellScriptUnicodeQuoting(unittest.TestCase):
    """A bare `$VAR` immediately followed by a non-ASCII character (in
    practice the `…` these scripts use in progress messages) can have the
    variable name swallow that character's leading byte under some locales:
    the variable expands to nothing and the stray bytes land in the output
    as invalid UTF-8. It has happened twice — once in CI during a release,
    reproduced only under GitHub Actions' locale, and once while writing the
    out-of-tree signing stage, caught only because a sibling test module
    shifted the locale under `unittest discover`. Brace the expansion."""

    SCRIPTS = ("app/build.sh", "install.sh", "scheduler.sh")
    BARE_BEFORE_NON_ASCII = re.compile(
        r'\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]')

    def test_no_bare_expansion_touches_a_non_ascii_character(self):
        offenders = []
        for name in self.SCRIPTS:
            path = REPO / name
            if not path.exists():
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                for match in self.BARE_BEFORE_NON_ASCII.finditer(line):
                    offenders.append(f"{name}:{lineno}: {match.group(0)!r} "
                                     f"— brace it as ${{...}}")
        self.assertEqual(offenders, [], "\n".join(offenders))
