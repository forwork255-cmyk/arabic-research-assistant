# Full AI Assistant Roadmap

Tracks the plan to grow this from a light MVP into a full-featured AI assistant, per the decision to "fully commit" rather than ship the light version.

**Why this file exists:** conversation history gets compacted/summarized over long sessions, which can lose fine-grained details (this file itself exists because the original full 14-item list got lost that way once already). This file does NOT get affected by compaction — it's read fresh each time. Check it at the start of any new roadmap step.

## Note on completeness

The original plan had **14 items across 4 phases**. Only **9 are recorded below** (6 done, 3 identified but not started) — the other 5 were lost when conversation history was compacted before this file existed, and are not reconstructed here to avoid inventing items that weren't actually part of the original plan. Before starting Phase 3/4 work, it's worth a short session to re-derive what else belongs on this list.

## Phase 1 — Foundation (done)

- [x] Real database (Firestore) — replaced in-memory usage tracking
- [x] Real user accounts — email/password sign-up & login (bcrypt), replaced shared access codes
- [x] Persistent per-account search history — follows the account across devices/sessions

## Phase 2 — Core UX & safety (done)

- [x] Conversational chat redesign — native chat bubbles/input instead of a static report form
- [x] Site-wide safety cap — emergency stop on total real searches (protects the API budget)
- [x] Content moderation — blocks harmful/off-topic/jailbreak questions before they reach the real pipeline

## Phase 3/4 — Identified

- [x] Error monitoring (Sentry) — captures unhandled + previously-silently-caught exceptions, verified live
- [x] Legal docs draft — [PRIVACY_POLICY.md](PRIVACY_POLICY.md) / [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) written; both explicitly marked DRAFT, several `[يُحدَّد]` placeholders (jurisdiction, contact email, company name) need a real lawyer before publishing
- [ ] Payments (Stripe) — Model: Sonnet | Effort: High (real money + security stakes). Needs the user to create the Stripe account and make pricing decisions before implementation starts.

## Known open issues (not on the original roadmap, found during testing)

- [ ] Intermittent `PipelineError` on some questions (seen live once, cause not yet diagnosed — no server log access to see the exact message)

## Working agreement

- Confirm model + effort before starting each item (established pattern)
- Test locally/free first, then real live test only with explicit go-ahead, cost-estimated first
- One meaningful feature at a time, test after every step
- Commit locally, user pushes, then live-verify on the deployed app
