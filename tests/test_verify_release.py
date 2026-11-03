"""Unit tests for tools/verify_release.py (R12).

Everything here runs offline. The verifier's network and subprocess edges are
thin wrappers; the decisions worth testing — what counts as a failure, what
counts as "could not tell", and what does not apply — are pure functions over
already-fetched data, so they are tested directly with fixtures.
"""
import base64
import hashlib
import io
import sys
import tarfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import verify_release as vr  # noqa: E402

PASS, FAIL, UNVERIFIED, SKIPPED = vr.PASS, vr.FAIL, vr.UNVERIFIED, vr.SKIPPED

# RFC 8032 §7.1 test vectors 1 and 2. Cross-checked against an independent
# Ed25519 implementation (OpenSSL 4.1 `pkeyutl -verify -rawin`) before being
# written here, so a typo in a vector cannot make a broken verifier pass.
RFC_VECTORS = [
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]

V = "2.21.1"
SHA = "32710fbcc7e30d665132b1e1a63b021795e3e43156680dafdf9b281b03de1ba7"


def cask(version=V, sha=SHA):
    return (f'cask "maccleaner" do\n  version "{version}"\n  sha256 "{sha}"\n\n'
            '  url "https://github.com/Fullex26/MacCleaner/releases/download/'
            'v#{version}/MacCleaner-v#{version}-macos-universal.zip"\nend\n')


def appcast(version=V, length=3210006, sig="c2ln" * 22):
    sig_attr = f' sparkle:edSignature="{sig}"' if sig is not None else ""
    return ('<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0" '
            'xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
            f'<channel><item><sparkle:version>{version}</sparkle:version>'
            f'<sparkle:shortVersionString>{version}</sparkle:shortVersionString>'
            f'<enclosure url="https://example.invalid/MacCleaner-v{version}-macos-universal.zip"'
            f'{sig_attr} length="{length}" type="application/octet-stream"/>'
            '</item></channel></rss>')


def cli_tarball(version=V, engine_version=V, with_engine=True):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        root = f"MacCleaner-v{version}-cli"
        files = {f"{root}/install.sh": b"#!/bin/bash\n"}
        if with_engine:
            files[f"{root}/cleaner.py"] = f'VERSION = "{engine_version}"\n'.encode()
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestEd25519(unittest.TestCase):
    """The verifier must not depend on whatever `openssl` is first on PATH:
    stock macOS ships LibreSSL 3.3.6, which cannot verify Ed25519 at all."""

    def test_rfc8032_vectors_verify(self):
        for pk, msg, sig in RFC_VECTORS:
            with self.subTest(pk=pk[:8]):
                self.assertTrue(vr.ed25519_verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)))

    def test_flipped_message_bit_is_rejected(self):
        pk, _, sig = RFC_VECTORS[1]
        self.assertFalse(vr.ed25519_verify(bytes.fromhex(pk), b"\x73", bytes.fromhex(sig)))

    def test_flipped_signature_bit_is_rejected(self):
        pk, msg, sig = RFC_VECTORS[0]
        bad = bytearray(bytes.fromhex(sig))
        bad[0] ^= 1
        self.assertFalse(vr.ed25519_verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes(bad)))

    def test_wrong_key_is_rejected(self):
        _, msg, sig = RFC_VECTORS[0]
        other_pk = RFC_VECTORS[1][0]
        self.assertFalse(vr.ed25519_verify(bytes.fromhex(other_pk), bytes.fromhex(msg), bytes.fromhex(sig)))

    def test_s_out_of_range_is_rejected(self):
        # Malleability guard: S must be below the group order L.
        pk, msg, sig = RFC_VECTORS[0]
        raw = bytearray(bytes.fromhex(sig))
        raw[32:] = (vr.L + 1).to_bytes(32, "little")
        self.assertFalse(vr.ed25519_verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes(raw)))


