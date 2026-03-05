# Security Policy

## Supported Versions

MacCleaner is currently in early release. Security fixes are applied to the latest version only.

| Version | Supported |
|---------|-----------|
| latest  | ✅ |
| older   | ❌ |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability, report it privately via GitHub's Security Advisory feature:

1. Go to https://github.com/Fullex26/MacCleaner/security/advisories
2. Click **"Report a vulnerability"**
3. Fill in the details — what the issue is, how to reproduce it, and its potential impact

You can expect an acknowledgement within **72 hours** and a status update within **7 days**.
If the issue is confirmed, a fix will be prepared and released as soon as practical, with credit to
the reporter in the release notes (unless you prefer to remain anonymous).

## Scope

Vulnerabilities worth reporting include:

- Unintended file deletion outside of expected target paths
- Path traversal via config values (`skip_paths`, custom paths) leading to unintended deletions
- Command injection via config values passed to subprocess calls (docker prune, pnpm store prune, etc.)
- Privilege escalation via the install script
- `auto_approve` or `--yes` deleting items marked `safe=False` without user confirmation

Out of scope:

- Files deleted that were explicitly configured as targets by the user
- Issues requiring the attacker to have already modified `config.json`
- Vulnerabilities in third-party tools MacCleaner invokes (Docker, pnpm, etc.)

## Security Design Notes

- MacCleaner runs as the current user — it does not use `sudo` or elevate privileges
- `safe=False` targets always prompt for confirmation, even with `--yes` / `auto_approve: true`
- Command-based targets (docker prune, etc.) run via `subprocess.run(shell=True)` with values sourced from config — avoid putting untrusted values in `config.json`
