#!/usr/bin/env python3
"""Verify a published MacCleaner release, read-only (R12).

    python3 tools/verify_release.py            # the latest release
    python3 tools/verify_release.py 2.21.1     # a specific one ("v2.21.1" also works)

Every check reports one of four outcomes:

  PASS        checked, and correct
  FAIL        checked, and wrong — the release (or a channel) is defective
  UNVERIFIED  could not be checked from here — not evidence either way
  SKIPPED     does not apply (e.g. channel checks on a release that is no
              longer the latest: its casks are *meant* to have moved on)

Exit status: 0 nothing failed or went unverified; 1 at least one FAIL;
2 usage error; 3 no FAIL but something UNVERIFIED. 1 and 3 are separate so
automation can tell "the release is broken" from "this machine could not
tell", which is the distinction a hand check most easily gets wrong: during
the 2.21.0 verification an empty signature file was reported by `openssl` in
exactly the words it uses for a wrong signature.

Read-only by construction. Network access is GET-only through
`build_request`, and every subprocess goes through `run_readonly`, which
refuses anything outside a short allowlist of inspection commands. Downloads
land in a temporary directory removed on every exit path.

Stdlib only, including Ed25519: stock macOS ships LibreSSL 3.3.6, which
cannot verify Ed25519, so a check that shelled out to `openssl` would pass
only on machines that happen to have Homebrew's OpenSSL first on PATH.
"""
import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import namedtuple
from pathlib import Path

PASS, FAIL, UNVERIFIED, SKIPPED = "PASS", "FAIL", "UNVERIFIED", "SKIPPED"
Result = namedtuple("Result", "name status detail")

SPARKLE_NS = "http://www.andymatuschak.org/xml-namespaces/sparkle"
APP_PLIST = "MacCleaner.app/Contents/Info.plist"
APP_ENGINE = "MacCleaner.app/Contents/Resources/cleaner.py"
APP_SPARKLE = "MacCleaner.app/Contents/Frameworks/Sparkle.framework/"


def asset_names(version):
    return (f"MacCleaner-v{version}-macos-universal.zip",
            f"MacCleaner-v{version}-cli.tar.gz",
            "appcast.xml")


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def engine_version(text):
    m = re.search(r'^VERSION = "([^"]+)"', text or "", re.M)
    return m.group(1) if m else None


# ── Ed25519 (RFC 8032 §5.1.7, reference arithmetic) ──────────────────────────
# The same curve maths release.yml uses to derive the public key from the
# signing seed, extended to verification. Slow by crypto standards, fast
# enough here: one verification of a ~3 MB ZIP takes well under a second.

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * _D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _same_point(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % P == 0 and (p[1] * q[2] - q[1] * p[2]) % P == 0


def _decompress(raw):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % P)


