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
- [x] Legal docs draft — [PRIVACY_POLICY.md](PRIVACY_POLICY.md) / [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) written; both explicitly marked DRAFT, several `[يُحدَّد]` placeholders (jurisdiction, contact email, company name) need a real lawyer before publishing; shown + agreement-gated at sign-up
- [x] Manual subscriptions (pay-the-owner-directly model) — `auth.grant_subscription()`, owner-only sidebar panel gated by `OWNER_EMAIL` secret. Bounded to `SUBSCRIPTION_SEARCH_LIMIT = 200`/period (like a real paid plan, not literal-unlimited) so one subscriber can't exhaust the site-wide cap alone. Owner's own account is genuinely unlimited (`is_owner()`).
- [ ] Payments (Stripe) — **abandoned as the near-term path**: Stripe does not support Iraq-based sellers/payout recipients (confirmed via research), and neither do the main alternatives (Dodo Payments, Lemon Squeezy). Real workaround exists (**Stripe Atlas** — form a US LLC, ~$650–900 first year + ~$250–500/year ongoing, eligible since Iraq isn't on Atlas's sanctions-exclusion list) but is parked for later/international expansion, not needed now.
- [ ] **Automated Iraqi payment gateway** (new item, replaces the Stripe item as the realistic next payment step) — researched, not implemented. Two real, working options found: **ZainCash** (Iraq's most-used mobile wallet, documented REST API + JWT, sandbox available, ~1.15% fee) and **First Iraqi Bank (FIB)** (licensed bank, official `fib-python-payment-sdk` on GitHub, accepts local+international cards, 1–5% fee negotiated). Recommended: ZainCash first (matches actual target users). Manual subscriptions (above) are the bridge until this is built.
- [ ] Tiered plans with different models (Normal/Pro/Max, discussed not started) — different pipeline stages could use Sonnet vs Opus per plan tier; needs (a) verifying Opus is actually better for this task before promising it, (b) pricing that covers Opus's real higher per-token cost.

## Known open issues (not on the original roadmap, found during testing)

- [ ] Intermittent `PipelineError` on some questions (seen live once, cause not yet diagnosed — no server log access to see the exact message)
- [ ] Streamlit Cloud sometimes serves a stale/partially-reloaded module after a push (hit twice now: once with `FIREBASE_KEY`, once with `auth.is_subscribed`) — fix is always a full Reboot from the app's ⋮ menu, not just a save/refresh

## Pricing research (done, not yet applied to real prices)

- Exchange rate at research time: $1 ≈ 1,310 IQD
- Real per-search cost estimate: ~80 IQD (~$0.06), based on actual session spend -- refine with more real data before finalizing prices
- Reality-check anchor: Spotify Premium in Iraq = 4,900 IQD/month -- pricing should feel small next to that
- Original credit-pack proposal (Basic 20/Standard 60/Pro 150 searches at ~50% margin) was superseded by the manual-subscription (time-based, not count-based) model above -- would need re-deriving if credit packs come back

## Working agreement

- Confirm model + effort before starting each item (established pattern)
- Test locally/free first, then real live test only with explicit go-ahead, cost-estimated first
- One meaningful feature at a time, test after every step
- Commit locally, user pushes, then live-verify on the deployed app
