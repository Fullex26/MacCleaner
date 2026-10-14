import contextlib
import io
import unittest

import cleaner


class TestStandaloneLegacyCategory(unittest.TestCase):
    def parse(self, argv):
        return cleaner.build_parser().parse_args(cleaner.translate_legacy(argv))

    def test_standalone_category_selects_scan(self):
        for argv, expected in [
            (["--category", "node"], ["node"]),
            (["--category=node"], ["node"]),
            (["--category", "node", "--category", "xcode"], ["node", "xcode"]),
            (["--category", "node,xcode", "--json"], ["node,xcode"]),
        ]:
            with self.subTest(argv=argv):
                args = self.parse(argv)
                self.assertEqual(args.command, "scan")
                self.assertEqual(args.category, expected)

    def test_explicit_cleanup_mode_stays_cleanup(self):
        args = self.parse(["--category", "node", "--clean", "--yes"])
        self.assertEqual(args.command, "clean")
        self.assertTrue(args.yes)

    def test_missing_category_value_stays_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.parse(["--category"])
        self.assertEqual(error.exception.code, 2)