class TestSignatureCheck(unittest.TestCase):
    """An unreadable signature is "could not tell", never "bad signature".

    During the 2.21.0 hand verification a shell extraction produced an empty
    signature file and `openssl` reported it in exactly the words it uses for a
    wrong signature. Reporting that as FAIL would condemn a good release."""

    def setUp(self):
        pk, msg, sig = RFC_VECTORS[1]
        self.key = base64.b64encode(bytes.fromhex(pk)).decode()
        self.msg = bytes.fromhex(msg)
        self.sig = base64.b64encode(bytes.fromhex(sig)).decode()

    def test_valid_signature_passes(self):
        self.assertEqual(vr.check_signature(self.key, self.sig, self.msg).status, PASS)

    def test_empty_signature_is_unverified_not_failed(self):
        r = vr.check_signature(self.key, "", self.msg)
        self.assertEqual(r.status, UNVERIFIED)
        self.assertIn("0 bytes", r.detail)

    def test_missing_signature_is_unverified(self):
        self.assertEqual(vr.check_signature(self.key, None, self.msg).status, UNVERIFIED)

    def test_undecodable_signature_is_unverified(self):
        self.assertEqual(vr.check_signature(self.key, "!!not base64!!", self.msg).status, UNVERIFIED)

    def test_missing_key_is_unverified(self):
        self.assertEqual(vr.check_signature(None, self.sig, self.msg).status, UNVERIFIED)

    def test_wrong_signature_fails(self):
        self.assertEqual(vr.check_signature(self.key, self.sig, self.msg + b"x").status, FAIL)


class TestAssets(unittest.TestCase):
    NAMES = [f"MacCleaner-v{V}-macos-universal.zip", f"MacCleaner-v{V}-cli.tar.gz", "appcast.xml"]

    def test_complete_asset_set_passes(self):
        self.assertEqual(vr.check_assets(V, self.NAMES).status, PASS)

    def test_missing_zip_fails_naming_it(self):
        r = vr.check_assets(V, self.NAMES[1:])
        self.assertEqual(r.status, FAIL)
        self.assertIn("macos-universal.zip", r.detail)

    def test_missing_appcast_fails(self):
        r = vr.check_assets(V, self.NAMES[:2])
        self.assertEqual(r.status, FAIL)
        self.assertIn("appcast.xml", r.detail)


class TestAppcast(unittest.TestCase):
    def test_matching_appcast_passes(self):
        r, sig = vr.check_appcast(appcast(), V, 3210006)
        self.assertEqual(r.status, PASS)
        self.assertEqual(sig, "c2ln" * 22)

    def test_missing_appcast_fails_clearly(self):
        r, sig = vr.check_appcast(None, V, 3210006)
        self.assertEqual(r.status, FAIL)
        self.assertIn("missing", r.detail)
        self.assertIsNone(sig)

    def test_length_mismatch_fails(self):
        # Sparkle treats this as a corrupt download and refuses the update.
        r, _ = vr.check_appcast(appcast(length=3210005), V, 3210006)
        self.assertEqual(r.status, FAIL)
        self.assertIn("3210005", r.detail)

    def test_version_mismatch_fails(self):
        r, _ = vr.check_appcast(appcast(version="2.21.0"), V, 3210006)
        self.assertEqual(r.status, FAIL)

    def test_missing_signature_attribute_fails(self):
        # Present appcast, absent signature: the release itself is defective.
        r, _ = vr.check_appcast(appcast(sig=None), V, 3210006)
        self.assertEqual(r.status, FAIL)

    def test_dtd_is_refused_before_parsing(self):
        evil = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
                + appcast()[appcast().index("<rss"):])
        r, sig = vr.check_appcast(evil, V, 3210006)
        self.assertEqual(r.status, FAIL)
        self.assertIn("DTD", r.detail)
        self.assertIsNone(sig)

    def test_unparseable_appcast_fails(self):
        r, _ = vr.check_appcast("<rss><channel>", V, 3210006)
        self.assertEqual(r.status, FAIL)


class TestCask(unittest.TestCase):
    def test_matching_cask_passes(self):
        self.assertEqual(vr.check_cask("tap", cask(), V, SHA, is_latest=True).status, PASS)

    def test_wrong_hash_fails_showing_both(self):
        wrong = "0" * 64
        r = vr.check_cask("tap", cask(sha=wrong), V, SHA, is_latest=True)
        self.assertEqual(r.status, FAIL)
        self.assertIn(wrong[:12], r.detail)
        self.assertIn(SHA[:12], r.detail)

    def test_stale_version_fails_on_latest_release(self):
        r = vr.check_cask("tap", cask(version="2.21.0"), V, SHA, is_latest=True)
        self.assertEqual(r.status, FAIL)
        self.assertIn("2.21.0", r.detail)

    def test_newer_cask_is_skipped_for_an_older_release(self):
        # Verifying 2.21.0 after 2.21.1 shipped: the casks are *meant* to have
        # moved on, so a mismatch there is not a defect in 2.21.0.
        r = vr.check_cask("tap", cask(), "2.21.0", "f" * 64, is_latest=False)
        self.assertEqual(r.status, SKIPPED)

    def test_unreadable_cask_is_unverified(self):
        self.assertEqual(vr.check_cask("tap", None, V, SHA, is_latest=True).status, UNVERIFIED)

    def test_cask_without_sha_line_is_unverified(self):
        r = vr.check_cask("tap", 'cask "maccleaner" do\nend\n', V, SHA, is_latest=True)
        self.assertEqual(r.status, UNVERIFIED)