def ed25519_verify(public, message, signature):
    """True only for a valid signature. Malformed input is simply invalid."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % L
    return _same_point(_mul(s, _G), _add(r, _mul(h, a)))


# ── Pure checks over already-fetched data ────────────────────────────────────

def check_assets(version, names):
    missing = [n for n in asset_names(version) if n not in set(names)]
    if missing:
        return Result("Release assets", FAIL, "missing: " + ", ".join(missing))
    return Result("Release assets", PASS, "zip, CLI tarball and appcast.xml all present")


def check_appcast(xml_text, version, zip_size):
    """Returns (Result, edSignature or None)."""
    name = "Appcast"
    if xml_text is None:
        return Result(name, FAIL, "appcast.xml is missing — installed copies will never be offered "
                                  "this release"), None
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        # A real appcast never declares a DTD; refusing one keeps entity
        # expansion out of the parser entirely (stdlib only, so no defusedxml).
        return Result(name, FAIL, "appcast.xml declares a DTD, which a Sparkle appcast never needs"), None
    try:
        item = ET.fromstring(xml_text).find("channel/item")
    except ET.ParseError as e:
        return Result(name, FAIL, f"appcast.xml does not parse: {e}"), None
    if item is None:
        return Result(name, FAIL, "appcast.xml has no <item>"), None
    advertised = (item.findtext(f"{{{SPARKLE_NS}}}version") or "").strip()
    enclosure = item.find("enclosure")
    sig = enclosure.get(f"{{{SPARKLE_NS}}}edSignature") if enclosure is not None else None
    problems = []
    if advertised != version:
        problems.append(f"advertises {advertised or 'no version'}, not {version}")
    if enclosure is None:
        problems.append("no <enclosure>")
    else:
        if sig is None:
            problems.append("enclosure has no sparkle:edSignature")
        length = enclosure.get("length")
        if length != str(zip_size):
            # Sparkle treats a length mismatch as a corrupt download.
            problems.append(f"enclosure length {length} != published ZIP size {zip_size}")
    if problems:
        return Result(name, FAIL, "; ".join(problems)), sig
    return Result(name, PASS, f"advertises {version}; length {zip_size} matches the ZIP"), sig


def check_signature(key_b64, sig_b64, data):
    name = "Ed25519 signature"
    if not key_b64:
        return Result(name, UNVERIFIED, "no SUPublicEDKey in the shipped bundle to check against")
    if sig_b64 is None:
        return Result(name, UNVERIFIED, "no signature available to check")
    try:
        key = base64.b64decode(key_b64, validate=True)
        sig = base64.b64decode(sig_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        return Result(name, UNVERIFIED, f"could not decode key or signature ({e})")
    if len(sig) != 64 or len(key) != 32:
        # Unreadable is not the same as wrong; see the module docstring.
        return Result(name, UNVERIFIED, f"signature is {len(sig)} bytes and key {len(key)} bytes "
                                        "(expected 64 and 32) — could not be read, which is not "
                                        "evidence the release is bad")
    if not ed25519_verify(key, data, sig):
        return Result(name, FAIL, "does not verify against the published ZIP under the shipped key — "
                                  "Sparkle will refuse this update")
    return Result(name, PASS, "verifies against the published ZIP under the shipped SUPublicEDKey")


def check_key_continuity(new_key, prev_key, prev_version, previous_readable=False):
    """`previous_readable` separates "its bundle read fine and has no key" (a
    pre-Sparkle release: nothing to continue) from "could not read it"."""
    name = "Key continuity"
    if prev_version is None:
        return Result(name, SKIPPED, "no earlier release to compare against")
    if prev_key is None and previous_readable:
        return Result(name, SKIPPED, f"v{prev_version} shipped no updater key, so there is nothing to continue")
    if prev_key is None:
        return Result(name, UNVERIFIED, f"could not read v{prev_version}'s SUPublicEDKey")
    if new_key != prev_key:
        return Result(name, FAIL, f"SUPublicEDKey differs from v{prev_version}'s — copies installed "
                                  f"from v{prev_version} will reject this update")
    return Result(name, PASS, f"same key as v{prev_version}: installed copies can verify this update")


def check_bundle(info, engine_text, version, has_sparkle):
    name = "App bundle contents"
    problems = []
    app_version = (info or {}).get("CFBundleShortVersionString")
    if app_version != version:
        problems.append(f"CFBundleShortVersionString {app_version}, not {version}")
    bundled = engine_version(engine_text)
    if engine_text is None:
        problems.append("bundled engine cleaner.py missing")
    elif bundled != version:
        problems.append(f"bundled engine is {bundled}, not {version}")
    if not has_sparkle:
        problems.append("Sparkle.framework not embedded — this build cannot self-update")
    if problems:
        return Result(name, FAIL, "; ".join(problems))
    return Result(name, PASS, f"app and bundled engine both {version}; Sparkle embedded")


def check_cli_tarball(data, version):
    name = "CLI tarball"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            engines = sorted((m for m in tf.getmembers()
                              if m.isfile() and m.name.rsplit("/", 1)[-1] == "cleaner.py"),
                             key=lambda m: m.name.count("/"))
            if not engines:
                return Result(name, FAIL, "no cleaner.py in the CLI tarball")
            # Read in memory; nothing is extracted, so member paths never touch disk.
            text = tf.extractfile(engines[0]).read().decode("utf-8", "replace")
    except (tarfile.TarError, OSError, EOFError) as e:
        return Result(name, FAIL, f"not a readable tarball: {e}")
    shipped = engine_version(text)
    if shipped != version:
        return Result(name, FAIL, f"ships engine {shipped}, release is {version}")
    return Result(name, PASS, f"ships engine {version}")


def check_cask(label, text, version, zip_sha, is_latest):
    name = f"{label} cask"
    if not is_latest:
        return Result(name, SKIPPED, f"casks track the latest release, not v{version}")
    if text is None:
        return Result(name, UNVERIFIED, "could not fetch the cask")
    v = re.search(r'^\s*version "([^"]+)"', text, re.M)
    s = re.search(r'^\s*sha256 "([0-9a-f]{64})"', text, re.M)
    if not v or not s:
        return Result(name, UNVERIFIED, "could not find its version and sha256 lines")
    problems = []
    if v.group(1) != version:
        problems.append(f"says {v.group(1)}, release is {version}")
    if s.group(1) != zip_sha:
        problems.append(f"sha256 {s.group(1)[:12]}… != published ZIP {zip_sha[:12]}…")
    if problems:
        return Result(name, FAIL, "; ".join(problems))
    return Result(name, PASS, f"{version}, sha256 matches the published ZIP")


def check_stable_feed(xml_text, version, is_latest):
    name = "Stable appcast feed"
    if not is_latest:
        return Result(name, SKIPPED, f"the feed serves the latest release, not v{version}")
    if xml_text is None:
        return Result(name, UNVERIFIED, "could not fetch releases/latest/download/appcast.xml")
    m = re.search(r"<sparkle:version>([^<]+)</sparkle:version>", xml_text)
    served = m.group(1).strip() if m else None
    if served != version:
        return Result(name, FAIL, f"serves {served}; installed copies will not be offered {version}")
    return Result(name, PASS, f"releases/latest/download/appcast.xml serves {version}")


def parse_codesign(rc, out):
    name = "Code signature"
    if rc is None:
        return Result(name, UNVERIFIED, out)
    if rc != 0:
        return Result(name, FAIL, "codesign --verify --deep --strict: " + out.strip().splitlines()[-1])
    return Result(name, PASS, "valid (--deep --strict), satisfies its Designated Requirement")


def parse_stapler(rc, out):
    name = "Notarization ticket"
    if rc is None:
        return Result(name, UNVERIFIED, out)
    if rc != 0 or "worked" not in out:
        return Result(name, FAIL, "stapler validate: " + (out.strip().splitlines() or ["no output"])[-1])
    return Result(name, PASS, "stapled ticket validates")


def parse_spctl(rc, out):
    name = "Gatekeeper"
    if rc is None:
        return Result(name, UNVERIFIED, out)
    source = re.search(r"^source=(.+)$", out, re.M)
    if rc != 0 or "rejected" in out:
        return Result(name, FAIL, "rejected" + (f" ({source.group(1)})" if source else ""))
    if not source or source.group(1).strip() != "Notarized Developer ID":
        # Signed but not notarized is still blocked on first launch.
        return Result(name, FAIL, f"accepted but source={source.group(1).strip() if source else '?'} "
                                  "— signed, not notarized")
    return Result(name, PASS, "accepted, source=Notarized Developer ID")


def exit_code(results):
    statuses = {r.status for r in results}
    if FAIL in statuses:
        return 1
    if UNVERIFIED in statuses:
        return 3
    return 0


# ── The read-only edges ──────────────────────────────────────────────────────

class NotReadOnly(Exception):
    pass


# Argument prefixes this tool may run. `ditto -x -k` writes, but only into the
# verifier's own temporary directory; everything else only inspects.
_ALLOWED = (("codesign", "--verify"), ("codesign", "-dv"), ("xcrun", "stapler", "validate"),
            ("spctl", "-a"), ("ditto", "-x", "-k"))
_FORBIDDEN_ARGS = {"--sign", "-s", "--remove-signature", "staple", "--add", "--remove",
                   "--master-disable", "--enable", "--disable"}


# Matched exactly, not as a prefix: borrowing the signed-in gh CLI's token
# lifts the API limit from 60/hour to 5,000/hour, and no flag is needed.
_ALLOWED_EXACT = (("gh", "auth", "token"),)


def is_readonly(argv):
    if tuple(argv) in _ALLOWED_EXACT:
        return True
    return any(tuple(argv[:len(p)]) == p for p in _ALLOWED) and not _FORBIDDEN_ARGS & set(argv)


def run_readonly(argv, timeout=180):
    """Run an allowlisted inspection command. Returns (returncode, output),
    or (None, reason) when the tool is not installed here."""
    if not is_readonly(argv):
        raise NotReadOnly(f"refusing to run non-read-only command: {argv!r}")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, f"`{argv[0]}` is not available on this machine"
    except subprocess.TimeoutExpired:
        return None, f"`{' '.join(argv[:3])}` timed out after {timeout}s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def build_request(url, token=None):
    headers = {"User-Agent": "maccleaner-verify-release", "Accept": "application/vnd.github+json"}
    # A token only ever goes to the GitHub API, never to a redirect target or
    # a raw-content host.
    if token and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers, method="GET")


def describe_http_error(code, headers, url):
    if code in (403, 429) and str((headers or {}).get("X-RateLimit-Remaining")) == "0":
        return ("GitHub API rate limit reached (60 requests/hour without a token) — set GH_TOKEN, "
                "or sign in with `gh auth login` and this tool will use that token")
    return f"HTTP {code} for {url}"


def fetch(url, token=None, timeout=60):
    """(bytes, None, status) on success; (None, reason, status-or-None) otherwise.

    The status travels with the failure because only a 404 is evidence that
    something is absent; a 403 rate limit or a dropped connection is not."""
    try:
        with urllib.request.urlopen(build_request(url, token), timeout=timeout) as r:
            return r.read(), None, r.status
    except urllib.error.HTTPError as e:
        return None, describe_http_error(e.code, e.headers, url), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"{url}: {e}", None


def fetch_json(path, repo, token):
    data, err, status = fetch(f"https://api.github.com/repos/{repo}{path}", token)
    return (json.loads(data), None, status) if data is not None else (None, err, status)


def github_token():
    """GH_TOKEN / GITHUB_TOKEN, else the signed-in gh CLI's token, else None."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    rc, out = run_readonly(["gh", "auth", "token"], timeout=15)
    out = (out or "").strip()
    return out if rc == 0 and out and not any(c.isspace() for c in out) else None


