# Full AI Assistant Roadmap

Tracks the plan to grow this from a light MVP into a full-featured AI assistant, per the decision to "fully commit" rather than ship the light version.

**Why this file exists:** conversation history gets compacted/summarized over long sessions, which can lose fine-grained details (this file itself exists because the original full 14-item list got lost that way once already). This file does NOT get affected by compaction — it's read fresh each time. Check it at the start of any new roadmap step.

## Pick up here (updated 2026-09-05)

Waiting on: Wayl (jisr@wayl.io) to reply with an API token -- emailed 2026-09-05, asked whether a solo/unregistered seller can onboard and whether the card fee (2.5%+600 IQD) also applies to ZainCash-wallet payments. No integration code should be written until that reply arrives (or the user says to proceed on assumptions).

**Open decision, waiting on the user: what should `auth.SUBSCRIPTION_SEARCH_LIMITS` actually be?** Items 3+4 below are done and revealed the flat-100-for-every-plan cap (set earlier for marketing) is financially risky at real cost. Real per-search cost, measured from the actual Normal vs Max comparison test (2026-09-05), using CURRENT pricing (Sonnet 5 $3/$15, Opus 5 $5/$25, Haiku 4.5 $1/$5 per million tokens -- looked up fresh, not assumed): **Normal ≈ 230 IQD/search, Max ≈ 320 IQD/search** (Opus 5 is only ~1.4x Sonnet 5 now, much closer than the old "Opus is way pricier" assumption). This replaces the earlier ~80 IQD/search estimate, which was measured before the full-length-output change and is now known to be ~3x too low.