class TestStableFeed(unittest.TestCase):
    def test_latest_release_served_passes(self):
        self.assertEqual(vr.check_stable_feed(appcast(), V, is_latest=True).status, PASS)

    def test_feed_behind_latest_fails(self):
        self.assertEqual(vr.check_stable_feed(appcast(version="2.21.0"), V, is_latest=True).status, FAIL)

    def test_older_release_is_skipped(self):
        self.assertEqual(vr.check_stable_feed(appcast(), "2.21.0", is_latest=False).status, SKIPPED)

    def test_unreachable_feed_is_unverified(self):
        self.assertEqual(vr.check_stable_feed(None, V, is_latest=True).status, UNVERIFIED)


class TestBundle(unittest.TestCase):
    def test_matching_bundle_passes(self):
        r = vr.check_bundle({"CFBundleShortVersionString": V}, f'VERSION = "{V}"\n', V, has_sparkle=True)
        self.assertEqual(r.status, PASS)

    def test_stale_bundled_engine_fails(self):
        r = vr.check_bundle({"CFBundleShortVersionString": V}, 'VERSION = "2.21.0"\n', V, has_sparkle=True)
        self.assertEqual(r.status, FAIL)
        self.assertIn("engine", r.detail)

    def test_app_version_mismatch_fails(self):
        r = vr.check_bundle({"CFBundleShortVersionString": "2.21.0"}, f'VERSION = "{V}"\n', V, has_sparkle=True)
        self.assertEqual(r.status, FAIL)

    def test_missing_sparkle_fails(self):
        r = vr.check_bundle({"CFBundleShortVersionString": V}, f'VERSION = "{V}"\n', V, has_sparkle=False)
        self.assertEqual(r.status, FAIL)
        self.assertIn("Sparkle", r.detail)


class TestCliTarball(unittest.TestCase):
    def test_matching_engine_passes(self):
        self.assertEqual(vr.check_cli_tarball(cli_tarball(), V).status, PASS)

    def test_stale_engine_fails(self):
        r = vr.check_cli_tarball(cli_tarball(engine_version="2.21.0"), V)
        self.assertEqual(r.status, FAIL)
        self.assertIn("2.21.0", r.detail)

    def test_missing_engine_fails(self):
        self.assertEqual(vr.check_cli_tarball(cli_tarball(with_engine=False), V).status, FAIL)

    def test_corrupt_tarball_fails(self):
        self.assertEqual(vr.check_cli_tarball(b"not a tarball", V).status, FAIL)


class TestKeyContinuity(unittest.TestCase):
    """Whether *installed* copies can take the update. release.yml already
    gates the key against the source tree; nothing else compared it with what
    the previous release actually shipped."""

    def test_same_key_passes(self):
        self.assertEqual(vr.check_key_continuity("a2V5", "a2V5", "2.21.0").status, PASS)

    def test_rotated_key_fails(self):
        r = vr.check_key_continuity("bmV3", "b2xk", "2.21.0")
        self.assertEqual(r.status, FAIL)
        self.assertIn("2.21.0", r.detail)

    def test_no_previous_release_is_skipped(self):
        self.assertEqual(vr.check_key_continuity("a2V5", None, None).status, SKIPPED)

    def test_unreadable_previous_key_is_unverified(self):
        self.assertEqual(vr.check_key_continuity("a2V5", None, "2.21.0").status, UNVERIFIED)

    def test_previous_release_without_an_updater_is_skipped(self):
        # 2.6.0 is the first Sparkle build: 2.5.0's bundle reads fine and simply
        # has no key. That is "nothing to continue", not "could not tell" —
        # reporting it as unverified would leave 2.6.0 inconclusive forever.
        r = vr.check_key_continuity("a2V5", None, "2.5.0", previous_readable=True)
        self.assertEqual(r.status, SKIPPED)
        self.assertIn("2.5.0", r.detail)