def check_release_lookup(tag, status, err):
    if status == 404:
        return Result("Release", FAIL, f"{tag} not found ({err})")
    return Result("Release", UNVERIFIED, f"could not look up {tag}: {err}")


def _text(data):
    return data.decode("utf-8", "replace") if data is not None else None


def _read_zip_member(zf, member):
    try:
        return zf.read(member)
    except KeyError:
        return None


def previous_release(releases, current):
    """The newest published, non-prerelease release older than `current`."""
    older = [r for r in releases or []
             if not r.get("draft") and not r.get("prerelease")
             and r.get("tag_name") != current.get("tag_name")
             and (r.get("published_at") or "") < (current.get("published_at") or "")]
    return max(older, key=lambda r: r["published_at"], default=None)


def _zip_url(release, version):
    for a in release.get("assets", []):
        if a.get("name") == f"MacCleaner-v{version}-macos-universal.zip":
            return a.get("browser_download_url")
    return None


# ── Orchestration ────────────────────────────────────────────────────────────

def verify(version, repo, tap, token, workdir):
    results = []
    tag = f"v{version}"
    release, err, status = fetch_json(f"/releases/tags/{tag}", repo, token)
    if release is None:
        return [check_release_lookup(tag, status, err)]
    if release.get("draft"):
        results.append(Result("Release", FAIL, f"{tag} is still a draft"))
    else:
        kind = "prerelease" if release.get("prerelease") else "release"
        results.append(Result("Release", PASS, f"{tag} published {release.get('published_at')} as a {kind}"))

    latest, latest_err, _ = fetch_json("/releases/latest", repo, token)
    is_latest = None if latest is None else latest.get("tag_name") == tag

    assets = {a["name"]: a for a in release.get("assets", [])}
    results.append(check_assets(version, assets))
    zip_name, cli_name, appcast_name = asset_names(version)

    zip_data = cli_data = appcast_text = None
    if zip_name in assets:
        zip_data, err, _ = fetch(assets[zip_name]["browser_download_url"])
        if zip_data is None:
            results.append(Result("ZIP download", UNVERIFIED, err))
    if cli_name in assets:
        cli_data, err, _ = fetch(assets[cli_name]["browser_download_url"])
        results.append(check_cli_tarball(cli_data, version) if cli_data is not None
                       else Result("CLI tarball", UNVERIFIED, err))
    if appcast_name in assets:
        raw, err, _ = fetch(assets[appcast_name]["browser_download_url"])
        appcast_text = _text(raw)
        if raw is None:
            results.append(Result("Appcast", UNVERIFIED, err))

    if zip_data is None:
        return results  # every remaining check reads the ZIP

    zip_sha = sha256_hex(zip_data)
    results.append(Result("ZIP sha256", PASS, f"{zip_sha} ({len(zip_data):,} bytes)"))

    sig = None  # a missing or unfetchable appcast was reported above
    if appcast_text is not None:
        appcast_result, sig = check_appcast(appcast_text, version, len(zip_data))
        results.append(appcast_result)

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        plist_raw = _read_zip_member(zf, APP_PLIST)
        info = plistlib.loads(plist_raw) if plist_raw else {}
        engine_raw = _read_zip_member(zf, APP_ENGINE)
        has_sparkle = any(n.startswith(APP_SPARKLE) for n in zf.namelist())
    results.append(check_bundle(info, _text(engine_raw), version, has_sparkle))
    key = info.get("SUPublicEDKey")
    results.append(check_signature(key, sig, zip_data))

    prev_version = prev_key = None
    prev_readable = False
    listing, _, _ = fetch_json("/releases?per_page=50", repo, token)
    prev = previous_release(listing, release) if listing is not None else None
    if listing is None:
        results.append(Result("Key continuity", UNVERIFIED, "could not list earlier releases"))
    else:
        if prev is not None:
            prev_version = prev["tag_name"].lstrip("v")
            url = _zip_url(prev, prev_version)
            prev_zip = fetch(url)[0] if url else None
            if prev_zip is not None:
                with zipfile.ZipFile(io.BytesIO(prev_zip)) as zf:
                    raw = _read_zip_member(zf, APP_PLIST)
                if raw:
                    prev_readable = True
                    prev_key = plistlib.loads(raw).get("SUPublicEDKey")
        results.append(check_key_continuity(key, prev_key, prev_version, prev_readable))

    # Code signing: ditto preserves the symlinks and modes inside
    # Sparkle.framework that Python's zipfile would flatten, and a bundle
    # extracted without them fails --strict for reasons unrelated to the release.
    zip_path = Path(workdir) / zip_name
    zip_path.write_bytes(zip_data)
    out_dir = Path(workdir) / "app"
    rc, out = run_readonly(["ditto", "-x", "-k", str(zip_path), str(out_dir)])
    app = out_dir / "MacCleaner.app"
    if rc != 0 or not app.is_dir():
        reason = out if rc is None else f"could not extract the ZIP: {out.strip()}"
        for n in ("Code signature", "Notarization ticket", "Gatekeeper"):
            results.append(Result(n, UNVERIFIED, reason))
    else:
        results.append(parse_codesign(*run_readonly(["codesign", "--verify", "--deep", "--strict", str(app)])))
        results.append(parse_stapler(*run_readonly(["xcrun", "stapler", "validate", str(app)])))
        results.append(parse_spctl(*run_readonly(["spctl", "-a", "-vv", str(app)])))

    if is_latest is None:
        for n in ("Stable appcast feed", "Repo cask", "Tap cask"):
            results.append(Result(n, UNVERIFIED, f"could not tell whether {tag} is the latest release "
                                                 f"({latest_err})"))
    else:
        feed = fetch(f"https://github.com/{repo}/releases/latest/download/appcast.xml")[0]
        results.append(check_stable_feed(_text(feed), version, is_latest))
        for label, slug in (("Repo", repo), ("Tap", tap)):
            raw = fetch(f"https://raw.githubusercontent.com/{slug}/main/Casks/maccleaner.rb")[0]
            results.append(check_cask(label, _text(raw), version, zip_sha, is_latest))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only verification of a published MacCleaner release.")
    ap.add_argument("version", nargs="?", help="e.g. 2.21.1 or v2.21.1 (default: the latest release)")
    ap.add_argument("--repo", default="Fullex26/MacCleaner")
    ap.add_argument("--tap", default="Fullex26/homebrew-tap")
    args = ap.parse_args(argv)
    token = github_token()

    version = (args.version or "").lstrip("v")
    if not version:
        latest, err, _ = fetch_json("/releases/latest", args.repo, token)
        if latest is None:
            print(f"Could not determine the latest release: {err}", file=sys.stderr)
            return 3
        version = latest["tag_name"].lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", version):
        ap.error(f"not a release version: {args.version!r}")

    print(f"Verifying MacCleaner v{version} ({args.repo})", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="mc-verify-release-") as workdir:
        results = verify(version, args.repo, args.tap, token, workdir)
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status:<10}  {r.name:<{width}}  {r.detail}")
    code = exit_code(results)
    counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, UNVERIFIED, SKIPPED)}
    verdict = {0: "verified", 1: "DEFECTIVE", 3: "INCONCLUSIVE"}[code]
    sys.stdout.flush()  # keep the verdict below the table it summarises
    print(f"\nv{version}: {verdict} — " + ", ".join(f"{n} {s.lower()}" for s, n in counts.items() if n),
          file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
