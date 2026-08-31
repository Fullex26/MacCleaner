# Mac App Store Feasibility

**Status: assessed, blocked on product decisions — not on engineering effort.**
Covers the roadmap items "Sandboxed App Store build" and "Mac App Store
release".

## The core conflict

The App Store sandbox denies exactly what MacCleaner does. A sandboxed app
cannot read or delete other apps' data under `~/Library/Caches`, `~/Library/
Developer`, `/private/tmp`, or anything outside its container without a
user-granted security-scoped bookmark per directory. There is no entitlement
for "clean other apps' caches" — the tools on the MAS in this space ship
severely reduced variants and funnel users to a notarized direct download for
full function.

## What a MAS variant could honestly be

- **Storage X-ray (read-only):** `storage-map` / `storage-insights` behind
  user-granted folder access — genuinely useful, sandbox-compatible.
- **Reveal + advise:** find reclaimable space, deep-link the user to Finder /
  the vendor's own cleanup, delete nothing.
- Full cleaning stays in the direct-download build (already signed,
  notarized, Sparkle-updated — distribution trust is solved without MAS).

## Prerequisites (decisions, not code)

1. Swift engine (see `V3-SWIFT-ENGINE.md`) — MAS cannot ship the Python
   engine subprocess model as-is.
2. A product decision that a read-only MAS variant is worth maintaining as a
   second SKU (App Review, screenshots, a second update channel).
3. Apple Developer account actions (App Store Connect record, review
   submission) — owner-level, outward-facing.

## Recommendation

Keep the checkbox open. Revisit after V3 stage 3, when a Swift read-only core
exists anyway; the MAS variant then becomes a packaging exercise around
`storage-map` rather than a fork of the cleaner.