Research done to inform the cap/price decision (2026-09-05, user-requested, high effort):
- **Average usage**: most AI-assistant users are not daily power users (only 41% of ChatGPT's MAU use it 3+×/week). The closest real comparable to this app's cost profile, Perplexity's "Deep Research" mode, caps even paying $20/mo subscribers at only **~20/month**.
- **Worst-case/power-user %**: no AI-specific published number exists (Perplexity didn't disclose one when they cut their own limits), but the general SaaS/freemium pattern is a Pareto split -- **roughly the top 20% of users drive the large majority of usage**. Design assumption: ~1-in-5 subscribers will be a genuine heavy user regularly approaching the cap; the rest use a fraction of it.
- **Iraqi market pricing (direct comparable found)**: resold/shared ChatGPT Plus seats are actively sold on Iraqi Instagram right now for **7,000 IQD/month** -- same product category, same social-media-resale channel the user described from personal experience. Combined with Telegram Premium (~$5 ≈ 6,550 IQD) and the earlier Spotify anchor (4,900 IQD), the accepted range for a paid digital subscription in Iraq clusters around **5,000-7,000 IQD/month**. Official ChatGPT Plus ($20 ≈ 26,000 IQD) is far outside what the local market actually pays directly.
- **Cap options costed out** (at 230 IQD/search real normal-tier cost): 100 (current) = 23,000 IQD worst-case, badly underwater vs. a 6-7k price; 40 = 9,200 IQD, underwater if fully used; 30 ≈ 6,900 IQD, roughly breakeven at full use; 25 ≈ 5,750 IQD, breakeven with room, matches the Perplexity Deep Research benchmark almost exactly.
- **Not yet decided**: final cap number and final price per plan. User is deciding this themselves, no rush -- do not change `auth.SUBSCRIPTION_SEARCH_LIMITS` or the placeholder prices again until they say what they picked.

Other good zero/low-cost things to do in the meantime:
1. **Live-verify paper follow-up questions** (small real cost -- re-sends the PDF) — upload a PDF, get the analysis, ask one follow-up, confirm a second follow-up box appears after. Committed but intentionally deferred by user, not urgent.

Once Wayl replies: build the checkout-link + webhook integration (see the Wayl item under Phase 3/4 below for what's already known), test in sandbox mode first, then go live.

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
- [x] Manual subscriptions (pay-the-owner-directly model) — `auth.grant_subscription()`, owner-only sidebar panel gated by `OWNER_EMAIL` secret. Bounded to `auth.SUBSCRIPTION_SEARCH_LIMITS` (flat 100/period for every plan -- a marketing call: a smaller "max" cap read as less appealing than "normal" even though max costs more per search; most subscribers won't use the full allowance, and `global_limit.py`'s site-wide cap is the backstop if that assumption is wrong) so one subscriber can't exhaust the site-wide cap alone. Owner's own account is genuinely unlimited (`is_owner()`).
- [ ] **Automated payment via Wayl** (new item, replaces the ZainCash/FIB item below now that a real unified provider was found) — researched: [Wayl](https://wayl.io) is a Baghdad-based payment facilitator, built for small/solo merchants (no company registration required per their own marketing, "light KYC"), backed by a real Visa MoU and a MoneyHash orchestration partnership for Iraq. Bundles ZainCash + QiCard + FIB + Visa/Mastercard behind one hosted-checkout API (customer picks their method on Wayl's own page, no card data touches this app). Real fees confirmed from their pricing page: local cards 2.5%+600 IQD, international cards 3.5%+600 IQD per transaction; payout to FIB free, to ZainCash 0.3%+5,000 IQD, to other banks 0.5%+25,000 IQD. Placeholder prices set (revisable anytime, not final): normal 6,000 / pro 9,000 / max 12,000 IQD per month. API token requested via email to `jisr@wayl.io` -- waiting on their reply before any integration code is written. Manual subscriptions (above) remain the bridge until this is live.
- [ ] Payments (Stripe) — **abandoned as the near-term path**: Stripe does not support Iraq-based sellers/payout recipients (confirmed via research), and neither do the main alternatives (Dodo Payments, Lemon Squeezy). Real workaround exists (**Stripe Atlas** — form a US LLC, ~$650–900 first year + ~$250–500/year ongoing, eligible since Iraq isn't on Atlas's sanctions-exclusion list) but is parked for later/international expansion, not needed now.
- ~~Automated Iraqi payment gateway (direct ZainCash or FIB integration)~~ — superseded by the Wayl item above: a unified provider covering ZainCash + QiCard + FIB + cards in one integration beat building three separate direct integrations.
- [x] Tiered plans with different models (Normal/Pro/Max) — mechanism implemented: `auth.py` accounts now carry a `plan` field (`auth.PLANS = ("normal", "pro", "max")`), `run_assistant.PLAN_MODELS` maps each tier to real models (normal: Sonnet/Sonnet, pro: Sonnet/Opus, max: Opus/Opus, for relevance classification/synthesis), owner's grant panel has a plan selector, `app.py`'s main search picks `make_relevance_classifier(plan)`/`make_synthesizer(plan)` accordingly. Scope: only the main search respects plan tier so far -- expand/follow-up/research-follow-up still always use the normal models. Tested: 6/6 logic tests + live UI confirmation. **Real Normal vs Max comparison test completed 2026-09-05**: same real question through both tiers -- max read all 5 selected sources vs. normal's fewer, and user's own note was "good length, better than normal, read all sources." Opus tier does produce a real, noticeable quality improvement, not just a more expensive label. (This same test is also where the real per-search cost data below came from.)
- [x] **Single-paper upload + analysis** — new capability, not on the original 9-item list: user attaches a PDF via the chat input (native Claude document content block, not our own text extraction), gets either a structured summary or a grounded answer to a specific question about that one paper. New files: `paper_analysis.py` (prompt + `MAX_PDF_BYTES = 15MB` guard), `model_client.call_model_with_document()`, `run_assistant.analyze_paper()`. Different from the existing multi-paper OpenAlex-abstract-search flow. `CLAUDE.md` previously excluded "PDF processing"; superseded by the full-assistant pivot. Verified live with a real PDF (real cost). One real bug found and fixed live: moderation was judging a short caption like "لخص" against the "is this a freestanding research question" bar and wrongly rejecting it -- fixed with a paper-aware moderation prompt (`moderation.format_paper_question_moderation_prompt`).
- [~] **Paper follow-up questions** — user reported no way to ask more questions after a paper upload (the loop that search results already have was missing for papers). Added `paper_analysis.format_paper_followup_prompt()`, `app.py`'s `handle_paper_followup_input()`/`render_paper_followup_thread()`. PDF is cached in `st.session_state` only (not Firestore -- 15MB file would exceed its 1MB document limit), so follow-ups only work while the file is still cached from the original upload in that session; a reload shows a clear "re-upload to ask more" message instead of crashing. Each follow-up re-sends the full PDF, so it costs the same as a fresh paper analysis. Committed (`b26b288`), code done but **live-verification intentionally deferred by user** (2026-09-05) -- not urgent right now, revisit later.
- [x] **Delete / star search history** — sidebar entries had no way to remove or pin a past search. Added `history.delete_entry()`/`history.set_starred()`, sidebar now shows a star toggle (pins to a "المفضلة" section at the top) and a delete button (two-click confirm, permanent). No model cost -- pure Firestore + UI. Committed (`0b0bf3f`), **live-verified working** (2026-09-05).

## Output quality

- [x] Full-length output (not demo MVP terseness) — per-paper findings 80-100 → 150-200 Arabic words, ai_synthesis 200-220 → 450-600 words (multi-paragraph), disagreements/limitations given more explanatory room. `EXTRACTION_MAX_TOKENS` 1000 → 2000, `FINAL_SYNTHESIS_MAX_TOKENS` 2600 → 6000. **Live-verified 2026-09-05** with two real searches (normal + max tier) -- no truncation, both completed cleanly after fixing the two bugs this surfaced (synthesis validator caps out of sync with the prompt, and relevance classification's missing structured-output enforcement -- see Known open issues below).

## Known open issues (not on the original roadmap, found during testing)

- [x] Intermittent `PipelineError` on some questions — root-caused via Sentry: OpenAlex transient failures (429/5xx/timeout) had no retry and the real per-query errors were discarded from the exception message. Fixed in `openalex_search.fetch_results()` (one retry) and `pipeline_runner.py` (error message now surfaces the real cause). See commit `19b0ff8`.
- [ ] Streamlit Cloud sometimes serves a stale/partially-reloaded module after a push (hit three times now: `FIREBASE_KEY`, `auth.is_subscribed`, `run_assistant.make_relevance_classifier`) — fix is always a full Reboot from the app's ⋮ menu, not just a save/refresh. Consider whether this is worth a standing habit: always Reboot after every push, not just when something breaks.
- [x] `PipelineError` at the extraction stage after the full-length-output change — the prompt (`pipeline_runner.py`) was raised to allow up to 3 disagreements / 4 limitations, but `synthesis.py`'s validator caps were left at the old 2/3, so a correct, real model response hit "exceeding the maximum" and failed. Fixed by syncing the validator constants to the prompt's stated limits. See commit `f8537c9`. **General lesson: whenever a prompt's stated limit changes, the matching validator constant must change with it in the same edit** -- these two files drifting apart is exactly this bug's root cause, and it's an easy mistake to repeat.
- [x] `PipelineError` at relevance classification: "Item 2: unexpected field(s) not allowed: ['title']" — found live right after the fix above, on retry. Root cause: relevance classification was the one AI-boundary stage still using plain-text "please return exactly these fields" prompting instead of Anthropic's native JSON Schema structured output (which moderation/extraction/synthesis already used) -- so nothing structurally stopped the model from occasionally adding an extra field, which the validator then correctly rejected. Fixed by migrating it to the same native-schema pattern (`relevance_filter.RELEVANCE_JSON_SCHEMA`, `additionalProperties: false`). See commit `b889c6a`.

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
