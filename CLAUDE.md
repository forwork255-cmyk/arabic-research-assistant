# Arabic Research Assistant

## My situation

I am a complete beginner in programming and have almost no coding experience.
I am using Claude Code to help me build this project. I want to understand the important fundamentals while using AI to do much of the implementation.

## Project goal

Build an Arabic-first academic research assistant. Originally scoped as a small, affordable MVP; the goal has since expanded to a full-featured AI assistant (not just the light MVP) -- see `ROADMAP.md` for the phase-by-phase plan and current progress. That file is the source of truth for "what phase are we on" -- this file should not duplicate that tracking, only the durable project rules/architecture.

A user should eventually be able to:

1. Ask an academic research question in Arabic.
2. Search for relevant scholarly literature in Arabic and English.
3. Find and compare relevant studies.
4. Summarize evidence in Arabic.
5. Identify agreement, disagreement, limitations, and possible research gaps.
6. Produce properly sourced references.

## Critical rules

- Never invent papers, authors, journals, DOI numbers, quotations, statistics, or findings.
- Important claims must be traceable to real sources.
- Clearly separate retrieved evidence from AI interpretation.
- Prefer reliable scholarly sources.
- Keep the system as simple and inexpensive as possible.
- Build the smallest useful MVP before adding advanced features.
- Avoid unnecessary dependencies and complexity.
- Do not build features just because they sound impressive.
- Assume I have a very limited budget.

## Development style

- Explain important decisions in beginner-friendly language.
- Before major changes, tell me what you plan to change.
- Make one meaningful feature at a time.
- Test changes before declaring them finished.
- When something fails, find the root cause instead of repeatedly patching symptoms.
- Tell me when I am overengineering the project.

## Current status

Deployed and live (Streamlit Community Cloud), with real user accounts, persistent per-account history, and a conversational chat UI -- not just an MVP script anymore. Current architecture:

- `openalex_search.py` — OpenAlex API access and paper metadata/abstract reconstruction.
- `search_pipeline.py` — multi-query retrieval, merging, deduplication, and deterministic retrieval statistics.
- `.claude/skills/research-search-workflow/SKILL.md` — generates bounded English and Arabic search queries from an Arabic academic question.
- `relevance_filter.py` — deterministic selection/reporting around model-provided relevance classifications.
- `synthesis.py` — builds the restricted synthesis input packet and validates the model's structured synthesis output.
- `pipeline_runner.py` — orchestrates the full pipeline (query gen → retrieval → relevance → selection → extraction → synthesis) plus expand/follow-up/research-follow-up flows.
- `run_assistant.py` — the only file supplying real model calls (via `model_client.py`), one function per pipeline stage, each logging token usage.
- `moderation.py` — safety check on user questions before they reach the real pipeline.
- `db.py` — shared Firestore connection.
- `auth.py` — accounts (bcrypt password hashing) and manually-granted subscriptions (pay-the-owner-directly model).
- `history.py` — persistent per-account search history.
- `global_limit.py` — site-wide emergency cap on total real searches, protecting the API budget regardless of account count.
- `app.py` — the Streamlit UI, chat-based (`st.chat_message`/`st.chat_input`), no pipeline/prompt/model logic of its own.

The model-dependent tasks are: moderation, query generation, relevance classification, evidence extraction, evidence synthesis, and follow-up Q&A. Deterministic Python handles API retrieval, deduplication, selection rules, input assembly, and output validation for all of them.

## Established product principles

- The system must never fabricate academic sources or research findings.
- Evidence and AI interpretation must be clearly separated.
- Failure to retrieve evidence must never be presented as proof that literature does not exist.
- Weak evidence should be reported honestly rather than forced into a confident answer.
- Keep real per-search API cost as low as reasonably possible; always estimate cost before a live test.
- Authentication, database, and manual (non-gateway) payments now exist -- see `ROADMAP.md` for what's built vs. planned. Still no automated payment gateway and no mobile app.
