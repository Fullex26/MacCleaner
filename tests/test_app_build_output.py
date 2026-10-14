import os
from pathlib import Path
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
            self.assertTrue(embedded.is_file(), result.stdout + result.stderr)
            self.assertEqual(embedded.read_text(), 'fixture Swift engine')