class TestGatekeeperParsing(unittest.TestCase):
    def test_notarized_passes(self):
        out = "x.app: accepted\nsource=Notarized Developer ID\norigin=Developer ID Application: X (T)\n"
        self.assertEqual(vr.parse_spctl(0, out).status, PASS)

    def test_signed_but_not_notarized_fails(self):
        # Gatekeeper still blocks first launch of a Developer-ID-only app.
        out = "x.app: accepted\nsource=Developer ID\n"
        self.assertEqual(vr.parse_spctl(0, out).status, FAIL)

    def test_rejected_fails(self):
        self.assertEqual(vr.parse_spctl(3, "x.app: rejected\n").status, FAIL)


class TestReleaseLookup(unittest.TestCase):
    """Only a 404 is evidence a release does not exist.

    Found running the verifier for real: after a few runs GitHub's
    unauthenticated limit (60/hour) answered 403, and the first version
    reported "v2.21.1 not found" and DEFECTIVE for a release that was fine."""

    def test_404_means_the_release_is_missing(self):
        self.assertEqual(vr.check_release_lookup("v9.9.9", 404, "HTTP 404").status, FAIL)

    def test_rate_limit_is_unverified(self):
        r = vr.check_release_lookup("v2.21.1", 403, "GitHub API rate limit reached")
        self.assertEqual(r.status, UNVERIFIED)
        self.assertIn("rate limit", r.detail)

    def test_server_error_is_unverified(self):
        self.assertEqual(vr.check_release_lookup("v2.21.1", 502, "HTTP 502").status, UNVERIFIED)

    def test_network_failure_is_unverified(self):
        self.assertEqual(vr.check_release_lookup("v2.21.1", None, "timed out").status, UNVERIFIED)

    def test_rate_limit_message_names_the_remedy(self):
        msg = vr.describe_http_error(403, {"X-RateLimit-Remaining": "0"}, "https://api.github.com/x")
        self.assertIn("rate limit", msg)
        self.assertIn("GH_TOKEN", msg)

    def test_ordinary_403_is_not_called_a_rate_limit(self):
        msg = vr.describe_http_error(403, {"X-RateLimit-Remaining": "41"}, "https://api.github.com/x")
        self.assertNotIn("rate limit", msg)


class TestReadOnly(unittest.TestCase):

    def test_borrowing_the_gh_token_is_allowed(self):
        # Read-only, and raises the API limit from 60/hour to 5,000/hour.
        self.assertTrue(vr.is_readonly(["gh", "auth", "token"]))

    def test_other_gh_auth_commands_are_refused(self):
        for argv in (["gh", "auth", "logout"], ["gh", "auth", "token", "--with-token"],
                     ["gh", "auth", "refresh"]):
            with self.subTest(argv=argv):
                self.assertFalse(vr.is_readonly(argv))

    """The verifier must never publish, edit or delete anything."""

    def test_write_commands_are_refused(self):
        for argv in (["gh", "release", "delete", "v1"], ["git", "push"],
                     ["gh", "release", "upload", "v1", "x"], ["rm", "-rf", "/tmp/x"],
                     ["codesign", "--sign", "-", "x.app"], ["xcrun", "stapler", "staple", "x.app"],
                     ["spctl", "--add", "x.app"], ["xattr", "-cr", "x.app"]):
            with self.subTest(argv=argv):
                with self.assertRaises(vr.NotReadOnly):
                    vr.run_readonly(argv)

    def test_network_helper_only_issues_get(self):
        req = vr.build_request("https://api.github.com/repos/o/r/releases/latest")
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data)

    def test_token_is_never_sent_off_github_api(self):
        req = vr.build_request("https://example.invalid/x", token="secret")
        self.assertNotIn("Authorization", req.headers)
        self.assertNotIn("Authorization", {k.title(): v for k, v in req.header_items()})


class TestExitCode(unittest.TestCase):
    def r(self, status):
        return vr.Result("x", status, "")

    def test_all_pass_or_skipped_exits_zero(self):
        self.assertEqual(vr.exit_code([self.r(PASS), self.r(SKIPPED)]), 0)

    def test_any_failure_exits_one(self):
        self.assertEqual(vr.exit_code([self.r(PASS), self.r(UNVERIFIED), self.r(FAIL)]), 1)

    def test_inconclusive_without_failure_exits_three(self):
        # Distinct from 1 so automation can tell "the release is broken" from
        # "this machine could not check it".
        self.assertEqual(vr.exit_code([self.r(PASS), self.r(UNVERIFIED)]), 3)


class TestSha(unittest.TestCase):
    def test_sha256_hex(self):
        self.assertEqual(vr.sha256_hex(b"abc"), hashlib.sha256(b"abc").hexdigest())


if __name__ == "__main__":
    unittest.main()
